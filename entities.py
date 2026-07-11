import hashlib
import json
import sqlite3

from db import get_db, now_iso

KNOWN_ENTITIES = [
    {"canonical_name": "Maya Chen",      "entity_type": "person",   "aliases": ["Maya", "@mayachen_loomwork"]},
    {"canonical_name": "Devin Park",     "entity_type": "person",   "aliases": ["Devin"]},
    {"canonical_name": "Priya Nair",     "entity_type": "person",   "aliases": ["Priya"]},
    {"canonical_name": "Jordan Rivera",  "entity_type": "person",   "aliases": ["Jordan"]},
    {"canonical_name": "Sam Vora",       "entity_type": "person",   "aliases": ["Sam"]},
    {"canonical_name": "Loomwork",       "entity_type": "company",  "aliases": ["@loomwork", "loomwork.com"]},
    {"canonical_name": "Northpeak",      "entity_type": "investor", "aliases": ["Northpeak Capital"]},
    {"canonical_name": "Atlas Ventures", "entity_type": "investor", "aliases": ["Atlas"]},
    {"canonical_name": "Acme Freight",   "entity_type": "company",  "aliases": ["Acme"]},
    {"canonical_name": "FreightPilot",   "entity_type": "product",  "aliases": []},
    {"canonical_name": "Hartmann Group", "entity_type": "company",  "aliases": ["Hartmann"]},
    {"canonical_name": "DeltaX Logistics", "entity_type": "company", "aliases": ["DeltaX"]},
]

KNOWN_RELATIONSHIPS = [
    ("Maya Chen",      "founded",        "Loomwork"),
    ("Northpeak",      "invested_in",    "Loomwork"),
    ("Atlas Ventures", "invested_in",    "Loomwork"),
    ("Sam Vora",       "angel_invested", "Loomwork"),
    ("Devin Park",     "works_at",       "Loomwork"),
    ("Priya Nair",     "works_at",       "Loomwork"),
    ("Jordan Rivera",  "works_at",       "Acme Freight"),
    ("Loomwork",       "competes_with",  "FreightPilot"),
]


def _entity_id(canonical_name: str) -> str:
    return hashlib.sha256(canonical_name.strip().lower().encode()).hexdigest()[:12]


def _levenshtein(a: str, b: str) -> int:
    a, b = a.lower(), b.lower()
    if a == b:
        return 0
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            cost = 0 if a[i-1] == b[j-1] else 1
            dp[j] = min(dp[j-1]+1, prev[j]+1, prev[j-1]+cost)
    return dp[n]


def resolve_entity(name: str) -> str | None:
    normalised = name.strip().lower()
    for entity in KNOWN_ENTITIES:
        all_names = [entity["canonical_name"]] + entity["aliases"]
        for known in all_names:
            if normalised == known.lower():
                return entity["canonical_name"]
            if _levenshtein(normalised, known.lower()) <= 1:
                return entity["canonical_name"]
    return None


def seed_entities(db: sqlite3.Connection) -> None:
    for e in KNOWN_ENTITIES:
        eid = _entity_id(e["canonical_name"])
        db.execute(
            "INSERT OR IGNORE INTO entities (id, canonical_name, entity_type, aliases, created_at) VALUES (?,?,?,?,?)",
            (eid, e["canonical_name"], e["entity_type"], json.dumps(e["aliases"]), now_iso())
        )
    for (a, rel, b) in KNOWN_RELATIONSHIPS:
        aid = _entity_id(a)
        bid = _entity_id(b)
        rid = hashlib.sha256(f"{aid}{rel}{bid}".encode()).hexdigest()[:12]
        db.execute(
            "INSERT OR IGNORE INTO entity_relationships (id, entity_a_id, relation_type, entity_b_id, learned_at) VALUES (?,?,?,?,?)",
            (rid, aid, rel, bid, now_iso())
        )
    db.commit()


def link_entities_to_fact(fact_id: str, entity_names: list[str], db: sqlite3.Connection) -> None:
    for name in entity_names:
        canonical = resolve_entity(name)
        if canonical:
            eid = _entity_id(canonical)
            db.execute(
                "INSERT OR IGNORE INTO entity_mentions (fact_id, entity_id) VALUES (?,?)",
                (fact_id, eid)
            )
