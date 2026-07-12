# Design Note — Loomwork Decision Brain

**Author:** Syed Mujtaba Mahdi · **Slice:** Q3 — "What runway can I defend in this week's investor update?"

---

## 1. Architecture — where the LLM sits and why

```
Document → Ingest (Claude extracts typed facts) → SQLite →
Memory (no LLM) → Decision (retrieve → Tavily if needed → Claude synthesizes) → Append-only log
```

LLM at exactly two seams. **Seam 1** (`ingest.py::claude_extract`): one call per document, `temperature=0`, strict JSON output, per-fact Pydantic validation before any row is written. **Seam 2** (`decision.py::claude_synthesize`): receives the CEO question, retrieved facts with IDs and verbatim quotes, and detected contradictions; every factual claim in the response must carry a `[fact_id]` citation or the output is invalid. Everything between the seams is deterministic — FTS5 retrieval, arithmetic contradiction detection, tier-weighted confidence scoring, Levenshtein entity resolution. Zero anthropic imports in `memory.py` — enforced, verifiable with grep.

**The architectural question I answered differently than Part One:** Part One uses embedding-based signal clustering to connect learnings across sources. I did not build that, and not because I ran out of time. For this question type it is the wrong tool. The runway contradiction is not a semantic similarity problem — it is an arithmetic problem. `18 ≠ 9`. An embedding will score those as *similar* because both appear in runway sentences; it will not surface the conflict. The system that finds the contradiction needs a `numeric_value` column, an inequality check, and a threshold. That is what I built. The claim that embeddings replace SQL in a decision support system is wrong: embeddings are a retrieval heuristic, not a truth model. The truth model is the bi-temporal schema.

The tradeoff is real: FTS5 misses semantically related facts that use different words. At 10k+ facts, a vector index sits *alongside* FTS5 as a second retrieval path — it does not replace the structured contradiction layer. The architecture scales additively, not by substitution.

**Memory vs. research gate:** `should_research()` fires on an OR condition — confidence below 0.65 *or* any HIGH-severity contradiction present. For Q3, local confidence was 0.68 (above threshold), but three HIGH contradictions were present. Research fired regardless. Tavily results re-enter through `ingest_document()` as `source_type="research"` with E1/E2 evidence tiers — the same write pipeline, not a special code path. They are deduplicated, citation-tracked, and never double-counted.

---

## 2. Where I agreed with Part One, and why each choice is right independently

**No LLM in the read path.** LLMs in the read path make the system non-auditable — "why did it say that last week?" becomes unanswerable if synthesis re-runs on every query. The deterministic read path means every answer is reproducible from the same fact set. That is a stronger auditability guarantee than any logging layer.

**Contradiction detection at write-time, in the same transaction as INSERT.** Detecting at query time makes the contradiction a function of which facts happen to be retrieved — the same question asked twice could return a different contradiction set. Detecting at write-time makes it a property of the data: stored once, with both fact IDs, a severity, and a description, persisting across all future queries.

**Content-addressed identity + `temperature=0`.** `source_id = SHA256(content)[:16]`, `fact_id = SHA256(source_id + fact_type + claim)[:16]`. Re-ingest is structurally a no-op. `temperature=0` makes extraction deterministic enough that the contradiction detector's output is a function of the schema, not of inference variance.

**Human decides, system recommends.** `human_decision` is NULL until `/decide` is called. Synthesis never sets it. A 409 fires on any second call. This is not a safety feature bolted on — the system is structurally incapable of approving its own recommendation.

---

## 3. Where I diverged, and the defense

**3.1 Contradiction scoped to `runway_months` — the 114-contradiction lesson.**

The naive algorithm — flag any two facts of the same `fact_type` with >10% numeric difference — produced 114 contradictions on first seed run. Acme €10k vs Brightway €12k: flagged. Every budget threshold across calls: flagged. Signal buried completely.

I added `GLOBALLY_SCOPED_TYPES = {"runway_months"}` after seeing that output. The insight it forced: not all numeric facts are globally comparable. Deal-level metrics are *per-entity* data points — two customers with different budget ceilings is expected signal. Company-level metrics are *globally* comparable — one company cannot simultaneously have 9 and 18 months of runway. The result was three rows: 24 → 18 → 9 months across Q1 Atlas, May Northpeak, June board note. A declining trend I had not anticipated; I am saying so because presenting it as pre-planned would be dishonest. The 114-contradiction run is the proof the scoping was necessary, not premature.

**3.2 ICP contradiction not stored as a DB row — the clearest gap.**

The ICP conflict (tweet: mid-market self-serve; investor update: moving upmarket) is categorical. `detect_contradictions()` compares `numeric_value` fields only. The signal surfaces in synthesis, but it is not a first-class row with a severity. The schema supports it — `conflict_type` is `TEXT NOT NULL` and `claim_conflict` is a valid value. The fix is a second detection path: same `fact_type`, conflicting string values, different sources, write-time. This is the first thing I build next.

**3.3 Invalid facts dropped, not raised.** The model occasionally emits malformed numeric pairs. Raising crashes the entire seed on one bad fact out of ~100. Dropping logs a `logger.warning` — visible, no ghost data. The right failure mode for a pipeline running against an external API is: discard the untrustworthy record, log it, keep processing.

---

## 4. What was deliberately skipped

| Skipped | Why |
|---------|-----|
| Q1 (ICP drift) and Q2 (budget objection) | One question answered completely beats three answered halfway. Facts are in the DB; only retrieval focus and synthesis context change. |
| Signal promotion ladder | Facts carry evidence tiers and confidence. The ladder is the next layer — implementing it well requires the categorical contradiction detection and embedding retrieval path first. |
| Embeddings / vector retrieval | Right for scale; wrong as the primary contradiction mechanism. Added alongside FTS5 at 10k+ facts. |
| Multi-tenancy, auth, MCP | `organization_id` on existing tables; MCP maps one-to-one to the OpenAPI spec at `/brain/docs`. Straightforward additions. |
| Test suite | Manual verification: Q3, approve, 409, idempotent re-ingest, grep invariant checks. All passed. |

---

## 5. What is next, in order

1. Categorical contradiction detection — `claim_conflict` path, ICP becomes a DB row
2. Q1 and Q2 synthesis — same pipeline, different retrieval focus
3. Signal promotion ladder — `candidate → emerging → validated`, human review gates
4. Dynamic entity resolution — extract from Claude output, auto-merge at Levenshtein ≤ 1
5. MCP server — one adapter over the existing OpenAPI spec, no new routes

---

*`claude-3-5-haiku-20241022` was unavailable. `claude-sonnet-4-5-20250929` was selected after testing three alternatives against the `temperature=0` + strict-JSON-output constraint. The hard invariant is `temperature=0`, not the model string.*
