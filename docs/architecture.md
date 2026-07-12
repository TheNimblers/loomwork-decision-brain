# Architecture — Loomwork Decision Brain
**Builders Studio Challenge · Syed Mujtaba Mahdi · Branch: `build/q3-slice`**

---

## System Overview

A CEO drops a week of raw inputs (board notes, investor updates, transcripts, emails, tweets) into one end. The system extracts typed facts with verbatim citations, stores them in a bi-temporal SQLite database, detects contradictions deterministically at write-time, and answers questions with cited, confidence-scored recommendations that end in a human-approved decision. Every step is logged. Nothing acts autonomously.

```
┌─────────────────┐   POST /brain/ingest    ┌──────────────────────────────────────────┐
│  Raw Document   │ ──────────────────────► │           Ingest Pipeline                │
│  (any text)     │                          │  1. content_hash → dedup check           │
└─────────────────┘                          │  2. INSERT into sources                  │
                                             │  3. claude_extract() → typed JSON facts  │
                                             │     Seam 1 — only LLM call in write path │
                                             │  4. Per-fact: Pydantic validate           │
                                             │  5. INSERT into facts (bi-temporal)      │
                                             │  6. detect_contradictions() — no LLM     │
                                             │  7. link_entities_to_fact() — no LLM     │
                                             │  8. db.commit() — atomic                 │
                                             └──────────────┬───────────────────────────┘
                                                            │ writes to SQLite
                                                            ▼
                                             ┌──────────────────────────────────────────┐
                                             │          SQLite Memory Layer             │
                                             │  sources, facts, contradictions,         │
                                             │  entities, entity_mentions,              │
                                             │  entity_relationships, decisions         │
                                             │  FTS5 full-text search over facts        │
                                             │  WAL mode, foreign_keys ON               │
                                             │  Zero LLM calls — enforced               │
                                             └──────────────┬───────────────────────────┘
                                                            │ reads from SQLite
                                                            ▼
┌─────────────────┐   POST /brain/query     ┌──────────────────────────────────────────┐
│  CEO Question   │ ──────────────────────► │          Decision Pipeline               │
└─────────────────┘                          │  1. FTS5 keyword retrieval (no LLM)      │
                                             │  2. get_contradictions_for_facts()       │
                                             │  3. compute_confidence() — tier-weighted │
                                             │  4. should_research() — OR condition:    │
                                             │     confidence < 0.65 OR any HIGH contra │
                                             │  5. If research: Tavily → re-ingest      │
                                             │     (same pipeline, source_type=research)│
                                             │  6. claude_synthesize() → cited JSON     │
                                             │     Seam 2 — only LLM call in read path  │
                                             │  7. INSERT decisions (human_decision=NULL)│
                                             └──────────────┬───────────────────────────┘
                                                            │
                                                            ▼
                                             ┌──────────────────────────────────────────┐
                                             │  Decision Log (append-only)              │
                                             │  human_decision = NULL until CEO acts    │
                                             │  POST /brain/decisions/{id}/decide       │
                                             │  409 on second call — immutable record   │
                                             └──────────────────────────────────────────┘
```

---

## Core Design Rules (enforced in code, not just docs)

| Rule | Enforcement |
|------|-------------|
| LLM at write-time only | `memory.py` has zero `anthropic` imports — verifiable with grep |
| Sub-100ms reads | SQLite FTS5 + 3 indexed columns. No embeddings in hot path |
| Typed atoms, not summaries | Claude output → `json.loads()` → Pydantic per-fact validation before any INSERT |
| Provenance as rows | `decisions.retrieved_fact_ids` is a JSON array of fact IDs. Citations trace to verbatim quotes |
| Bi-temporal facts | `valid_from` (world time, from document_date) and `learned_at` (ingest time) are separate columns |
| Content-addressed sources | `source_id = content_hash(content)` — re-ingest of same document returns `already_existed: true`, writes nothing |
| Content-addressed facts | `fact_id = content_hash(source_id + fact_type + claim)` — duplicate facts across documents are merged |
| Human decides | `human_decision` defaults NULL. Synthesis never sets it. Only `POST /decide` does. 409 on second call |
| Append-only decisions | No DELETE, no UPDATE on `decisions` except the single `human_decision` write via `/decide` |
| Research excluded from contradiction | `detect_contradictions()` returns `[]` immediately if `source_type == "research"` |
| temperature=0 always | Both `client.messages.create()` calls set it explicitly. No exceptions |

---

## SQLite Schema (actual — from `db.py::init_db()`)

```sql
-- SOURCES: raw inputs, content-addressed
CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,   -- SHA256(content.strip().lower())[:16]
    title         TEXT NOT NULL,
    source_type   TEXT NOT NULL,      -- call_transcript | investor_update | board_note |
                                      -- internal_email | internal_note | social_post |
                                      -- board_document | email | research
    content       TEXT NOT NULL,
    document_date TEXT,               -- ISO "YYYY-MM-DD" — when the document was written
    ingested_at   TEXT NOT NULL       -- UTC ISO datetime — when we learned about it
);

CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(
    title, content,
    content='sources', content_rowid='rowid'
);
-- + 3 triggers: sources_fts_insert, sources_fts_delete, sources_fts_update

-- FACTS: bi-temporal typed atoms
CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,   -- SHA256(source_id + fact_type + claim)[:16]
    source_id       TEXT NOT NULL REFERENCES sources(id),
    fact_type       TEXT NOT NULL,      -- snake_case from EXTRACTION_SYSTEM_PROMPT
    claim           TEXT NOT NULL,      -- complete self-contained statement
    verbatim_quote  TEXT NOT NULL,      -- character-for-character copy from source
    evidence_tier   TEXT NOT NULL,      -- E1 (casual) → E5 (contractual)
    confidence      REAL NOT NULL,      -- 0.0–1.0, set by Claude at extraction
    conditions      TEXT,               -- JSON array or null
    entities        TEXT,               -- JSON array of entity name strings
    numeric_value   REAL,               -- parsed number if fact is numeric
    numeric_unit    TEXT,               -- months | USD | EUR | headcount | percent | k_usd | k_eur
    valid_from      TEXT,               -- world time (from document_date)
    learned_at      TEXT NOT NULL,      -- ingest time (UTC)
    superseded_by   TEXT REFERENCES facts(id),  -- NULL = still current
    contested       INTEGER DEFAULT 0   -- 1 if contradiction detected against this fact
);

CREATE INDEX IF NOT EXISTS idx_facts_type      ON facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_facts_source    ON facts(source_id);
CREATE INDEX IF NOT EXISTS idx_facts_contested ON facts(contested);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact_type, claim, verbatim_quote, conditions,
    content='facts', content_rowid='rowid'
);
-- + 3 triggers: facts_fts_insert, facts_fts_delete, facts_fts_update

-- CONTRADICTIONS: detected at write-time, never by LLM
CREATE TABLE IF NOT EXISTS contradictions (
    id                TEXT PRIMARY KEY,  -- SHA256(fact_type:min_val:max_val)[:16]
    fact_a_id         TEXT NOT NULL REFERENCES facts(id),
    fact_b_id         TEXT NOT NULL REFERENCES facts(id),
    conflict_type     TEXT NOT NULL,     -- numeric_mismatch (only type in this slice)
    description       TEXT NOT NULL,     -- "runway_months: 18.0 months (A) vs 9.0 months (B)"
    severity          TEXT NOT NULL,     -- high (>30% diff) | medium (10–30%)
    detected_at       TEXT NOT NULL,
    resolved_at       TEXT,
    resolution_note   TEXT
);

-- ENTITIES: people, companies, investors, products
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,    -- SHA256(canonical_name.lower())[:12]
    canonical_name  TEXT NOT NULL,
    entity_type     TEXT NOT NULL,       -- person | company | investor | product
    aliases         TEXT NOT NULL,       -- JSON array
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_mentions (
    fact_id   TEXT NOT NULL REFERENCES facts(id),
    entity_id TEXT NOT NULL REFERENCES entities(id),
    PRIMARY KEY (fact_id, entity_id)
);

-- ENTITY RELATIONSHIPS: typed directed graph
CREATE TABLE IF NOT EXISTS entity_relationships (
    id            TEXT PRIMARY KEY,   -- SHA256(entity_a_id + rel_type + entity_b_id)[:12]
    entity_a_id   TEXT NOT NULL REFERENCES entities(id),
    relation_type TEXT NOT NULL,      -- founded | invested_in | works_at | competes_with | angel_invested
    entity_b_id   TEXT NOT NULL REFERENCES entities(id),
    source_id     TEXT REFERENCES sources(id),
    fact_id       TEXT REFERENCES facts(id),
    learned_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rel_entity_a ON entity_relationships(entity_a_id);
CREATE INDEX IF NOT EXISTS idx_rel_entity_b ON entity_relationships(entity_b_id);
CREATE INDEX IF NOT EXISTS idx_rel_type     ON entity_relationships(relation_type);

-- DECISIONS: append-only closed loop
CREATE TABLE IF NOT EXISTS decisions (
    id                    TEXT PRIMARY KEY,  -- UUID4
    question              TEXT NOT NULL,
    retrieved_fact_ids    TEXT NOT NULL,     -- JSON array of fact IDs used in synthesis
    contradictions_found  TEXT,              -- JSON array of contradiction IDs
    research_triggered    INTEGER DEFAULT 0, -- 1 if Tavily was called
    research_queries      TEXT,              -- JSON array of Tavily query strings
    research_fact_ids     TEXT,              -- JSON array of fact IDs from research
    synthesis             TEXT NOT NULL,     -- full cited synthesis text
    confidence            REAL NOT NULL,     -- from Claude's calibrated output
    recommended_action    TEXT NOT NULL,     -- one specific action
    open_gaps             TEXT,              -- JSON array
    key_contradiction     TEXT,              -- one sentence or null
    human_decision        TEXT,              -- NULL | 'approved' | 'rejected'
    decision_note         TEXT,              -- CEO's note when deciding
    created_at            TEXT NOT NULL
    -- UPDATE decisions SET human_decision = ?, decision_note = ? WHERE id = ?
    -- is the ONLY permitted write after INSERT. No DELETE. Ever.
);
```

---

## Module Breakdown (actual — from source)

### `db.py`
- `get_db()` → `sqlite3.Connection` with `row_factory = Row`, WAL mode, foreign_keys ON
- `content_hash(text)` → `SHA256(text.strip().lower().encode()).hexdigest()[:16]`
- `now_iso()` → UTC ISO datetime string
- `init_db()` → runs full schema via `executescript()` (inline SQL, not migrations files)

### `models.py`
- `IngestRequest` — title, source_type (Literal of 9 types), content, document_date, metadata
- `ExtractedFact` — all fact fields + `model_validator` enforcing numeric_value/unit both-or-neither
- `ExtractionResult` — `facts: list[ExtractedFact]`
- `QueryRequest` — question: str
- `DecideRequest` — decision: Literal['approved', 'rejected'], note: Optional[str]
- `Fact`, `Contradiction`, `DecisionRecord` — DB row representations

### `prompts.py`
Two constants, no logic, no imports.
- `EXTRACTION_SYSTEM_PROMPT` — strict JSON output, 21 fact types, 5 evidence tiers, verbatim_quote rule, numeric rules
- `SYNTHESIS_SYSTEM_PROMPT` — strict JSON output, mandatory [fact_id] citations, contradiction surfacing rules, confidence calibration, one-action recommendation format

### `ingest.py` — Write Seam 1

```
ingest_document(req: IngestRequest) → dict
  1. source_id = content_hash(req.content)
  2. if source exists → return {already_existed: True}   ← content-addressed dedup
  3. INSERT into sources
  4. extraction = claude_extract(...)                    ← SEAM 1, temperature=0
     strips markdown fences → json.loads() → per-fact Pydantic validation
     invalid facts: logger.warning + drop (not raise)
  5. for each ExtractedFact:
       fact_id = content_hash(source_id + fact_type + claim)
       if fact_id exists → skip
       INSERT into facts
       link_entities_to_fact(fact_id, ef.entities, db)
       detect_contradictions(fact_id, ef, db, source_id, title, source_type)
  6. db.commit()
  7. return {source_id, facts_extracted, contradictions_detected, fact_ids}

detect_contradictions(new_fact_id, ef, db, source_id, source_title, source_type):
  if source_type == "research" → return []     ← research never pollutes contradiction panel
  if ef.numeric_value is None → return []
  if ef.fact_type not in GLOBALLY_SCOPED_TYPES → return []   ← {"runway_months"} only
  compare against all existing facts of same fact_type from different sources
  skip same-source comparisons (source_id != ?)
  skip research sources in existing set
  diff = abs(new - existing) / max(existing, 0.001)
  if diff <= 0.10 → skip (noise)
  severity = "high" if diff > 0.30 else "medium"
  contradiction_id = SHA256(fact_type:min_val:max_val)[:16]   ← value-pair dedup
  if contradiction already exists → skip
  INSERT into contradictions
  UPDATE facts SET contested = 1 for both fact IDs
```

### `memory.py` — Read Path (zero anthropic imports — enforced)

```
EVIDENCE_TIER_WEIGHTS = {E5: 1.0, E4: 0.9, E3: 0.75, E2: 0.55, E1: 0.35}
RESEARCH_TRIGGER_THRESHOLD = 0.65

retrieve_facts(question, limit=20):
  → FTS5 MATCH over facts_fts (stopwords stripped)
  → fallback: LIKE search on claim/verbatim_quote if FTS returns error
  → ORDER BY confidence DESC, evidence_tier DESC
  → WHERE superseded_by IS NULL

retrieve_facts_by_ids(fact_ids):
  → SELECT with IN clause, full source join

get_contradictions_for_facts(fact_ids):
  → WHERE fact_a_id IN (...) OR fact_b_id IN (...)
  → full fact text joined

compute_confidence(facts, contradictions):
  → base = sum(TIER_WEIGHTS[tier]) / len(facts)
  → multiply by 0.70 per HIGH contradiction
  → multiply by 0.85 per MEDIUM contradiction
  → clamp to [0.10, 0.95]

should_research(confidence, contradictions):
  → confidence < RESEARCH_TRIGGER_THRESHOLD OR any(c.severity == "high")
  → OR condition: HIGH contradiction triggers research even if confidence > 0.65

get_all_contradictions():
  → ORDER BY detected_at DESC
  → both facts' claim/quote/numeric_value/source joined

get_all_decisions(), get_decision(id):
  → read-only queries on decisions table
```

### `entities.py` — Entity Graph (deterministic, no LLM)

12 known entities seeded at startup:
- People: Maya Chen, Devin Park, Priya Nair, Jordan Rivera, Sam Vora
- Companies: Loomwork, Acme Freight, Hartmann Group, DeltaX Logistics
- Investors: Northpeak, Atlas Ventures
- Products: FreightPilot

8 typed relationships seeded: founded, invested_in, works_at, competes_with, angel_invested

```
resolve_entity(name):
  1. Exact match (case-insensitive) against canonical_name + all aliases
  2. Levenshtein ≤ 1 auto-merge (typo threshold)
  → returns canonical_name or None

link_entities_to_fact(fact_id, entity_names, db):
  → for each name: resolve → INSERT OR IGNORE into entity_mentions
```

Entity IDs are `SHA256(canonical_name.lower())[:12]`.

### `research.py` — Tavily Integration

```
research_gaps(queries: list[str]) → list[str]:   (returns fact_ids)
  → Tavily search, max_results=5 per query, up to 3 queries
  → deduplicate by URL (seen_urls set)
  → filter: score ≥ 0.5 AND content ≥ 80 chars
  → evidence tier: E2 for HIGH_AUTHORITY_DOMAINS, E1 otherwise
  → ingest_document(source_type="research", metadata={evidence_tier_override: tier})
  → research facts flow through the same write pipeline as corpus docs
  → research sources never create contradictions (excluded in detect_contradictions)
```

High authority domains include: sec.gov, crunchbase.com, techcrunch.com, wsj.com, ft.com, bloomberg.com, reuters.com, hbr.org, mckinsey.com, bcg.com.

### `decision.py` — Write Seam 2

```
answer_question(question: str) → dict:
  1. facts = retrieve_facts(question, limit=20)
  2. fact_ids = [f.id for f in facts]
  3. contradictions = get_contradictions_for_facts(fact_ids)
  4. confidence = compute_confidence(facts, contradictions)
  5. if should_research(confidence, contradictions):
       queries = build_research_queries(question, contradictions)
       → up to 3 queries built from contradiction descriptions + "data 2026"
       research_fact_ids = research_gaps(queries)
       research_facts = retrieve_facts_by_ids(research_fact_ids)
       facts = facts + research_facts
       contradictions = get_contradictions_for_facts([f.id for f in facts])
       confidence = compute_confidence(facts, contradictions)
  6. synthesis_json = claude_synthesize(question, facts, contradictions)  ← SEAM 2
       strips fences → json.loads()
       returns: {synthesis, confidence, recommended_action, open_gaps, key_contradiction}
  7. decision_id = uuid4()
     INSERT into decisions (human_decision=NULL)
  8. return {decision_id, synthesis, confidence, recommended_action,
             open_gaps, key_contradiction, research_triggered, research_queries,
             sources_cited}

Note: confidence in response uses synthesis_json["confidence"] — Claude's calibrated
output — not the locally computed value. Local computation is used only for the
should_research() gate.
```

### `seed.py` — One-Command Seeder

```
init_db() → seed_entities() → for each doc in data/loomwork_corpus.json:
  ingest_document(IngestRequest(title, source_type, content, document_date))
  → time.sleep(0.5) between docs (rate-limit headroom)
→ print per-doc counts and totals
```

Opens corpus with `encoding="utf-8"` — required on Windows to handle `€` characters.

### `main.py` — FastAPI Application

```python
app = FastAPI(title="Decision Brain", version="1.0.0",
              docs_url="/brain/docs", openapi_url="/brain/openapi.json")
app.mount("/brain/static", StaticFiles(directory="static"))
```

Lifespan: `init_db()` + `seed_entities()` on startup.

---

## REST API Endpoints

All paths prefixed `/brain/`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/brain/` | Serve `static/index.html` — minimal UI |
| GET | `/brain/healthz` | `{status, facts_count, decisions_count, contradictions_count}` |
| POST | `/brain/ingest` | Ingest document → extract facts → detect contradictions |
| GET | `/brain/facts` | List facts; params: `fact_type`, `query`, `limit`, `include_contested` |
| GET | `/brain/contradictions` | All contradictions with both facts' text and numeric values joined |
| GET | `/brain/entities` | All entities with mention counts |
| POST | `/brain/query` | Answer question → synthesis + decision log write |
| GET | `/brain/decisions` | All decisions (append-only log) |
| GET | `/brain/decisions/{id}` | Single decision record |
| POST | `/brain/decisions/{id}/decide` | Set human_decision; 409 if already decided |
| GET | `/brain/docs` | Auto-generated OpenAPI UI (FastAPI) |
| GET | `/brain/openapi.json` | OpenAPI spec — MCP-ready agent surface |

---

## Key Invariants — Grep Verification

```bash
# I1: memory.py has zero anthropic imports
grep "anthropic" memory.py           # must return nothing

# I2: decisions table append-only
grep -n "DELETE.*decisions\|UPDATE.*decisions" *.py
# only permitted match: main.py — UPDATE decisions SET human_decision

# I3: both LLM call sites
grep -rn "client.messages.create" *.py   # must return exactly 2 lines

# I4: temperature=0 everywhere
grep -rn "temperature" *.py | grep -v "temperature=0"   # must return nothing

# I5: content_hash formula
grep "content_hash" db.py   # SHA256(text.strip().lower().encode())[:16]
```

---

## Confidence Scoring — How It Works

| Tier | Weight | Typical sources |
|------|--------|----------------|
| E5 | 1.00 | Contractual, audited statements |
| E4 | 0.90 | Board commitments, executive decisions in writing |
| E3 | 0.75 | Stated with supporting reasoning or data |
| E2 | 0.55 | Stated as fact, no supporting detail |
| E1 | 0.35 | Casual mention, uncertain language ("roughly", "maybe") |

Base confidence = `sum(weights) / count(facts)`

Penalties:
- Each HIGH severity contradiction: × 0.70
- Each MEDIUM severity contradiction: × 0.85

Clamped to [0.10, 0.95].

Claude's confidence (from synthesis output) overrides the local computation in the final response. Local computation is used only for `should_research()`.

---

## Research Trigger Logic

```python
should_research(confidence, contradictions):
    has_high = any(c.severity == "high" for c in contradictions)
    return confidence < 0.65 OR has_high
```

This is an **OR** condition. For Q3, confidence comes back at ~0.68 — above threshold. But three HIGH contradictions are present. Research fires regardless. Tavily fetches AE cost benchmarks which join the fact set as E1/E2 evidence before synthesis.

---

## Citation Flow

1. Claude extracts fact → stored with 16-char hex `id`
2. At synthesis: fact IDs passed to Claude alongside verbatim quotes
3. Synthesis prompt mandates: every claim must be followed by `[fact_id]`
4. Response stored in `decisions.synthesis` with inline `[hexid]` citations
5. UI `renderSynthesis()` converts `[hexid]` → superscript badge showing first 6 chars; full ID in tooltip
6. Any reviewer can trace: badge → fact_id → `GET /brain/facts` → verbatim_quote → source document

---

## Known Gaps (documented, not bugs)

| Gap | Impact | Fix |
|----|--------|-----|
| Categorical contradiction detection not built | ICP conflict not a DB row; surfaces in synthesis only | Second path in `detect_contradictions()` for `claim_conflict` type |
| Schema inline in `db.py`, `migrations/` unused | Migration files are dead code | Wire `init_db()` to iterate `migrations/*.sql` |
| Invalid facts dropped (not raised) | Malformed Claude output silently removed; visible in seed logs | Acceptable for demo; raise + retry in production |
| Entity resolution hardcoded | New entities not auto-discovered from extraction | Dynamic resolution from extraction output |
| No test suite | Manual verification only | pytest suite covering ingest, contradiction, synthesis, 409 |
| Docker does not auto-seed | User must run `python seed.py` after `docker compose up` | Entrypoint script: `seed.py && uvicorn` |
