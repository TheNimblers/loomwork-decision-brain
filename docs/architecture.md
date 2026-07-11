# Architecture — Decision Brain
**Builders Studio Challenge · Full Technical Specification**

---

## System Overview

A CEO drops a week of raw inputs (board notes, investor updates, transcripts, emails) into one end. The system extracts typed facts with verbatim citations, stores them in a bi-temporal SQLite database, detects contradictions deterministically, and answers questions with cited, confidence-scored answers that end in a human-approved recommendation. Every step is logged. Nothing acts autonomously.

```
Raw Document         POST /brain/ingest         Ingest Pipeline
(any text)  ─────────────────────────►   1. Hash content → deduplicate
                                         2. Claude extract → typed JSON facts
                                         3. Store facts bi-temporally in SQLite
                                         4. Detect contradictions (deterministic)
                                         5. Resolve entities (fuzzy match)
                                                │
                                                ▼ writes to SQLite
                                      SQLite Memory Layer
                                      sources / facts / contradictions /
                                      entities / entity_mentions / decisions
                                      FTS5 for full-text search
                                      No LLM ever touches this path
                                                │
                                                ▼ reads from SQLite
CEO Question         POST /brain/query        Decision Pipeline
                     ─────────────────────────►  1. FTS5 retrieval (no LLM)
                                                  2. Contradiction check (no LLM)
                                                  3. Confidence check → Tavily if low
                                                  4. Research facts re-ingested (same pipe)
                                                  5. Claude synthesize → strict JSON out
                                                  6. Write to decision log (append-only)
                                                            │
                                                            ▼
                                                   Decision Log (append-only)
                                                   human_decision = null until CEO acts
                                                   POST /brain/decisions/{id}/decide
```

---

## Core Design Rules (enforced in code, not just docs)

| Rule | How it is enforced |
|------|--------------------|
| LLM at write-time only | No `anthropic.messages.create` call anywhere in `memory.py` or any read-path function |
| Reads sub-100ms | SQLite FTS5 + indexed columns only. No embeddings in hot path. |
| Typed atoms, not summaries | Claude output is parsed with `json.loads` + Pydantic validation. String-only output fails at ingest |
| Provenance as rows | Every decision log row stores `retrieved_fact_ids` as a JSON array. Citations are fact IDs, not text |
| Bi-temporal | `valid_from` (world time) and `learned_at` (ingest time) are separate NOT NULL columns |
| Content-addressed | `id = SHA256(source_id + fact_type + normalise(claim))[:16]` — re-ingest of same document is a no-op |
| Human decides | `human_decision` column defaults to NULL. Synthesis endpoint never sets it. Only `/decide` does |
| Append-only decisions | No UPDATE or DELETE on the `decisions` table. Ever |

---

## SQLite Schema

```sql
-- SOURCES: raw inputs, content-addressed
CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,   -- SHA256(content)[:16]
    title         TEXT NOT NULL,
    source_type   TEXT NOT NULL,      -- 'investor_update' | 'board_note' | 'transcript' | 'email' | 'doc' | 'research'
    content       TEXT NOT NULL,
    document_date TEXT,               -- ISO date "YYYY-MM-DD" — when the document was written (world time)
    ingested_at   TEXT NOT NULL       -- ISO datetime — when we learned about it
);

-- Full-text search over sources
CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(
    title, content,
    content='sources', content_rowid='rowid'
);

-- FACTS: bi-temporal typed atoms
CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,   -- SHA256(source_id || fact_type || normalised_claim)[:16]
    source_id       TEXT NOT NULL,
    fact_type       TEXT NOT NULL,      -- snake_case: runway_months | headcount | arr | churn_rate | burn_rate ...
    claim           TEXT NOT NULL,      -- complete self-contained statement
    verbatim_quote  TEXT NOT NULL,      -- exact words from source
    evidence_tier   TEXT NOT NULL,      -- E1 (casual mention) → E5 (board/legal commitment)
    confidence      REAL NOT NULL,      -- 0.0–1.0, set by Claude at extraction
    conditions      TEXT,               -- JSON array: ["after 2 AE hires"] or null
    entities        TEXT,               -- JSON array of entity name strings
    numeric_value   REAL,               -- parsed number if fact is numeric
    numeric_unit    TEXT,               -- 'months' | 'USD' | 'headcount' | '%' etc.
    valid_from      TEXT,               -- world time: when this was true (from document_date)
    learned_at      TEXT NOT NULL,      -- ingest time: when we stored it
    superseded_by   TEXT,               -- FK to facts.id — NULL means still current
    contested       INTEGER DEFAULT 0,  -- 1 if a contradiction has been detected against this fact
    FOREIGN KEY (source_id)    REFERENCES sources(id),
    FOREIGN KEY (superseded_by) REFERENCES facts(id)
);

CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id);
CREATE INDEX IF NOT EXISTS idx_facts_contested ON facts(contested);

-- Full-text search over facts
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact_type, claim, verbatim_quote, conditions,
    content='facts', content_rowid='rowid'
);

-- CONTRADICTIONS: detected at write-time
CREATE TABLE IF NOT EXISTS contradictions (
    id                TEXT PRIMARY KEY,  -- SHA256(fact_a_id || fact_b_id)[:16]
    fact_a_id         TEXT NOT NULL,
    fact_b_id         TEXT NOT NULL,
    conflict_type     TEXT NOT NULL,     -- 'numeric_mismatch' | 'claim_conflict' | 'temporal_conflict'
    description       TEXT NOT NULL,     -- human-readable: "18 months (investor_update) vs 9 months (board_note)"
    severity          TEXT NOT NULL,     -- 'high' | 'medium' | 'low'
    detected_at       TEXT NOT NULL,
    resolved_at       TEXT,              -- null until resolved
    resolution_note   TEXT,
    FOREIGN KEY (fact_a_id) REFERENCES facts(id),
    FOREIGN KEY (fact_b_id) REFERENCES facts(id)
);

-- ENTITIES: people, companies, investors
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,    -- SHA256(normalised_canonical_name)[:12]
    canonical_name  TEXT NOT NULL,
    entity_type     TEXT NOT NULL,       -- 'person' | 'company' | 'investor' | 'product'
    aliases         TEXT NOT NULL,       -- JSON array of known name variants
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_mentions (
    fact_id   TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    PRIMARY KEY (fact_id, entity_id),
    FOREIGN KEY (fact_id)   REFERENCES facts(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

-- ENTITY RELATIONSHIPS: typed directed edges
CREATE TABLE IF NOT EXISTS entity_relationships (
    id            TEXT PRIMARY KEY,   -- SHA256(entity_a_id || relation_type || entity_b_id)[:12]
    entity_a_id   TEXT NOT NULL,
    relation_type TEXT NOT NULL,       -- 'founded' | 'invested_in' | 'works_at' | 'competes_with' | 'angel_invested'
    entity_b_id   TEXT NOT NULL,
    source_id     TEXT,               -- document where this relationship was extracted
    fact_id       TEXT,               -- specific fact that established this relationship
    learned_at    TEXT NOT NULL,
    FOREIGN KEY (entity_a_id) REFERENCES entities(id),
    FOREIGN KEY (entity_b_id) REFERENCES entities(id),
    FOREIGN KEY (source_id)   REFERENCES sources(id),
    FOREIGN KEY (fact_id)     REFERENCES facts(id)
);

CREATE INDEX IF NOT EXISTS idx_rel_entity_a ON entity_relationships(entity_a_id);
CREATE INDEX IF NOT EXISTS idx_rel_entity_b ON entity_relationships(entity_b_id);
CREATE INDEX IF NOT EXISTS idx_rel_type     ON entity_relationships(relation_type);

-- DECISIONS: append-only, the closed loop
CREATE TABLE IF NOT EXISTS decisions (
    id                    TEXT PRIMARY KEY,  -- UUID4
    question              TEXT NOT NULL,
    retrieved_fact_ids    TEXT NOT NULL,     -- JSON array of fact IDs used in retrieval
    contradictions_found  TEXT,              -- JSON array of contradiction IDs surfaced
    research_triggered    INTEGER DEFAULT 0, -- 1 if Tavily was called
    research_queries      TEXT,              -- JSON array of search strings sent to Tavily
    research_fact_ids     TEXT,              -- JSON array of fact IDs created from research results
    synthesis             TEXT NOT NULL,     -- full answer with [fact_id] citations inline
    confidence            REAL NOT NULL,     -- overall answer confidence
    recommended_action    TEXT NOT NULL,     -- single specific next action
    open_gaps             TEXT,              -- JSON array: what we don't know
    key_contradiction     TEXT,              -- description of main conflict, or null
    human_decision        TEXT,              -- NULL | 'approved' | 'rejected'
    decision_note         TEXT,              -- CEO's note when deciding
    created_at            TEXT NOT NULL
    -- NO UPDATE allowed on this table. human_decision is set once via /decide endpoint.
);
```

---

## Module Breakdown

### `db.py` — Database layer
- `get_db()` → returns sqlite3 connection with `row_factory = sqlite3.Row`
- `init_db()` → runs all CREATE TABLE statements above
- `content_hash(text: str) → str` → `hashlib.sha256(text.strip().lower().encode()).hexdigest()[:16]`
- `now_iso() → str` → UTC ISO datetime

### `ingest.py` — Write pipeline (LLM touches here)
```
ingest_document(title, source_type, content, document_date) → IngestResult
  1. hash = content_hash(content)
  2. if source exists by hash → return early (no-op, content-addressed)
  3. INSERT into sources
  4. facts_json = claude_extract(source_type, title, content, document_date)
     → strict JSON, validated with Pydantic
  5. for each fact in facts_json:
       a. fact_id = content_hash(source_id + fact_type + normalise(claim))
       b. if fact_id already exists → skip (re-ingest is no-op)
       c. INSERT into facts
       d. detect_contradictions(fact)  ← deterministic, no LLM
       e. resolve_entities(fact)       ← deterministic, no LLM
  6. return { source_id, facts_count, contradictions_count }
```

### `memory.py` — Read path (NO LLM — enforced by convention, zero anthropic imports)
```
retrieve_facts(query: str, fact_types: list[str] = None, limit: int = 20) → list[Fact]
  → SQLite FTS5 search over facts_fts WHERE superseded_by IS NULL
  → Filter by fact_type if provided
  → Order by confidence DESC, learned_at DESC

get_contradictions(fact_ids: list[str]) → list[Contradiction]
  → SELECT from contradictions WHERE fact_a_id IN (...) OR fact_b_id IN (...)

compute_answer_confidence(facts: list[Fact], contradictions: list[Contradiction]) → float
  → base = mean(f.confidence for f in facts) weighted by evidence_tier_weight[f.evidence_tier]
  → if any contradiction with severity='high': multiply by 0.5
  → if any contradiction with severity='medium': multiply by 0.75
  → return clamped to [0.0, 1.0]

should_research(confidence: float, contradictions: list[Contradiction]) → bool
  → return confidence < 0.65 OR any(c.severity in ('high', 'medium') for c in contradictions)
```

**Evidence tier weights for confidence calculation:**
```python
TIER_WEIGHTS = { "E5": 1.0, "E4": 0.9, "E3": 0.8, "E2": 0.6, "E1": 0.4 }
```

### `entities.py` — Entity resolution (deterministic, no LLM)
```
resolve_entity(name: str, entity_type: str) → entity_id
  1. Normalise: lowercase, strip punctuation, collapse whitespace
  2. Exact match against entities.canonical_name → return entity_id
  3. Check aliases JSON for exact match → return entity_id
  4. Levenshtein distance to all canonical_names:
       if min_distance <= 2 → merge candidate, flag for human review (do not auto-merge)
       if min_distance <= 1 → auto-merge (typo threshold)
  5. If no match → INSERT new entity, return new entity_id
```

### `research.py` — Tavily search (results re-enter the same pipeline)
```
research_gaps(queries: list[str], parent_decision_id: str) → list[str]
  → for each query: tavily_client.search(query, max_results=3)
  → for each result: ingest_document(
       title = result['url'],
       source_type = 'research',
       content = result['title'] + '\n' + result['content'],
       document_date = today
     )
  → return list of ingested fact IDs
```

Research facts re-enter through `ingest.py` exactly like any other document. They get `evidence_tier` set to E1 or E2 by Claude (external web claim), `confidence` typically 0.4–0.6. They are stored with `source_type = 'research'` and their URL as the `title`. They appear in citations like any other fact.

### `decision.py` — Decision pipeline (LLM touches here for synthesis only)
```
answer_question(question: str) → DecisionResult
  1. facts = memory.retrieve_facts(question, limit=20)
  2. contradictions = memory.get_contradictions([f.id for f in facts])
  3. confidence = memory.compute_answer_confidence(facts, contradictions)
  4. research_fact_ids = []
     if memory.should_research(confidence, contradictions):
       queries = build_research_queries(question, contradictions)
       research_fact_ids = research.research_gaps(queries, decision_id)
       research_facts = memory.retrieve_facts_by_ids(research_fact_ids)
       facts = facts + research_facts
       confidence = memory.compute_answer_confidence(facts, contradictions)
  5. synthesis_json = claude_synthesize(question, facts, contradictions)
     → strict JSON, validated with Pydantic
  6. decision_id = uuid4()
     INSERT into decisions (append-only)
  7. return DecisionResult(decision_id, synthesis_json, facts, contradictions)
```

### `prompts.py` — Claude prompt templates (constants only, no logic)

See `prompts.py` for the full extraction and synthesis prompts. Both are strict JSON-output system prompts with `temperature=0`.

### `main.py` — FastAPI app
```python
app = FastAPI(title="Decision Brain")
app.mount("/brain/static", StaticFiles(directory="static"), name="static")
```

---

## REST API Endpoints

All paths include `/brain/` prefix.

### `GET /brain/`
Serves `static/index.html` — the minimal UI.

### `GET /brain/healthz`
```json
{ "status": "ok", "facts_count": 42, "decisions_count": 3, "contradictions_count": 1 }
```

### `POST /brain/ingest`
**Request:**
```json
{
  "title": "Investor Update Q2 2026",
  "source_type": "investor_update",
  "content": "...full text...",
  "document_date": "2026-07-01"
}
```
**Response:**
```json
{
  "source_id": "a3f9b2c1",
  "already_existed": false,
  "facts_extracted": 7,
  "contradictions_detected": 1,
  "contradictions": [
    {
      "id": "x7y2z1",
      "description": "runway_months: 18 months (investor_update) conflicts with 9 months (board_note, conditional on 2 AE hires)",
      "severity": "high"
    }
  ]
}
```

### `GET /brain/facts`
**Query params:** `fact_type=`, `query=`, `limit=20`, `include_contested=true`
**Response:** Array of fact objects with full source context joined.

### `GET /brain/contradictions`
**Response:** Array of contradiction objects with both facts fully expanded.

### `POST /brain/query`
**Request:**
```json
{ "question": "What is our runway and is the number in the investor update accurate?" }
```
**Response:**
```json
{
  "decision_id": "uuid",
  "synthesis": "Your investor update states 18 months of runway [a3f9b2c1]...",
  "confidence": 0.71,
  "recommended_action": "Verify the post-hire burn rate with your CFO before the next investor meeting...",
  "open_gaps": ["Current monthly burn rate not found in ingested documents"],
  "key_contradiction": "Investor update (18 months) directly conflicts with board note (9 months post-hire)...",
  "research_triggered": true,
  "sources_cited": [...]
}
```

### `GET /brain/decisions`
Returns all decision log entries, ordered by `created_at DESC`. `human_decision` is null until decided.

### `POST /brain/decisions/{id}/decide`
**Request:** `{ "decision": "approved" | "rejected", "note": "optional CEO note" }`
**Response:** Updated decision record.
**Constraint:** This is the only endpoint that writes to `human_decision`. It is an append — no other field changes.

---

## Contradiction Detection Algorithm

Run after every new fact insertion. Fully deterministic, no LLM:

```python
def detect_contradictions(new_fact: Fact, db: Connection) -> list[Contradiction]:
    contradictions = []

    # Step 1: Find existing facts of the same type
    existing = db.execute(
        "SELECT * FROM facts WHERE fact_type = ? AND id != ? AND superseded_by IS NULL",
        (new_fact.fact_type, new_fact.id)
    ).fetchall()

    for existing_fact in existing:
        # Step 2a: Numeric mismatch — same type, different value, same unit
        if (new_fact.numeric_value is not None
                and existing_fact['numeric_value'] is not None
                and new_fact.numeric_unit == existing_fact['numeric_unit']):
            relative_diff = abs(new_fact.numeric_value - existing_fact['numeric_value']) / max(existing_fact['numeric_value'], 0.001)
            if relative_diff > 0.10:  # >10% difference = contradiction
                severity = 'high' if relative_diff > 0.30 else 'medium'
                contradiction = Contradiction(
                    fact_a_id=existing_fact['id'],
                    fact_b_id=new_fact.id,
                    conflict_type='numeric_mismatch',
                    description=f"{new_fact.fact_type}: {existing_fact['numeric_value']} {existing_fact['numeric_unit']} vs {new_fact.numeric_value} {new_fact.numeric_unit}",
                    severity=severity
                )
                contradictions.append(contradiction)
                # Mark both facts as contested
                db.execute("UPDATE facts SET contested = 1 WHERE id IN (?, ?)",
                           (existing_fact['id'], new_fact.id))

    return contradictions
```

---

## Environment Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
DATABASE_PATH=./brain.db
PORT=8090
```

---

## Agent Surface

FastAPI generates a complete OpenAPI 3.1 spec automatically at `/brain/openapi.json`. Any LLM agent can:

1. Fetch `/brain/openapi.json` to discover all endpoints, parameters, and response schemas
2. Call `/brain/ingest` to add documents to memory
3. Call `/brain/query` to ask a question and get a structured cited answer
4. Call `/brain/decisions/{id}/decide` to record a human decision

The API is intentionally designed for agent consumption:
- Every response is typed JSON matching a Pydantic schema
- `fact_id` references are stable
- The `/brain/docs` endpoint serves Swagger UI for interactive human exploration
- Error responses follow FastAPI's standard format with `detail` fields

---

## Entity Resolution — Loomwork Corpus

The corpus contains recurring entities across 12 documents. Resolution is deterministic:

| Canonical name | Aliases in corpus | Type |
|---------------|-------------------|------|
| Maya Chen | Maya, @mayachen_loomwork | person |
| Devin Park | Devin | person |
| Priya Nair | Priya | person |
| Jordan Rivera | Jordan | person |
| Sam Vora | Sam | person |
| Loomwork | @loomwork, loomwork.com | company |
| Northpeak | Northpeak Capital | investor |
| Atlas Ventures | Atlas | investor |
| Acme Freight | Acme | company |
| FreightPilot | — | competitor |

Resolution algorithm: normalise → exact match → Levenshtein ≤ 1 (auto-merge) → Levenshtein 2–3 (flag for review, do not auto-merge). No LLM. Every merge decision is auditable.

---

## File Structure

```
loomwork-decision-brain/
├── main.py
├── db.py
├── ingest.py
├── memory.py
├── entities.py
├── research.py
├── decision.py
├── prompts.py
├── models.py
├── static/
│   └── index.html
├── data/
│   └── loomwork_corpus.json
├── migrations/
├── docs/
│   ├── architecture.md
│   └── design-note.md
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Startup Command

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8090}
```

On startup: `init_db()` runs, creates all tables if not exist. On first request to `POST /brain/ingest`, the database populates.
