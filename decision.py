import anthropic
import json
import os
import uuid

from db import get_db, now_iso
from memory import (retrieve_facts, get_contradictions_for_facts,
                    compute_confidence, should_research, retrieve_facts_by_ids)
from prompts import SYNTHESIS_SYSTEM_PROMPT
from research import research_gaps

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def build_research_queries(question: str, contradictions: list[dict]) -> list[str]:
    queries: list[str] = []
    for c in contradictions:
        if c["severity"] in ("high", "medium"):
            queries.append(f"{c['description']} data 2026")
    if not queries:
        queries.append(f"{question} benchmark data 2026")
    return queries[:3]


def claude_synthesize(question: str, facts: list[dict], contradictions: list[dict]) -> dict:
    """SEAM 2: only LLM call in the decision path."""
    facts_block = json.dumps([{
        "fact_id":        f["id"],
        "fact_type":      f["fact_type"],
        "claim":          f["claim"],
        "verbatim_quote": f["verbatim_quote"],
        "evidence_tier":  f["evidence_tier"],
        "confidence":     f["confidence"],
        "source":         f.get("source_title"),
        "date":           f.get("source_date"),
        "conditions":     json.loads(f["conditions"]) if f.get("conditions") else None,
        "contested":      bool(f.get("contested", 0))
    } for f in facts], indent=2)
    contradictions_block = json.dumps([{
        "id":           c["id"],
        "description":  c["description"],
        "severity":     c["severity"],
        "fact_a_quote": c.get("fact_a_quote"),
        "fact_b_quote": c.get("fact_b_quote")
    } for c in contradictions], indent=2)
    user_message = f"""Question: {question}

Retrieved facts ({len(facts)} total):
{facts_block}

Contradictions detected ({len(contradictions)} total):
{contradictions_block}"""
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2048,
        temperature=0,
        system=SYNTHESIS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def answer_question(question: str) -> dict:
    facts = retrieve_facts(question, limit=20)
    fact_ids = [f["id"] for f in facts]
    contradictions = get_contradictions_for_facts(fact_ids)
    confidence = compute_confidence(facts, contradictions)

    research_fact_ids: list[str] = []
    research_queries: list[str] = []
    if should_research(confidence, contradictions):
        research_queries = build_research_queries(question, contradictions)
        research_fact_ids = research_gaps(research_queries)
        if research_fact_ids:
            research_facts = retrieve_facts_by_ids(research_fact_ids)
            facts = facts + research_facts
            fact_ids = [f["id"] for f in facts]
            contradictions = get_contradictions_for_facts(fact_ids)
            confidence = compute_confidence(facts, contradictions)

    synthesis_json = claude_synthesize(question, facts, contradictions)

    decision_id = str(uuid.uuid4())
    db = get_db()
    try:
        db.execute(
            """INSERT INTO decisions
               (id, question, retrieved_fact_ids, contradictions_found,
                research_triggered, research_queries, research_fact_ids,
                synthesis, confidence, recommended_action, open_gaps,
                key_contradiction, human_decision, decision_note, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?)""",
            (decision_id, question,
             json.dumps(fact_ids),
             json.dumps([c["id"] for c in contradictions]),
             1 if research_fact_ids else 0,
             json.dumps(research_queries) if research_queries else None,
             json.dumps(research_fact_ids) if research_fact_ids else None,
             synthesis_json["synthesis"],
             synthesis_json.get("confidence", confidence),
             synthesis_json["recommended_action"],
             json.dumps(synthesis_json.get("open_gaps", [])),
             synthesis_json.get("key_contradiction"),
             now_iso())
        )
        db.commit()
    finally:
        db.close()

    sources_cited = [{
        "fact_id":        f["id"],
        "source_title":   f.get("source_title"),
        "verbatim_quote": f["verbatim_quote"],
        "document_date":  f.get("source_date"),
        "evidence_tier":  f["evidence_tier"]
    } for f in facts]
    return {
        "decision_id":        decision_id,
        "synthesis":          synthesis_json["synthesis"],
        "confidence":         synthesis_json.get("confidence", confidence),
        "recommended_action": synthesis_json["recommended_action"],
        "open_gaps":          synthesis_json.get("open_gaps", []),
        "key_contradiction":  synthesis_json.get("key_contradiction"),
        "research_triggered": bool(research_fact_ids),
        "research_queries":   research_queries,
        "sources_cited":      sources_cited,
    }
