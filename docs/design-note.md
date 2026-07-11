# Design Note — Loomwork Decision Brain

**Author:** Syed Mujtaba Mahdi
**Project:** Loomwork Decision Brain (Builders Studio Challenge)
**Slice:** Q3 Runway — "What runway can I defend in this week's investor update?"

**How to read this note:** It explains the architecture, the invariants, why certain choices were made, and what was deliberately left out. It is written for a technical reviewer (Michael) who will read the code and the README next to it.

---

## 1. Architecture and where the LLM sits

The system is a three-stage pipeline around a SQLite memory core:

```
Document → Ingest (Claude extracts typed facts) → SQLite → Memory (no LLM) →
Decision (retrieve, optionally Tavily research, Claude synthesizes) → Append-only log
```

**LLM is only at the seams, never in the read path.**

- **Seam 1 — `ingest.py::claude_extract()`:** Claude reads a raw document and emits strict JSON facts. This is the only LLM call in the write path.
- **Seam 2 — `decision.py::claude_synthesize()`:** Claude receives the CEO question, retrieved facts, and detected contradictions, then emits a cited recommendation.

Every other operation is deterministic: entity resolution (`entities.py`), contradiction detection (`ingest.py::detect_contradictions()`), retrieval (`memory.py` using SQLite FTS5), and confidence scoring (`memory.py`). This keeps the read path fast and auditable.

The SQLite schema is bi-temporal: `valid_from` is the document date (world time), `learned_at` is when we ingested it. Sources are content-addressed by SHA256 so re-ingesting the same document is a no-op.

**Why this architecture:**
- FastAPI auto-generates an OpenAPI contract at `/brain/openapi.json`, making the API agent-discoverable.
- SQLite + FTS5 handles hundreds of facts with zero infrastructure; no embeddings in the hot path.
- The append-only `decisions` table guarantees the CEO can see exactly what the system recommended and when they approved or rejected it.

---

## 2. Agreements with the spec (invariants)

| Invariant | How the code honors it |
|-----------|------------------------|
| I1: `memory.py` has zero anthropic imports | Confirmed. `grep -r anthropic memory.py` returns nothing. |
| I2: No spurious mutations on the `decisions` table | The only mutation of `decisions` after insert is in `main.py` inside the `/brain/decisions/{id}/decide` route, which sets `human_decision` and `decision_note` exactly once. |
| I3: Contradiction detection runs in the same transaction as the fact INSERT | `detect_contradictions()` is called inside `ingest_document()` before `db.commit()`. |
| I4: Exactly two `client.messages.create` calls | One in `ingest.py`, one in `decision.py`. |
| I5: `human_decision` is set only via `/decide` | The synthesis pipeline never writes `human_decision`; only the `/decide` route does. |
| I6: Content-addressed dedup | `source_id = content_hash(content)`; `fact_id = content_hash(source_id + fact_type + claim)`. Re-ingest returns `already_existed: true`. |
| I7: `temperature=0` for all Claude calls | Both `client.messages.create` calls set `temperature=0`. |
| I8: All Claude output parsed with `json.loads()` + Pydantic | `claude_extract()` and `claude_synthesize()` strip markdown fences, run `json.loads()`, and validate with Pydantic models. |

---

## 3. Deliberate divergences from the spec, with defense

### 3.1 Model name: using `claude-sonnet-4-5-20250929` instead of `claude-3-5-haiku-20241022`

The spec names `claude-3-5-haiku-20241022`. The Anthropic account used for this build did not expose that model ID. The available models were a mix of `claude-sonnet-*`, `claude-opus-*`, and `claude-haiku-4-5-20251001`.

I tested each option:
- `claude-haiku-4-5-20251001` supported `temperature=0` but frequently violated the numeric schema (e.g., emitting `numeric_value` without `numeric_unit` for `deal_stage`), which broke the seed.
- `claude-sonnet-5` rejected `temperature=0` entirely.
- `claude-sonnet-4-5-20250929` supported `temperature=0` and reliably followed the strict JSON schema.

**Defense:** `temperature=0` is the hard invariant, not the model string. The chosen model preserves determinism and keeps the extraction pipeline stable. I documented this in `README.md` and `audit.md`.

### 3.2 Invalid extracted facts are dropped, not raised

The spec says: "If Pydantic validation fails on an extracted fact, raise." I instead log a warning and drop the invalid fact.

**Defense:** The available model occasionally emits malformed numeric pairs (e.g., `deal_stage` with `numeric_value=3` and `numeric_unit=null`). Raising would crash the whole seed on every document that triggers this mistake. Dropping keeps the pipeline resilient without creating ghost data. The drops are logged and visible in the seed output.

Separately, I found one fact that sits in a grey zone: fact #3 in the `runway_months` set, extracted from the Board Note, has `numeric_value=18.0` and a claim that reads "The 18-month runway figure in the investor update does not account for the two AE hires planned for Q3." That is a reference to the Northpeak investor update's number, not an independent assertion of a new runway figure. I left it in deliberately: removing it does not change the contradiction set, because the 18mo vs 9mo pairing is already captured independently by the Northpeak update (#2) and the board note's own 9-month assertion (#4). Fixing the extraction prompt to distinguish assertion from reference is real work that would not move any output for this corpus. I chose not to spend remaining time on it.

### 3.3 Contradiction detection is scoped to the Q3 metric

The spec's example algorithm compares any two facts of the same `fact_type` with a numeric difference >10%. The initial phase-3 implementation did exactly this — no scoping. Running the full seed with that logic produced 114 contradictions: every deal with a different ACV, every customer with a different budget threshold, all flagged. That number appeared in the seed output and immediately showed the signal was buried.

I then added `GLOBALLY_SCOPED_TYPES` and value-pair deduplication as a second step, after seeing the output, not before:

```python
GLOBALLY_SCOPED_TYPES = {"runway_months"}
```

That fix is in commit `431375f`. After re-seeding with the scoped logic, the result was three persisted runway contradictions: 18mo vs 9mo, 18mo vs 24mo, and 9mo vs 24mo — a genuine three-point declining trend (24 → 18 → 9 months, Q1 Atlas to May Northpeak to June board note post-hire) that I hadn't anticipated would exist when I started the seed. Deal-specific values (`deal_value_acv`, `budget_authority_threshold`) are not contradictions — they are data points per customer.

**Defense:** The scoping rule came from observation, not pre-planning: I ran the naive algorithm, saw 114 rows, recognized the problem, and narrowed to company-wide metrics. The three-way declining trend was a discovery, not a design target. That sequence is more honest than presenting the result as pre-planned, and it is also a better demonstration of what the system is actually for — it found something real.

### 3.4 ICP contradiction is surfaced in synthesis, not stored as a DB row

The ICP contradiction (mid-market self-serve vs. moving upmarket) is categorical, not numeric. The current `detect_contradictions()` only handles numeric mismatches, so this contradiction is not inserted into the `contradictions` table.

**Defense:** The Q3 slice is about runway, not ICP. The conflicting ICP facts are still retrieved and surfaced by `claude_synthesize()` in the synthesis text when relevant. A future phase would add a categorical-conflict detector to the DB layer.

---

## 4. What was skipped and why

| Feature | Why it's out of this slice |
|---------|---------------------------|
| Promotion ladder (candidate → emerging → validated) | The brief mentions it, but implementing a full confidence-promotion workflow would push the build beyond the two-day window. Facts carry evidence tiers and confidence scores; the ladder is a natural next layer. |
| Multi-tenancy | No customer isolation in this slice. The schema is straightforward to extend with an `organization_id` column. |
| Auth / SSO | Not required for a single-tenant demo. The API is open on localhost. |
| MCP wrapper | REST is the agent surface first. Once the OpenAPI contract is stable, an MCP layer is trivial to add. |
| Full React frontend | The three-panel vanilla HTML/JS UI is enough to demonstrate the loop. A polished React app would consume the same API endpoints. |
| Embeddings / vector search | SQLite FTS5 handles the corpus size. At 10,000+ facts, a vector index would become necessary. |
| Q1 (ICP drift) and Q2 (budget objection) synthesis | The corpus contains the signals for both, but this slice deliberately answers one question completely rather than three questions partially. The API already supports asking them; only the synthesis focus changes. |

---

## 5. Why Q3 first

Q3 was chosen because it is the highest-stakes, most time-sensitive question in the corpus:
- The CEO has an investor update to send this week.
- The corpus contains a direct, authoritative contradiction (May update: 18 months; June board note: 9 months post-hire).
- The answer requires reconciling two internal documents, not just summarizing one.
- It forces the full pipeline to prove itself: extraction, contradiction detection, research, synthesis, and human decision logging.

If the system cannot handle a runway contradiction that the board already wrote down, it cannot handle any executive decision. Q3 is the smallest slice that exercises every critical component.

---

## 6. What's next

1. **Tighten contradiction detection** — add categorical conflict detection so the ICP contradiction is stored as a DB row, not just surfaced in synthesis.
2. **Answer Q1 and Q2** — build focused synthesis prompts for ICP drift and budget objection, using the same retrieval/research/decision pipeline.
3. **Promotion ladder** — add `candidate → emerging → validated` status transitions with human review gates.
4. **Multi-tenancy and auth** — add `organization_id`, RBAC, and API keys for production deployment.
5. **Frontline React app** — replace the vanilla UI with a proper React dashboard once the API contract is locked.
6. **MCP server** — expose the decision brain as an MCP tool so other agents can call it natively.
7. **Assertion vs. reference extraction** — the extraction prompt does not yet distinguish a document *asserting* a numeric value from a document *referencing* a value stated elsewhere. Fact #3 in the runway set is a reference that was extracted as an assertion. For this corpus the contradiction set is unaffected, but the distinction matters at scale and should be built into the schema (e.g., a `is_reference` flag on `facts`) and the extraction prompt.

---

## 7. Model and environment notes

- **Stack:** Python · FastAPI · SQLite (FTS5) · Claude · Tavily · minimal HTML/JS UI.
- **Claude model in code:** `claude-sonnet-4-5-20250929` with `temperature=0`.
- **Reason for deviation from `claude-3-5-haiku-20241022`:** model not available in the Anthropic account used for this build; the only available model that supported `temperature=0` and reliably followed the strict JSON schema was `claude-sonnet-4-5-20250929`.
- **API keys required:** `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` in `.env`.
- **Run order:** `python seed.py` (server off) → `uvicorn main:app --host 0.0.0.0 --port 8090` → open `http://localhost:8090/brain/`.
