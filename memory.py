import json
import sqlite3

from db import get_db

EVIDENCE_TIER_WEIGHTS = {"E5": 1.0, "E4": 0.9, "E3": 0.75, "E2": 0.55, "E1": 0.35}
RESEARCH_TRIGGER_THRESHOLD = 0.65


def retrieve_facts(question: str, limit: int = 20) -> list[dict]:
    """FTS5 keyword retrieval. No LLM. Sub-100ms."""
    db = get_db()
    try:
        stopwords = {"is", "our", "the", "a", "an", "what", "can", "in", "i", "we", "this", "that", "of", "to", "and", "or", "for", "with", "on", "at", "my"}
        words = [w for w in question.lower().split() if w.isalpha() and w not in stopwords]
        if not words:
            return []
        fts_query = " OR ".join(words)
        rows = db.execute(
            """SELECT f.*, s.title as source_title, s.source_type, s.document_date as source_date
               FROM facts f
               JOIN sources s ON f.source_id = s.id
               JOIN facts_fts fts ON f.rowid = fts.rowid
               WHERE facts_fts MATCH ? AND f.superseded_by IS NULL
               ORDER BY f.confidence DESC, f.evidence_tier DESC
               LIMIT ?""",
            (fts_query, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        words = question.lower().split()[:3]
        results = []
        for word in words:
            rows = db.execute(
                """SELECT f.*, s.title as source_title, s.source_type, s.document_date as source_date
                   FROM facts f JOIN sources s ON f.source_id = s.id
                   WHERE LOWER(f.claim) LIKE ? OR LOWER(f.verbatim_quote) LIKE ?
                   ORDER BY f.confidence DESC LIMIT ?""",
                (f"%{word}%", f"%{word}%", limit)
            ).fetchall()
            results.extend([dict(r) for r in rows if dict(r) not in results])
        return results[:limit]
    finally:
        db.close()


def retrieve_facts_by_ids(fact_ids: list[str]) -> list[dict]:
    db = get_db()
    try:
        if not fact_ids:
            return []
        placeholders = ",".join("?" * len(fact_ids))
        rows = db.execute(
            f"""SELECT f.*, s.title as source_title, s.source_type, s.document_date as source_date
                FROM facts f JOIN sources s ON f.source_id = s.id
                WHERE f.id IN ({placeholders})""",
            fact_ids
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_contradictions_for_facts(fact_ids: list[str]) -> list[dict]:
    if not fact_ids:
        return []
    db = get_db()
    try:
        placeholders = ",".join("?" * len(fact_ids))
        rows = db.execute(
            f"""SELECT c.*,
                   fa.claim as fact_a_claim, fa.verbatim_quote as fact_a_quote,
                   fb.claim as fact_b_claim, fb.verbatim_quote as fact_b_quote
                FROM contradictions c
                JOIN facts fa ON c.fact_a_id = fa.id
                JOIN facts fb ON c.fact_b_id = fb.id
                WHERE c.fact_a_id IN ({placeholders}) OR c.fact_b_id IN ({placeholders})""",
            fact_ids + fact_ids
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def compute_confidence(facts: list[dict], contradictions: list[dict]) -> float:
    """Weighted average over evidence tiers, then penalise for contradictions."""
    if not facts:
        return 0.0
    total_weight = sum(EVIDENCE_TIER_WEIGHTS.get(f.get("evidence_tier", "E1"), 0.35) for f in facts)
    base = total_weight / len(facts)
    for c in contradictions:
        if c["severity"] == "high":
            base *= 0.70
        elif c["severity"] == "medium":
            base *= 0.85
    return round(min(max(base, 0.10), 0.95), 3)


def should_research(confidence: float, contradictions: list[dict]) -> bool:
    has_high = any(c["severity"] == "high" for c in contradictions)
    return confidence < RESEARCH_TRIGGER_THRESHOLD or has_high


def get_all_contradictions() -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            """SELECT c.*,
                   fa.claim as fact_a_claim, fa.verbatim_quote as fact_a_quote,
                   fa.numeric_value as fact_a_value, fa.numeric_unit as fact_a_unit,
                   sa.title as source_a_title,
                   fb.claim as fact_b_claim, fb.verbatim_quote as fact_b_quote,
                   fb.numeric_value as fact_b_value, fb.numeric_unit as fact_b_unit,
                   sb.title as source_b_title
                FROM contradictions c
                JOIN facts fa ON c.fact_a_id = fa.id JOIN sources sa ON fa.source_id = sa.id
                JOIN facts fb ON c.fact_b_id = fb.id JOIN sources sb ON fb.source_id = sb.id
                ORDER BY c.detected_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_all_facts(fact_type: str | None = None, query: str | None = None,
                  limit: int = 50, include_contested: bool = True) -> list[dict]:
    db = get_db()
    try:
        sql = """SELECT f.*, s.title as source_title, s.document_date as source_date
                 FROM facts f JOIN sources s ON f.source_id = s.id WHERE 1=1"""
        params: list = []
        if fact_type:
            sql += " AND f.fact_type = ?"
            params.append(fact_type)
        if not include_contested:
            sql += " AND f.contested = 0"
        if query:
            sql += " AND (LOWER(f.claim) LIKE ? OR LOWER(f.verbatim_quote) LIKE ?)"
            params.extend([f"%{query.lower()}%", f"%{query.lower()}%"])
        sql += " ORDER BY f.learned_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in db.execute(sql, params).fetchall()]
    finally:
        db.close()


def get_decision(decision_id: str) -> dict | None:
    db = get_db()
    try:
        row = db.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def get_all_decisions() -> list[dict]:
    db = get_db()
    try:
        return [dict(r) for r in db.execute(
            "SELECT * FROM decisions ORDER BY created_at DESC"
        ).fetchall()]
    finally:
        db.close()
