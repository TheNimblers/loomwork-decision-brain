# Design Note — Loomwork Decision Brain

**Author:** Syed Mujtaba Mahdi · **Slice:** Q3 — "What runway can I defend in this week's investor update?"

---

## 1. Architecture — where the LLM sits and why

```
Document → Ingest (Claude extracts typed facts) → SQLite →
Memory (no LLM) → Decision (retrieve → Tavily if needed → Claude synthesizes) → Append-only log
```

**LLM at exactly two seams, nowhere else.**

- **Seam 1 — `ingest.py::claude_extract()`:** Claude reads a raw document and emits strict JSON typed facts. One call per document. `temperature=0`. Validated by Pydantic before any row is written.
- **Seam 2 — `decision.py::claude_synthesize()`:** Claude receives the CEO question, retrieved facts with IDs and verbatim quotes, and detected contradictions. Emits a cited JSON answer — every factual claim must carry a `[fact_id]` inline or the output is invalid.

Everything between the seams is deterministic: FTS5 retrieval, contradiction detection (arithmetic, no LLM), confidence scoring (tier-weighted average with contradiction penalties), entity resolution (exact match + Levenshtein ≤ 1). The read path has zero anthropic imports — enforced, verifiable with grep.

**Why SQLite + FTS5 over a vector DB:** the corpus is hundreds of facts, not millions. FTS5 gives sub-10ms keyword retrieval with zero infrastructure. Contradiction detection requires structured `numeric_value` comparisons — embeddings cannot do this. At 10k+ facts a vector index would sit alongside FTS5, not replace it.

**Why the schema is bi-temporal:** `valid_from` is when the fact was true in the world (document date). `learned_at` is when it was ingested. "What did we know on June 1?" is a SQL query, not a rebuild. Sources are SHA256 content-addressed — re-ingesting the same document is a no-op.

---

## 2. Where I agreed with the spec

| Invariant | Code location |
|-----------|---------------|
| `memory.py` has zero anthropic imports | Enforced — grep confirms |
| Contradiction detection in same transaction as INSERT | `detect_contradictions()` called before `db.commit()` in `ingest_document()` |
| Exactly two `client.messages.create` calls | One in `ingest.py`, one in `decision.py` |
| `human_decision` set only via `/decide` | Synthesis never touches it; only the `/decide` route does — 409 on second call |
| Content-addressed dedup | `source_id = SHA256(content.strip().lower())[:16]`; `fact_id = SHA256(source_id + fact_type + claim)[:16]` — via `db.py::content_hash()` |
| `temperature=0` on all Claude calls | Both calls set it explicitly |
| Claude output parsed with `json.loads()` + Pydantic | Both seams strip markdown fences, parse JSON, validate with Pydantic |

---

## 3. Where I diverged and why

**Model string.** The spec names `claude-3-5-haiku-20241022`. That ID was not available in the account. I tested three alternatives: haiku-4-5 violated the numeric schema at extraction; sonnet-5 rejected `temperature=0`; sonnet-4-5-20250929 honored both. The hard invariant is `temperature=0`, not the model name.

**Invalid facts are dropped, not raised.** The spec says raise on Pydantic failure. The available model occasionally emits malformed numeric pairs (e.g., `numeric_value` present, `numeric_unit` null). Raising crashes the entire seed on one bad fact. Dropping logs a warning and keeps the pipeline resilient — the drops are visible in seed output. No ghost data is created.

**Contradiction detection scoped to `runway_months`.** The naive algorithm — compare any two facts of the same `fact_type` with >10% numeric difference — produced 114 contradictions on the first seed run: every deal ACV, every budget threshold, all flagged. Signal buried. I added `GLOBALLY_SCOPED_TYPES = {"runway_months"}` after seeing that output, not before. Deal-specific values are data points per customer, not contradictions. The result was three runway rows — 24 → 18 → 9 months across Q1 Atlas, May Northpeak, June board note — a genuine declining trend I had not anticipated. I am telling the sequence because presenting it as pre-planned would be dishonest.

**ICP contradiction not stored as a DB row.** The ICP conflict (tweet: mid-market self-serve; investor update: moving upmarket) is categorical, not numeric. `detect_contradictions()` compares `numeric_value` fields only. The signal surfaces in synthesis when relevant facts are retrieved — it is not lost. **This is the clearest gap.** The fix is a second detection path for `conflict_type = "claim_conflict"`: same `fact_type`, conflicting string values, different sources. The schema already supports it. This is the first thing I build next.

---

## 4. What was skipped

| Skipped | Why |
|---------|-----|
| Q1 (ICP drift) and Q2 (budget objection) synthesis | One question answered completely beats three answered halfway. The pipeline supports both — only the retrieval focus changes. |
| Promotion ladder (candidate → emerging → validated) | Facts carry evidence tiers and confidence scores. The ladder is the natural next layer; implementing it would have consumed the two days. |
| Categorical contradiction detection | Numeric-only in this slice. Named and fixed above as next build item. |
| File upload | UI accepts pasted text. A multipart route + per-type text extractor feeds the existing `ingest_document()` unchanged. |
| Multi-tenancy, auth, MCP | Single-tenant demo. `organization_id` column and an MCP layer mapping one-to-one from the OpenAPI spec are straightforward next steps. |

---

## 5. What is next

1. **Categorical contradiction detection** — `claim_conflict` path in `detect_contradictions()`. ICP becomes a DB row.
2. **Q1 and Q2 synthesis** — same pipeline, different retrieval focus. Facts are already in the DB.
3. **Dynamic entity resolution** — remove hardcoded `KNOWN_ENTITIES`; resolve from extraction output dynamically.
4. **Promotion ladder** — `candidate → emerging → validated` with human review gates.
5. **MCP server** — OpenAPI spec maps one-to-one to MCP tools.
