# Design Note — Loomwork Decision Brain

**Author:** Syed Mujtaba Mahdi · **Slice:** Q3 — "What runway can I defend in this week's investor update?"

---

## 1. Architecture — where the LLM sits and why

```
Document → Ingest (Claude extracts typed facts) → SQLite →
Memory (no LLM) → Decision (retrieve → Tavily if needed → Claude synthesizes) → Append-only log
```

**LLM at exactly two seams, nowhere else.**

- **Seam 1 — `ingest.py::claude_extract()`:** Claude reads a raw document and emits strict JSON typed facts. One call per document. `temperature=0`. Output stripped of markdown fences, parsed with `json.loads()`, validated per-fact with Pydantic before any row is written. Invalid facts are logged and dropped (see §3.2).
- **Seam 2 — `decision.py::claude_synthesize()`:** Claude receives the CEO question, retrieved facts with IDs and verbatim quotes, and detected contradictions. Emits a cited JSON answer — every factual claim must carry a `[fact_id]` inline or the output is invalid.

Everything between the seams is deterministic: FTS5 keyword retrieval, contradiction detection (arithmetic, no LLM), confidence scoring (tier-weighted average with contradiction penalties), entity resolution (exact match + Levenshtein ≤ 1). The read path has zero anthropic imports — enforced, verifiable with grep.

**Why SQLite + FTS5 over a vector DB:** the corpus is hundreds of facts, not millions. FTS5 gives sub-10ms keyword retrieval with zero infrastructure. Contradiction detection requires structured `numeric_value` comparisons — embeddings cannot do this. At 10k+ facts a vector index would sit alongside FTS5, not replace it.

**Why the schema is bi-temporal:** `valid_from` is when the fact was true in the world (document date). `learned_at` is when it was ingested. "What did we know on June 1?" is a SQL query, not a rebuild. Sources are SHA256 content-addressed — re-ingesting the same document is a no-op.

**Why research facts re-enter through the same ingest pipeline:** Tavily results pass through `ingest_document()` with `source_type="research"`. They get fact-extracted by Claude, stored with E1/E2 evidence tiers, and join the fact set for synthesis. There is no special code path for external data — it is just weaker-tier data in the same tables. This also means research results are deduplicated, citation-tracked, and never double-counted.

---

## 2. Where I agreed with the spec

| Invariant | Code location |
|-----------|---------------|
| `memory.py` has zero anthropic imports | Enforced — grep confirms |
| Contradiction detection in same transaction as INSERT | `detect_contradictions()` called before `db.commit()` in `ingest_document()` |
| Exactly two `client.messages.create` calls | One in `ingest.py`, one in `decision.py` |
| `human_decision` set only via `/decide` | Synthesis inserts NULL; only the `/decide` route writes a value — 409 on second call |
| Content-addressed dedup | `source_id = SHA256(content.strip().lower())[:16]` via `db.py::content_hash()`; `fact_id = SHA256(source_id + fact_type + claim)[:16]` |
| `temperature=0` on all Claude calls | Both calls set it explicitly |
| Claude output parsed with `json.loads()` + Pydantic | Both seams strip markdown fences, parse JSON, validate with Pydantic |
| Research sources excluded from contradiction detection | `detect_contradictions()` returns `[]` immediately if `source_type == "research"` |

---

## 3. Where I diverged and why

**3.1 Model string.** The spec names `claude-3-5-haiku-20241022`. That ID was not available in the account. I tested three alternatives: haiku-4-5 violated the numeric schema at extraction; sonnet-5 rejected `temperature=0`; `claude-sonnet-4-5-20250929` honored both constraints. The hard invariant is `temperature=0`, not the model name.

**3.2 Invalid facts are dropped, not raised.** The spec says raise on Pydantic failure. The available model occasionally emits malformed numeric pairs (e.g., `numeric_value` present, `numeric_unit` null). `models.py::ExtractedFact` has a `model_validator` that catches this. Raising on that failure crashes the entire seed on one bad fact out of ~100. Dropping logs a `logger.warning` and keeps the pipeline resilient — the drops are visible in seed output. No ghost data is created. This is a conscious production tradeoff.

**3.3 Contradiction detection scoped to `runway_months`.** The naive algorithm — compare any two facts of the same `fact_type` with >10% numeric difference — produced 114 contradictions on the first seed run: every deal ACV, every budget threshold, all flagged. Signal buried. I added `GLOBALLY_SCOPED_TYPES = {"runway_months"}` after seeing that output, not before. Deal-specific values are data points per customer, not contradictions. The result was three runway rows — 24 → 18 → 9 months across Q1 Atlas, May Northpeak, June board note — a genuine declining trend I had not anticipated. I am telling the sequence because presenting it as pre-planned would be dishonest.

**3.4 Contradiction deduplication by value pair, not fact pair.** The contradiction `id` is `SHA256(fact_type:min_val:max_val)[:16]`, not `SHA256(fact_a_id + fact_b_id)`. This means the same 18-vs-9 mismatch is stored once regardless of which specific fact instances carry those values. The architecture doc describes a fact-pair hash — the actual implementation uses value-pair hashing because numeric facts of the same type from different extraction runs can produce different fact IDs with the same underlying numeric conflict.

**3.5 ICP contradiction not stored as a DB row.** The ICP conflict (tweet: mid-market self-serve; investor update: moving upmarket) is categorical, not numeric. `detect_contradictions()` compares `numeric_value` fields only. The signal surfaces in synthesis when relevant facts are retrieved — it is not lost. **This is the clearest gap in this slice.** The fix is a second detection path for `conflict_type = "claim_conflict"`: same `fact_type`, conflicting string values, different sources. The schema already supports it (`conflict_type` TEXT NOT NULL). That is the first thing I build next.

**3.6 Schema inlined in `db.py`, not loaded from `migrations/`.** The `migrations/*.sql` files exist and are correct but `init_db()` never reads them — it runs an inline `executescript()`. Both are in sync. The migration files will be wired in before any production schema change. For a two-day demo the inline approach simplified the startup sequence with no correctness cost.

---

## 4. What was skipped

| Skipped | Why |
|---------|-----|
| Q1 (ICP drift) and Q2 (budget objection) synthesis | One question answered completely beats three answered halfway. The pipeline supports both — only the retrieval focus and synthesis prompt context change. |
| Signal promotion ladder (candidate → emerging → validated) | Facts carry evidence tiers and confidence scores. The ladder is the natural next layer; implementing it would have consumed the two days. |
| Categorical contradiction detection | Numeric-only in this slice. Named and fixed above as §6 item 1. |
| File upload | UI accepts pasted text. A multipart route + per-type text extractor feeds the existing `ingest_document()` unchanged. |
| Multi-tenancy, auth, MCP | Single-tenant demo. `organization_id` column and an MCP layer mapping one-to-one from the OpenAPI spec at `/brain/docs` are straightforward next steps. |
| Embedding / vector search | FTS5 handles the corpus. At 10k+ facts, pgvector sits alongside FTS5 for semantic retrieval. Not a replacement. |
| Test suite | `tests/` is empty. The six-step manual verification (Q3 answer, approve, 409, idempotent re-ingest, grep checks) was run and passed. |

---

## 5. What is next

1. **Categorical contradiction detection** — `claim_conflict` path in `detect_contradictions()`. ICP becomes a DB row with severity.
2. **Q1 and Q2 synthesis** — same pipeline, different retrieval focus. Facts are already in the DB.
3. **Dynamic entity resolution** — remove hardcoded `KNOWN_ENTITIES`; resolve from extraction output dynamically, flag low-confidence merges for human review.
4. **Signal promotion ladder** — `candidate → emerging → validated` with human review gates.
5. **MCP server** — OpenAPI spec at `/brain/docs` maps one-to-one to MCP tools. No new routes needed.
6. **Wire `migrations/` into `init_db()`** — iterate over `migrations/*.sql` in filename order, `executescript()` each.
7. **Assertion vs. reference extraction** — some facts echo a prior document rather than state a new claim. A `is_reference: bool` field on `ExtractedFact` would let synthesis distinguish "board note says X" from "board note echoes the investor update saying X" — different epistemic weight.
