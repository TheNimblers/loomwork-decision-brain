# Decision Brain — Builders Studio Challenge
**Syed Mujtaba Mahdi**

A CEO drops a week of raw inputs into one end. The system extracts typed facts with verbatim citations, detects contradictions deterministically at write-time, researches gaps via Tavily, and surfaces a cited recommendation the CEO explicitly approves or rejects. Every decision is logged append-only. The system never acts alone.

---

## Persona — Maya Chen, Loomwork

**Maya Chen** — Founder / CEO, Loomwork. Series A, ~40 people. AI ops copilot for logistics teams.
**Devin Park** — CTO. **Priya Nair** — Head of Sales.
**Investors** — Northpeak (Series A lead), Atlas Ventures (seed), Sam Vora (angel).
**Shadow competitor** — FreightPilot, mentioned in 4 out of 5 recent calls.

**The tensions in the corpus:**
- ICP drift: the tweet ships mid-market self-serve; the investor update says "moving upmarket." Maya's own note says raise minimum deal size to $25k.
- Runway contradiction: ~18 months in the Northpeak update; ~9 months in Devin's board note once 2 AEs are hired.
- Budget authority: blocks 3 out of 4 active deals, each with a different threshold ($10k, $12k, $15k).
- FreightPilot: comes up in 4 calls. Never formally tracked. $30k cheaper in one lost deal debrief.

---

## Scope — Q3 only

This slice answers one question completely:

**Q3 — Runway:**
> "What runway can I defend in this week's investor update?"

The Northpeak update says ~18 months. Devin's board note two days later says ~9 months once the two AE hires go through. The system detects this contradiction at write-time, researches AE cost benchmarks via Tavily, reconciles the burn math, and produces a cited recommendation Maya explicitly approves or rejects. Decision logged append-only.

---

## What is in the corpus but not answered in this slice

The challenge example defines three CEO questions. The corpus contains the signals for all three. This slice delivers Q3 only — one question answered completely is the right call for two days.

**Q1 — ICP drift** (corpus has the signal, not answered):
> "Is our ICP actually mid-market, or are we drifting up?"
The tweet ships mid-market self-serve. The Northpeak update says "moving upmarket." Maya's own note says raise minimum deal size to $25k. The contradiction is in the data and the brain will surface it — but Q1 synthesis is not in this slice.

**Q2 — Budget objection** (corpus has the signal, not answered):
> "Which objection is killing deals, and is it real?"
Budget authority blocks three deals at three thresholds ($10k, $12k, $15k). Priya's pipeline email names all three. The pattern is in the data — Q2 synthesis is not in this slice.

These are next steps, not omissions. The design note explains why Q3 first.

---

## Stack

Python · FastAPI · SQLite (FTS5 + bi-temporal schema) · Claude · Tavily · minimal HTML/JS UI

**Stack rationale:** Python because the AI tooling (Anthropic SDK, Tavily) is idiomatic there. FastAPI because it generates an OpenAPI spec at `/brain/docs` automatically — this is the agent surface. SQLite because the dataset is hundreds of facts, not millions, and FTS5 gives fast model-free reads with zero infrastructure.

**UI:** A small, single-page results view — enough to see Q3 working without spending the two days on UI instead of the loop. Three panels: the Q3 answer with cited fact IDs, the contradiction that triggered it, and the decision log with approve/reject. No framework.

**Note on model:** The build targets `claude-3-5-haiku-20241022`. The Anthropic account available for this build did not expose that model, so the running code uses `claude-sonnet-4-5-20250929` with `temperature=0` to preserve the deterministic intent.

---

## What the UI shows

The results view has three panels:

1. **Q3 answer panel** — the synthesised answer, confidence score, key contradiction highlighted, recommended action, and open gaps. Every claim links to its fact ID.
2. **Contradiction panel** — three runway contradictions shown: April baseline 24 months (Atlas Q1), May investor update 18 months (Northpeak), June board note 9 months conditional on AE hires. The declining-trend story across three sources.
3. **Decision log** — the pending recommendation. One approve button. One reject button. 409 if you try to approve twice.

---

## Requirements

- Python 3.13+ (matches Dockerfile)
- Anthropic API key
- Tavily API key (free tier sufficient)

---

## Environment variables

Create `.env` in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
DATABASE_PATH=./brain.db
PORT=8090
```

No secrets in the repo. `.env` is in `.gitignore`. `.env.example` is committed with placeholder values.

---

## Install

```bash
pip install -r requirements.txt
```

---

## Seed — one command

```bash
python seed.py
```

Reads `data/loomwork_corpus.json` and ingests all 12 corpus documents in order. Run before starting the server.

Actual output from this build:

```
Seeding 12 documents...

  OK [ 1/12] Investor Update — May 2026 (Northpeak Series A)       15 facts, 0 contradictions
  OK [ 2/12] Board Note — Q2 Runway and Hiring Decision             9 facts, 1 contradictions <- contradiction [HIGH]
  OK [ 3/12] Maya Chen Tweet — June 12 2026                         4 facts, 0 contradictions
  OK [ 4/12] Customer Call — Acme Freight Evaluation               10 facts, 0 contradictions
  OK [ 5/12] Sales Call — Brightway Logistics                        9 facts, 0 contradictions
  OK [ 6/12] Account Review — Delta Logix                            9 facts, 0 contradictions
  OK [ 7/12] Follow-up Call — Synapse Logistics                      8 facts, 0 contradictions
  OK [ 8/12] Investor Update — Q1 2026 (Atlas Ventures)              8 facts, 2 contradictions <- contradiction [HIGH]
  OK [ 9/12] Maya Weekly Note — ICP Decision June 20                 6 facts, 0 contradictions
  OK [10/12] Pipeline Update — Priya Nair June 25                   14 facts, 0 contradictions
  OK [11/12] Lost Deal Debrief — FreightMax Nordic                   9 facts, 0 contradictions
  OK [12/12] Sam Vora Email — Runway and ICP Alignment               8 facts, 0 contradictions

Seed complete. 12 sources 109 facts 3 contradictions detected.
```

The three persisted contradictions are all runway mismatches (18mo vs 9mo, 18mo vs 24mo, 9mo vs 24mo). Deal-specific values like `deal_value_acv` and `budget_authority_threshold` are intentionally excluded from numeric contradiction detection because a different customer's deal size is a data point, not a contradiction.

**Note on fact count reproducibility:** Anthropic's own documentation states that temperature=0 "will not be fully deterministic" — Claude ships no seed parameter, so variable batch composition on shared inference servers can produce minor token-level variance between runs. The per-document fact counts above may vary by a small number of facts if you re-run `seed.py`. The contradiction set (three runway mismatches) and the Q3 answer are stable regardless, because they are driven by the numeric values in the source documents, not by marginal extraction decisions.

---

## Run — one command

```bash
uvicorn main:app --host 0.0.0.0 --port 8090
```

Open `http://localhost:8090/brain/` in a browser.

---

## Ask — Q3

**Via the UI:** use the Query panel.

**Via curl:**
```bash
curl -X POST http://localhost:8090/brain/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What runway can I defend in this week'\''s investor update?"}'
```

**Approve a decision:**
```bash
curl -X POST http://localhost:8090/brain/decisions/{id}/decide \
  -H "Content-Type: application/json" \
  -d '{"decision": "approved", "note": "Confirmed with Devin — use 9-month figure with AE caveat"}'
```

**Actual Q3 response from this build:**

```json
{
  "decision_id": "2f6568a5-1e06-4a09-b7c8-004d35de166d",
  "synthesis": "You cannot defend 18 months of runway in this week's investor update...",
  "confidence": 0.68,
  "recommended_action": "Call your CFO today to confirm the exact post-hire monthly burn rate and cash balance, then update the investor deck to state '9 months of runway post-Q3 AE hires' before sending this week's update.",
  "open_gaps": [
    "Exact monthly burn rate after the two AE hires start in August",
    "Current cash balance as of this week",
    "Whether the AE hiring decision is final or still reversible",
    "Any revenue assumptions or other offsets that might extend the 9-month figure"
  ],
  "key_contradiction": "The May investor update claims 18 months of runway at current burn, but the June board note states this excludes planned Q3 AE hires, reducing actual runway to 9 months.",
  "research_triggered": true,
  "sources_cited": [...]
}
```

The full synthesis explains that 18 months is the pre-hire figure, the June 10 board note revises it to 9 months post-hire, and Sam Vora has flagged the discrepancy. The defensible number is **9 months of runway post-Q3 AE hires**, with the caveat that the CFO must confirm the exact post-hire burn rate.

---

## Corpus — 12 documents (`data/loomwork_corpus.json`)

| ID | Type | Date | Key signals |
|----|------|------|-------------|
| `northpeak-may` | investor_update | 2026-05-28 | Runway 18mo, ICP → enterprise |
| `board-q2` | board_note | 2026-06-10 | Runway 9mo conditional ← **contradiction** |
| `maya-tweet-0612` | social_post | 2026-06-12 | ICP mid-market self-serve ← **contradiction** |
| `acme-eval` | call_transcript | 2026-06-03 | Budget <$10k, FreightPilot |
| `brightway-call` | call_transcript | 2026-05-14 | FreightPilot 30% cheaper, budget |
| `delta-logix-call` | call_transcript | 2026-06-17 | Budget <$15k, FreightPilot approved vendor |
| `synapse-call` | call_transcript | 2026-06-24 | Budget <$12k, July cycle |
| `atlas-q1` | investor_update | 2026-04-02 | Runway 24mo (Q1 baseline), ICP mid-market |
| `maya-weekly-jun20` | internal_note | 2026-06-20 | ICP drift deliberation, min deal $25k |
| `priya-pipeline-jun25` | internal_email | 2026-06-25 | Budget pattern in 3 deals, FreightPilot |
| `freightmax-call` | call_transcript | 2026-06-19 | Lost to FreightPilot (€30k cheaper) |
| `sam-vora-email` | email | 2026-06-13 | Runway + ICP inconsistency flagged by investor |

---

## REST API

All paths prefixed `/brain/`. FastAPI generates interactive docs at `/brain/docs` and machine-readable OpenAPI spec at `/brain/openapi.json`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/brain/` | UI |
| GET | `/brain/docs` | Interactive OpenAPI docs (agent-consumable) |
| GET | `/brain/openapi.json` | OpenAPI spec |
| GET | `/brain/healthz` | Status + fact/decision/contradiction counts |
| POST | `/brain/ingest` | Ingest a document |
| GET | `/brain/facts` | List extracted facts (filter by fact_type, query) |
| GET | `/brain/contradictions` | List all detected contradictions with full context |
| GET | `/brain/entities` | List resolved entities |
| POST | `/brain/query` | Ask a question — retrieve, research, synthesise |
| GET | `/brain/decisions` | Append-only decision log |
| GET | `/brain/decisions/{id}` | Get a single decision |
| POST | `/brain/decisions/{id}/decide` | Approve or reject (409 if already decided) |

**Agent surface:** Any LLM agent can discover and call this API via the OpenAPI spec at `/brain/openapi.json`. The spec is generated automatically by FastAPI — no separate maintenance required.

---

## What I mocked or capped

- **Embeddings:** not used. SQLite FTS5 keyword search handles retrieval for this dataset size. Noted here rather than hidden. At 10,000+ facts, FTS5 would need augmenting with a vector index.
- **Promotion ladder:** not built. Facts carry confidence scores. The full candidate → emerging → validated promotion ladder from the challenge brief is a next step.
- **Multi-tenancy:** not implemented.
- **Auth:** none.
- **MCP:** REST first. MCP is a natural next layer once the core contract is stable.

---

## What I deliberately left out and why

The promotion ladder, multi-tenancy, auth, MCP, and a full frontend are all out of scope. The design note (`docs/design-note.md`) explains each tradeoff, the invariant architecture, and the deliberate divergences from the brief. The sharp slice — one pipeline that does extraction, contradiction detection, research, synthesis, and decision logging correctly — is worth more than six features that each do half the job.

---

## Project structure

```
loomwork-decision-brain/
├── main.py              # FastAPI app
├── db.py                # SQLite init, content_hash, bi-temporal helpers
├── ingest.py            # Write pipeline — Claude at the seam
├── memory.py            # Read path — zero anthropic imports (enforced)
├── entities.py          # Entity resolution — deterministic, no LLM
├── research.py          # Tavily → re-ingest via same pipeline
├── decision.py          # Decision pipeline — Claude synthesis
├── prompts.py           # Claude prompt constants
├── models.py            # Pydantic models
├── seed.py              # One-command seeder, reads data/loomwork_corpus.json
├── static/
│   └── index.html       # Minimal UI
├── data/
│   └── loomwork_corpus.json  # 12-item corpus array
├── migrations/          # Plain SQL schema files
├── docs/
│   ├── architecture.md  # Schema and API contracts
│   └── design-note.md   # Submission reasoning
├── README.md
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Docker

```bash
docker compose up --build
```

The app is available at `http://localhost:8090/brain/`.
