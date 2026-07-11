import anthropic
import hashlib
import json
import os

from db import get_db, content_hash, now_iso
from entities import link_entities_to_fact
from models import ExtractedFact, ExtractionResult, IngestRequest
from prompts import EXTRACTION_SYSTEM_PROMPT

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def claude_extract(source_type: str, title: str, content: str, document_date: str | None) -> ExtractionResult:
    """SEAM 1: the only LLM call in the write path."""
    user_message = f"""Document type: {source_type}
Title: {title}
Date: {document_date or 'unknown'}

---
{content}
---"""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        temperature=0,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    data = json.loads(raw)
    return ExtractionResult(**data)


def detect_contradictions(new_fact_id: str, ef: ExtractedFact, db, source_title: str) -> list[dict]:
    """Deterministic contradiction detection. No LLM. Called at every fact insert."""
    if ef.numeric_value is None:
        return []
    contradictions = []
    existing = db.execute(
        """SELECT f.id, f.fact_type, f.numeric_value, f.numeric_unit, s.title as source_title
           FROM facts f JOIN sources s ON f.source_id = s.id
           WHERE f.fact_type = ? AND f.id != ? AND f.superseded_by IS NULL
             AND f.numeric_value IS NOT NULL""",
        (ef.fact_type, new_fact_id)
    ).fetchall()
    for row in existing:
        if row["numeric_unit"] != ef.numeric_unit:
            continue
        diff = abs(ef.numeric_value - row["numeric_value"]) / max(abs(row["numeric_value"]), 0.001)
        if diff <= 0.10:
            continue
        severity = "high" if diff > 0.30 else "medium"
        c_id = hashlib.sha256(f"{row['id']}{new_fact_id}".encode()).hexdigest()[:16]
        desc = (f"{ef.fact_type}: {row['numeric_value']} {row['numeric_unit']} "
                f"({row['source_title']}) vs {ef.numeric_value} {ef.numeric_unit} ({source_title})")
        db.execute(
            """INSERT OR IGNORE INTO contradictions
               (id, fact_a_id, fact_b_id, conflict_type, description, severity, detected_at)
               VALUES (?,?,?,?,?,?,?)""",
            (c_id, row["id"], new_fact_id, "numeric_mismatch", desc, severity, now_iso())
        )
        db.execute("UPDATE facts SET contested = 1 WHERE id IN (?,?)", (row["id"], new_fact_id))
        contradictions.append({"id": c_id, "description": desc, "severity": severity})
    return contradictions


def ingest_document(req: IngestRequest) -> dict:
    db = get_db()
    try:
        source_id = content_hash(req.content)
        existing = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if existing:
            return {"source_id": source_id, "already_existed": True,
                    "facts_extracted": 0, "contradictions_detected": 0,
                    "contradictions": [], "fact_ids": []}
        db.execute(
            "INSERT INTO sources (id, title, source_type, content, document_date, ingested_at) VALUES (?,?,?,?,?,?)",
            (source_id, req.title, req.source_type, req.content, req.document_date, now_iso())
        )
        extraction = claude_extract(req.source_type, req.title, req.content, req.document_date)
        tier_override = (req.metadata or {}).get("evidence_tier_override")
        facts_inserted, all_contradictions, fact_ids = 0, [], []
        for ef in extraction.facts:
            fact_id = content_hash(source_id + ef.fact_type + ef.claim)
            if db.execute("SELECT id FROM facts WHERE id = ?", (fact_id,)).fetchone():
                continue
            final_tier = tier_override or ef.evidence_tier
            db.execute(
                """INSERT INTO facts
                   (id, source_id, fact_type, claim, verbatim_quote, evidence_tier,
                    confidence, conditions, entities, numeric_value, numeric_unit,
                    valid_from, learned_at, superseded_by, contested)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,0)""",
                (fact_id, source_id, ef.fact_type, ef.claim, ef.verbatim_quote,
                 final_tier, ef.confidence,
                 json.dumps(ef.conditions) if ef.conditions else None,
                 json.dumps(ef.entities) if ef.entities else None,
                 ef.numeric_value, ef.numeric_unit,
                 req.document_date, now_iso())
            )
            link_entities_to_fact(fact_id, ef.entities or [], db)
            contradictions = detect_contradictions(fact_id, ef, db, req.title)
            all_contradictions.extend(contradictions)
            fact_ids.append(fact_id)
            facts_inserted += 1
        db.commit()
        return {"source_id": source_id, "already_existed": False,
                "facts_extracted": facts_inserted,
                "contradictions_detected": len(all_contradictions),
                "contradictions": all_contradictions,
                "fact_ids": fact_ids}
    finally:
        db.close()
