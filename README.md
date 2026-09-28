# Financial Research Agent

An agentic RAG system that answers natural-language questions about SEC 10-K filings with source-grounded citations, exposed through both a FastAPI REST interface and a Model Context Protocol (MCP) server — with a streamed, full-stack Next.js chat UI on top.

## In one minute

- **What it is.** Five FY2025 10-K filings, cleaned to their 10-K documents (4,783 chunks) and their tagged XBRL facts (6,089), behind a LangGraph ReAct agent with five tools, a FastAPI streaming API, an MCP server and a Next.js chat UI. Free tiers throughout.
- **What is measured, and where.** A 71-item labelled benchmark ([eval/EVALUATION.md](eval/EVALUATION.md), every number traceable to a file under [eval/results/](eval/results/)). Retrieval, scored without a model: the shipped configuration puts a relevant chunk in the top 5 on **66.2%** of questions (dense alone 50.7%). Answers: the figure the question asked for is in the answer on **100%** of the items that have one (judged runs, first index), faithfulness 0.957. Every answer is verified against what was retrieved before it is served (70 of 70 verified on the latest run), at about $0.002 and 4 s per query on a laptop.
- **What it is not.** Not a production service (no auth, no rate limit, one small corpus), not statistically powered (n = 71, five strata under 9 items), and its answer-quality judge has not yet been re-bought on the rebuilt index (limitation 19).
- **What changed after review.** Three-quarters of the first index was XBRL markup, identifiers and metadata rather than 10-K text; finding 22 records the audit, the fix and every number that moved. [docs/DECISIONS.md](docs/DECISIONS.md) lists the design decisions with their evidence. MIT licensed.

---

## Live demo

- **App:** <https://financial-research-agent-pi.vercel.app>
- **Full stack:** `Next.js UI → SSE → FastAPI (/query/stream) → LangGraph ReAct agent → ChromaDB + Gemini`
- **Cold start:** the backend runs on a free tier and sleeps after inactivity — the **first request may take ~30–60 s** to wake the container, after which answers stream token-by-token. Please don't load-test the live link (Gemini free-tier RPM limits).
- **The deployed backend is a separate repository, redeployed deliberately.** It lives in
  its own Hugging Face Space repo rather than being built from this one on every push, so
  the two can drift. Its application code is **synced by hand from `main`**; the last sync
  is commit `11bb229` (2026-09-28), which carries everything described below — fact tools,
  retrieval switches, meter and answer verification. A later commit on `main` reaches the
  Space only at the next sync (see Deploy). What deliberately differs is the
  deployment machinery: the Space ships a **prebuilt Chroma index via Git LFS**, because
  re-embedding the old 67K-chunk index at image-build time exceeded Hugging Face's build timeout on the
  free CPU builder, whereas this repo's Dockerfile rebuilds the index and gitignores it.
  Because sync is manual, treat the live demo's revision as unverified unless you check it
  — numbers in [eval/EVALUATION.md](eval/EVALUATION.md) always name the exact agent model
  they were measured on.

---

## Architecture

```
       SEC EDGAR
           │
           ▼
   ┌──────────────────┐
   │   Ingestion      │   downloader → cleaner → chunker → embedder
   │ (ingestion/…)    │   (HTML strip, 512-char chunks, MiniLM embeddings)
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │    ChromaDB      │   persistent vector store, 4,783 chunks
   │ (data/chroma_db) │   metadata: ticker, chunk_idx, source path
   │  + facts.sqlite  │   6,089 tagged XBRL facts: concept, period, unit, segment
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │  ReAct Agent     │   LangGraph create_react_agent, tool-calling loop
   │ (agent/…)        │   tools: search_filings, list_available_companies,
   │                  │          compare_companies, lookup_financial_fact,
   │                  │          compute_metric
   └────────┬─────────┘
            ▼
   ┌──────────────────┐       ┌──────────────────┐
   │  MCP Server      │◀──────│  MCP Agent       │   tools exposed over
   │ (streamable-HTTP)│       │ (adapter client) │   streamable-HTTP JSON-RPC
   └────────┬─────────┘       └────────┬─────────┘
            ▼                          ▼
   ┌────────────────────────────────────────────┐
   │           FastAPI (api/main.py)            │   POST /query, POST /query/stream, GET /health
   │   MCP-first, direct-agent fallback          │   lifespan-built agents
   └────────────────────────────────────────────┘
            ▼
   ┌──────────────────┐
   │   Docker Compose │   api-server (8080) + mcp-server (8000)
   └──────────────────┘
```

Requests to `POST /query` try the MCP-backed agent first. If MCP is unreachable or times out, the request falls through to the in-process direct agent so the API stays available during transport outages.

**In production that fallback fires on every request.** The deployed Space runs a single container with no MCP server alongside it — `GET /health` there reports `"mcp_server": false` — so the live demo always answers via the in-process agent. That is a deliberate deployment choice: running a second container purely to prove the protocol works is not something a single-user demo warrants. The MCP server exists to show the tools being *served over* MCP and consumed through `langchain-mcp-adapters`, and it is exercised locally via `docker-compose`, not in the hosted demo. It has no tests and no evaluation behind it either — see [ROADMAP.md](ROADMAP.md) item 3.

**Frontend (`web/`).** A Next.js + TypeScript chat UI streams answers over Server-Sent Events:

```
Next.js UI  →  POST /query/stream (SSE)  →  FastAPI  →  LangGraph ReAct agent  →  ChromaDB + Gemini
```

The browser client uses `fetch` + `ReadableStream` (not `EventSource`, since the request POSTs a JSON body) to parse the `token` / `sources` / `verification` / `meta` / `done` / `error` events, rendering the answer live with its citations, verdict and cost. The `/query/stream` endpoint runs the agent to completion (same MCP-first, direct-agent fallback and 120 s timeout as `/query`, env-configurable via `AGENT_TIMEOUT_SECONDS`), then streams the final answer word-by-word — chosen over `astream_events` because isolating only the final-answer tokens across the tool-calling loop proved brittle.

---

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangChain, LangGraph (`create_react_agent`) |
| Vector store | ChromaDB (embedded, persistent SQLite + binary index) |
| Sparse index | `rank-bm25` over the same chunks, built from Chroma on first use (about a minute) and pickled under `data/`; used by the `bm25` and `hybrid` modes, both off by default |
| Embeddings | HuggingFace `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim) |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` (local CPU, ~90 MB), switched on by `RETRIEVAL_RERANK` |
| Structured facts | Inline XBRL parsed from the same filings into SQLite (`data/facts.sqlite`, 6,089 facts, built in ~3 s) — see [docs/adr/0001](docs/adr/0001-sqlite-for-xbrl-facts.md) |
| LLM | Google Gemini, set by `LLM_MODEL`. Default `gemini-3.1-flash-lite` across the agent, MCP agent and query engine |
| Tool protocol | Model Context Protocol (MCP), streamable-HTTP transport |
| API | FastAPI + Uvicorn |
| Frontend | Next.js (App Router) + TypeScript + Tailwind CSS |
| Streaming | Server-Sent Events over `POST /query/stream` (fetch + ReadableStream) |
| Backend tests | pytest — 222 tests, no network / API key / index required |
| Frontend tests | Vitest + React Testing Library — 3 tests |
| Packaging | Docker, docker-compose |
| Evaluation | RAGAS 0.4.3 (faithfulness, answer_relevancy, context_recall) scored per chunk, plus judge-free retrieval metrics (hit/recall@k, MRR, nDCG@5 against labelled chunks) and a ground-truth figure check — see [eval/EVALUATION.md](eval/EVALUATION.md) |
| Verification | Output contract: every answer becomes claims with cited observation ids and is checked against what was retrieved this turn, with no model in the check; fail-closed (`agent/contract.py`) |
| Hosting | Vercel (frontend) + Hugging Face Spaces (backend), both free tier |
| CI | GitHub Actions: lint, tests, build, and a retrieval quality gate on every PR (thresholds in `eval/ci_gate.json`, scored on the index rebuilt from the committed filings); the judged run is a manual, paid workflow |

---

## Quick start — local

```bash
# 1. Create and activate a Python 3.11 env
conda create -n fra python=3.11 -y
conda activate fra

# 2. Install dependencies (CPU-only PyTorch wheel)
pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

# 3. Configure secrets
cp .env.example .env
# then edit .env and set GEMINI_API_KEY=...

# 4. Build the vector index (first run only)
#    Reads the 10-K filings already committed under data/sec_filings/ — no SEC
#    download needed. Cleans each submission to its 10-K document and embeds the
#    4,783 chunks locally on CPU: 72 s on a 12-core laptop, 29 MB on disk. Only
#    re-run this if data/chroma_db/ is missing.
python -m ingestion.pipeline

# 5. Run the agent on its built-in smoke question (no arguments; edit
#    test_question in agent/financial_agent.py to ask something else)
python -m agent.financial_agent
# or, for a single-shot RAG query without the agent loop:
python -m retrieval.query_engine "What was Apple's total revenue in fiscal 2025?"
```

Get a free Gemini API key at <https://aistudio.google.com/app/apikey>.

### Retrieval switches

Every retrieval the agent's tools make goes through
[retrieval/retriever.py](retrieval/retriever.py), configured once per process
from `RETRIEVAL_*` environment variables (documented in
[.env.example](.env.example)). Unset, it is dense top-5 search — the code
default, byte for byte what the system shipped with before the ablation. The
measured configuration (reranker plus inferred ticker filter) is what the
deployment runs with the variables set; the tables below label it "measured
configuration". Each switch was measured on its own and in
combination with `python -m eval.run_retrieval_eval` before anything was
turned on; the matrix is in [eval/EVALUATION.md](eval/EVALUATION.md).

| Variable | Values | What it does |
|---|---|---|
| `RETRIEVAL_MODE` | `dense` (default), `bm25`, `hybrid` | Dense similarity, exact-token BM25, or both fused with Reciprocal Rank Fusion |
| `RETRIEVAL_RERANK` | `false` (default), `true` | Re-order `RETRIEVAL_FETCH_K` candidates with a CPU cross-encoder, keep the top k |
| `RETRIEVAL_FETCH_K` | integer, default `25` | Candidates per source before fusion or reranking |
| `RETRIEVAL_K` | integer, default `5` | Chunks returned per search call (the k the tools and the eval record) |
| `RETRIEVAL_TICKER_FILTER` | `none` (default), `inferred` | Restrict search to the one company the question names; no restriction when it names zero or several |
| `RETRIEVAL_RRF_K`, `RETRIEVAL_DENSE_WEIGHT`, `RETRIEVAL_SPARSE_WEIGHT` | | Hybrid fusion knobs |

### Structured facts: exact figures and a calculator

Every headline number in a 10-K is machine-tagged inside the filing (inline
XBRL) with its US-GAAP concept, period, unit and segment. The ingestion
pipeline parses those tags from the same submissions it chunks
([ingestion/xbrl.py](ingestion/xbrl.py)) into `data/facts.sqlite`, and the
agent gets two tools on top of it ([retrieval/facts.py](retrieval/facts.py)):

- **`lookup_financial_fact(ticker, concept, fiscal_year, segment)`** — the
  tagged value for a plain-language concept ("total net sales", "diluted EPS",
  "Google Cloud revenue"), resolved through a synonym table and a name search
  that returns candidates rather than guessing. The fiscal year is a filter:
  unset means the most recent year in the filing, and the tool says so. Every
  result is cited like a passage (`AAPL_10K_fact_123`).
- **`compute_metric(operation, a, b, n=None)`** — difference, sum, ratio,
  percent change, margin and CAGR (`n` years) in Python, with the formula
  shown. The model picks the operands; it never does the arithmetic.

The system prompt sends figure questions to the fact tool first and
narrative questions to search. Build the table on its own with:

```bash
python -m ingestion.xbrl
```

---

## Quick start — Docker

```bash
docker-compose up --build
```

This starts two services sharing the `./data` volume:

- `mcp-server` on port **8000** (MCP streamable-HTTP endpoint)
- `api-server` on port **8080** (FastAPI)

The first build is slow: CPU PyTorch and the transformer models download, and the image
re-embeds the 4,783 chunks (about a minute on a laptop CPU). Subsequent builds are
cached. Compose bind-mounts `./data` over `/app/data`, so the index the container serves
is the one on the host, not the one baked into the image.

If `data/chroma_db/` is empty, run the ingestion pipeline inside the container:

```bash
docker exec financial-research-agent-mcp-server-1 python -m ingestion.pipeline
```

---

## API usage

### `GET /health`

Returns service status and whether the MCP backend is reachable. `mcp_server` is
a boolean, not a status string — it is a 2-second TCP connect to `MCP_SERVER_URL`.

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "healthy",
  "mcp_server": false
}
```

`false` is what the deployed Space returns: it runs a single container with no
MCP server, so every request uses the in-process agent. Running the full
`docker-compose` stack locally returns `true`.

### `POST /query`

Send a natural-language question, get a grounded answer with source citations.

```bash
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What was Apple total revenue in fiscal year 2025?"}'
```

```json
{
  "answer": "Apple's total net sales for the 2025 fiscal year were $416,161 million.",
  "sources": [
    {
      "text": "Apple Inc. | 2025 Form 10-K | 35 The following table shows d ...",
      "ticker": "AAPL",
      "source_file": "AAPL_10K_chunk_395",
      "cited": true
    }
  ],
  "tokens_used": 5080,
  "meta": {
    "trace_id": "0a1b2c3d-...", "latency_ms": 4639.2, "llm_calls": 3,
    "input_tokens": 4900, "output_tokens": 180, "cost_usd": 0.001495,
    "tools": {"lookup_financial_fact": 1}, "tool_ms_total": 210.4, "backend": "direct"
  },
  "verification": {
    "status": "verified", "mode": "strict",
    "n_claims": 1, "n_figures": 1, "n_supported": 1,
    "failures": [], "cited": ["AAPL_10K_chunk_395"], "not_disclosed": false,
    "attempts": 1, "repaired": false, "contract_version": "da6f5bea0c8b"
  }
}
```

Each source is `{text, ticker, source_file, cited}` — the chunk index is encoded
in `source_file` as `TICKER_10K_chunk_N` (a tagged XBRL fact is
`TICKER_10K_fact_N`), the SSE endpoint splits it out into a separate
`chunk_idx` field, and `cited` is true when a claim in the verified record
cites that observation. Which agent served the request is in `meta.backend`.

`verification` is the output contract's verdict (see "Verified answers"
below): `status` is `verified`, `unverified`, `refused` or `skipped`; `mode`
is the `VERIFY_MODE` it ran under; `failures` names each check that failed
(`unknown_source`, `uncited_figure`, `unsupported_figure`, `dropped_figure`,
`empty_record`, `no_contract`) with the sentence and the figure; `cited`
lists the observation ids the record rests on, and each source's `cited`
flag is set from it only when the status is `verified`; `not_disclosed` is
the record's flag for "the filings do not say"; `attempts` and `repaired`
say whether the one repair was needed; `contract_version` hashes the
structuring prompt and schema. A provider's or parser's error message never
appears in the payload. When the status is `refused`, `answer` is a refusal
that says what could not be verified, never the draft.

Optional `ticker` field appends the company to the question (`… (company: AAPL)`); with
`RETRIEVAL_TICKER_FILTER=inferred` the retriever then restricts every search to it.

`meta` is the request's own meter, present on every successful answer:

```json
"meta": {
  "trace_id": "0a1b2c3d-...",
  "latency_ms": 6812.4,
  "llm_calls": 3,
  "input_tokens": 9120,
  "output_tokens": 210,
  "cost_usd": 0.002595,
  "tools": {"lookup_financial_fact": 2, "compute_metric": 1},
  "tool_ms_total": 1420.6,
  "backend": "direct"
}
```

`cost_usd` is computed from the repo's own dated price table
([agent/pricing.py](agent/pricing.py)), never read from a vendor dashboard,
and is `null` for a model the table does not know. `trace_id` is the run id
LangSmith shows as the trace when `LANGSMITH_TRACING=true`. `tokens_used`
is the sum of the two token counts.

### `POST /query/stream`

Same request body as `/query`, but streams the answer as Server-Sent Events
(`text/event-stream`) — this is what the web UI consumes.

```bash
curl -N -X POST http://localhost:8080/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "What were Apple total net sales in the most recent fiscal year?", "ticker": "AAPL"}'
```

Each line is one JSON event: `{"type":"token","text":...}` (repeated), then
`{"type":"sources","items":[{"ticker","chunk_idx","source","cited"}]}` (no
`text`; a tagged fact's `chunk_idx` is `"fact N"`), then
`{"type":"verification", ...}` with the same fields as `/query`'s
`verification`, then `{"type":"meta", ...}` with the same fields as
`/query`'s `meta`, then `{"type":"done"}` — or `{"type":"error","message":...}`,
which carries an `outcome` (`empty_answer`, `recursion_limit`) when the agent
finished without a usable answer. While the agent runs, the stream sends a
`: keepalive` comment line every 10 s so an idle proxy does not drop it. The chat UI renders the verdict as a
line above the sources (cited sources carry a check mark, a withheld answer
is marked as such) and `meta` under each answer as seconds, tokens, dollars,
tools called and the trace id. CORS origins are controlled by the
`FRONTEND_ORIGINS` env var (comma-separated; defaults include `http://localhost:3000`).

---

## Observability

Two layers, kept deliberately separate.

**Traces: LangSmith.** The agent is LangGraph, so setting three environment
variables traces every run with no code: `LANGSMITH_TRACING=true`,
`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`. Every tool call, every model call
and every retrieved passage is visible per run, and the `trace_id` the API
returns is the id to open. Traces are on for the deployed demo; tests force
tracing off, and the eval harness runs with it off unless a run is meant to
be inspected (the cost run below used a dedicated project).

**Numbers: our own meter.** [agent/meter.py](agent/meter.py) is a callback
attached to every run, in the API and in the eval harness alike. It records
wall-clock latency, model calls, tokens in and out, cost at a dated price
table the repo owns, each tool call with its duration, and the root run id.
Those numbers go into every results record and every API response, so a
figure in [eval/EVALUATION.md](eval/EVALUATION.md) can be recomputed from
the committed file and does not move when a vendor changes its cost table.
The eval harness also records what each judge pass cost.

Measured on the shipped configuration over the 71-item benchmark
(EVALUATION.md, "Cost and latency"): latency p50 **1.9 s**, p95 5.9 s;
6,199 tokens in and 94 out per query; **$0.0017 per query**,
$0.12 for the whole benchmark. A judge pass over the same answers costs about
$1.10, which is why evaluation spend is gated on the judge-free metrics first.

---

## Verified answers

The agent's final message is prose, and prose is not checkable. So before an
answer leaves the API it is turned into a record — one claim per sentence,
each with the ids of the observations that support it — by a second, cheaper
model call that sees the question, the draft and the observations with their
ids ([agent/contract.py](agent/contract.py)). The record is then checked with
no model in the loop:

| check | fails when |
|---|---|
| `unknown_source` | a cited id is not an observation from this turn |
| `uncited_figure` | a sentence states a figure and cites nothing |
| `unsupported_figure` | a figure is not in any observation the sentence cites |
| `dropped_figure` | a figure in the draft appears in no sentence of the record |

A figure counts as present under the same rules the evaluation's figure check
uses ([agent/figures.py](agent/figures.py)), read the way a verifier needs
them: a claim may round its source ("$26.4 billion" for "$26,448 million") but
may not be more precise than it, a bare integer must match exactly, and a
percentage may be written without its sign in the source (the calculator
prints `= 19.68`). Years are exempt; a wrong-year figure is the figure check's
and the fact tool's job, not this one's.

**Fail-closed.** One repair attempt — the structuring model is shown the
failures — then the answer is refused. `VERIFY_MODE=strict` (default): a
refused answer is served as a refusal that names what could not be verified,
HTTP 200, `verification.status = "refused"`. `warn`: the draft is served with
the verdict attached. `off`: no structuring call, no check — the behaviour
before the contract existed, kept for cost comparison. The structuring calls
are metered with the agent's own, so `meta.cost_usd` is the cost of the
answer served. Measured over all 71 benchmark items: **70 of 70** answers
verified on the first attempt, 77 of 77 figures supported by a cited
observation, 0 refused, drafts unchanged; +$0.0004 and +2.2 s per query
([eval/EVALUATION.md](eval/EVALUATION.md), "Output contract").

---

## Evaluation

71 labelled questions over five FY2025 10-K filings, seven question types. Two
instruments: RAGAS judge metrics on the agent's answers, and judge-free metrics
that need no LLM call at all — retrieval scored against the chunks that hold
each item's reference passage, and a check that the answer contains every
ground-truth figure. **Full method, findings and limitations:
[eval/EVALUATION.md](eval/EVALUATION.md).** Per-item evidence — every answer,
every retrieved chunk, every score — is committed under
[eval/results/](eval/results/), and every run lands on
[eval/results/LEADERBOARD.md](eval/results/LEADERBOARD.md) with a config hash.

### Retrieval, measured on its own (no LLM)

The benchmark question goes straight to the retriever; the top 25 chunk ids
are scored against the labelled chunks. 71 items, under a minute per
configuration, no API call. Nineteen configurations were measured this way
(the full matrix and findings 16–17 are in EVALUATION.md); the two that matter:

| | hit@5 | recall@5 | MRR | nDCG@5 | recall@25 | p50 latency |
|---|---|---|---|---|---|---|
| dense top-5, as originally shipped | 0.507 | 0.489 | 0.358 | 0.369 | 0.725 | 13.6 ms |
| **dense + cross-encoder rerank over 50 + inferred ticker filter** (the measured configuration; on with the `RETRIEVAL_*` variables) | **0.662** | **0.630** | **0.541** | **0.546** | **0.771** | 751 ms |
| the same, table items only (n = 27) | 0.481 | | | | | |
| the same, non-table items (n = 44) | 0.773 | | | | | |

Numbers are on the index rebuilt after review (finding 22: three-quarters
of the first index was XBRL markup, identifiers and metadata). Hybrid BM25 + dense fusion is the best
unreranked retriever on the clean index (0.592) and loses to reranked
dense candidates (0.634 against 0.662), so it is kept in the code, off by
default. Labels mark the
cited reference passage, not every chunk stating the fact, so these are lower
bounds (EVALUATION.md finding 13). Before the reranker, the agent's own query
wording found a labelled chunk on 43.7% of items (`agent_hit_rate`), below
the 50.7% the plain question achieved.

### Figure check (no LLM)

Two views of whether the answer got the number right, both with years
matched exactly and a candidate accepted when it rounds to the ground truth
at the precision the ground truth was written in. `figure_primary`: the
answer contains the figure the question asked for. `figure_exact`: it
contains every figure in the ground truth, context included. On the 45 items
whose ground truth carries a figure other than a year, the dense baseline scores **73.3%** and
**60.0%**. This is the check that scores a right-figure-wrong-year answer as
a failure where faithfulness scored it 1.00 (findings 14 and 18).

### Answer quality, schema 3 (contexts scored per chunk, with provenance)

The cached `gemini-3.1-flash-lite` answers for all 71 items, re-judged by
`gemini-3.6-flash` with each retrieved chunk as its own context. Terminal
failures (7, all recursion-limit) stay in the mean, scored on the placeholder
text they returned, never excluded. Evidence and per-stratum rows:
[baseline-v3-aea128d62403.json](eval/results/baseline-v3-aea128d62403.json).

| n | faithfulness | answer relevancy | context recall | figure_exact | agent_hit |
|---|---|---|---|---|---|
| 71 | **0.826** | 0.764 | 0.718 | 0.600 (n = 45) | 0.437 |

On the 61 answers byte-identical with the schema-2 run below, the new instrument
returned the same `context_recall` on every item and faithfulness within two
items of the old score. It also found that stripping the `[META 10-K, chunk
412]` header from a context costs 0.12 faithfulness on the same 71 answers
(0.826 with the header, 0.704 without),
because the chunk text says "the Company" and only the header says which one
(EVALUATION.md finding 15). This is the baseline every retrieval change is
compared against.

### The reranker inside the agent

The full benchmark regenerated with the shipped retrieval configuration
switched on, same model, same prompt, compared on the metrics that need no
judge:

| | dense top-5 (baseline) | dense + rerank 50 + inferred ticker | change |
|---|---|---|---|
| `figure_primary` (n = 45) | 0.733 | **0.911** | +0.18 |
| `figure_exact` (n = 45) | 0.600 | **0.844** | +0.24 |
| `agent_hit` (n = 71) | 0.437 | **0.662** | +0.23 |
| terminal failures (recursion limit) | 7 | **1** | −6 |

All seven recursion-limit failures in the baseline now answer, every one
with a figure to get right getting it right: the agent was searching without
finding until its step budget ran out; one new item (`qa_0063`) hit the limit
instead, hence −6 net. The `temporal` items did not improve — `figure_exact`
2 of 5 both times, and `figure_primary` fell from 4 of 5 to 2 of 5 — the model
still quotes the prior year when the year is unstated, a period-selection
problem, not a retrieval one; the fact tools close it in the next section. Judge-scored on the same answers: faithfulness 0.826 → **0.931**, answer relevancy 0.764 → 0.869, context recall 0.718 → 0.887. Evidence: [rerank-v3-764b3da65d36.json](eval/results/rerank-v3-764b3da65d36.json).

### The fact tools inside the agent

All 71 items regenerated with `lookup_financial_fact` and `compute_metric`
available on top of the reranked retrieval, judged the same way:

| | + reranker | + reranker + fact tools |
|---|---|---|
| `figure_primary` — the figure the question asked for (n = 45) | 0.911 | **1.000** |
| `figure_exact` — every ground-truth figure (n = 45) | 0.844 | **0.889** |
| faithfulness | 0.931 | **0.957** |
| answer relevancy | 0.869 | **0.894** |
| context recall | 0.887 | 0.852 |
| terminal failures | 1 | 1 |

The fact tool was used on 34 items and the calculator on 8. The `temporal`
items, which retrieval could not move, now all name the year asked: the
fiscal year is an argument to a lookup, not a column the model picks. On
the items that used the fact tool faithfulness is 0.984. Context recall
fell on those same items because the metric was built for passages and is
now handed fact rows (EVALUATION.md limitation 16). Evidence:
[facts-v3-70db17ff5e31.json](eval/results/facts-v3-70db17ff5e31.json).

### Answer quality, schema 2 (contexts scored as observation blobs)

The two runs below predate the per-chunk instrument and are kept as measured.
Same 66 items, **agent model the only variable**. Terminal failures (empty
answer or recursion limit) are counted as 0 in both, not excluded. Their
`context_recall` scored each tool observation as one blob of five passages
and is not comparable with schema-3 runs.

| | agent `gemini-2.5-flash-lite` | agent `gemini-3.1-flash-lite` | change |
|---|---|---|---|
| faithfulness | 0.7136 | **0.8813** | +0.1677 |
| answer relevancy | 0.5547 | **0.7625** | +0.2078 |
| context recall | 0.5152 | **0.6970** | +0.1818 |
| terminal failures | **12 / 66** (10 empty, 2 recursion) | **6 / 66** (0 empty, 6 recursion) | |

Judge `gemini-3.6-flash`, k=5, prompt `sha256:d1bedac20eb2`, RAGAS 0.4.3. Metric
coverage 100% on the after run; the before run's ten empty answers scored NaN
(84.8% coverage) and are counted as 0 above.

**What the numbers hide, and why the evaluation record matters more than the
table.** On the earlier model, 10 of 66 items returned an *empty* answer — and
RAGAS scored those `NaN` and then dropped them from the mean, reporting
faithfulness as 0.8411 instead of 0.7136. The system's worst items were improving
its score. The API served them as HTTP 200 and the streaming endpoint rendered
them as blank messages with source citations attached. That is now a named
terminal-failure state guarded at every layer, with 33 tests.

Four of six question-type strata are n ≤ 8 and `multi_hop` is a single item, so
the per-type breakdown in EVALUATION.md is anecdote, not measurement. n = 66
establishes no statistical significance.

### Metrics

Judge-scored (RAGAS, per chunk from schema 3):

- **faithfulness** — every claim in the answer must be grounded in a retrieved passage
- **answer_relevancy** — does the answer actually address the question
- **context_recall** — did the retrieved chunks contain what the ground-truth answer depends on

Judge-free (deterministic, no API call):

- **hit@k / recall@k / MRR / nDCG@5** — the retriever alone, against labelled chunks (`eval/benchmark_chunks.json`)
- **agent_hit_rate** — a labelled chunk appeared anywhere in the agent's own tool observations
- **figure_exact / figure_recall** — ground-truth figures present in the answer; years must match exactly

### Run it

```bash
# No LLM calls at all — validates benchmark parsing, labels, argparse and imports. This is CI.
python -m eval.run_eval --dry-run
python -m eval.run_retrieval_eval --dry-run

# Retriever alone, no LLM calls (~30 s). Writes eval/results/retrieval-<label>-<hash>.json.
python -m eval.run_retrieval_eval --label dense

# Rebuild the chunk labels from the local index (only after the benchmark or index changes).
python -m eval.chunk_labels

# The 5-item smoke benchmark, end to end.
python -m eval.run_eval --smoke --judge-provider groq

# Re-score the cached answers per chunk (schema 3) — zero generation calls.
# The cache key holds the prompt hash and, unless VERIFY_MODE=off, the output
# contract's tag; the baseline answers were cached under the pre-facts prompt,
# so this reproduces at the commit the results file records (`git_commit`).
VERIFY_MODE=off LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval --score-only --judge-provider google --judge-model gemini-3.6-flash --label baseline-v3

# A full generation + judge run.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval --judge-provider google --judge-model gemini-3.6-flash --label rerun

# Cross-family re-score from cache — zero generation calls (same caveat).
VERIFY_MODE=off LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval --score-only --judge-provider groq --label crossjudge

# Regenerate the leaderboard from every results file.
python -m eval.leaderboard

# What the index holds, class by class (finding 22), and the CI quality gate
# against the live index; then the judged smoke thresholds next to their calibration values.
python -m ingestion.audit
python -m eval.ci_gate retrieval
python -m eval.ci_gate calibrate
```

Every run records a hash of its resolved configuration. A configuration that
already has a complete results file is reported, not re-run, unless `--force`
is passed.

The run is **checkpointed and resumable**, keyed on
`(item id, agent model, prompt version)` plus the retrieval configuration and
the output contract's mode and version when they differ from the defaults, so
a quota wall costs one item rather
than a run. `--score-only` re-judges cached answers with a different judge at zero
generation cost. A partial run **withholds aggregates** — in stdout and in the
JSON — so a stopped run cannot be mistaken for a finished one.

### Judge configuration

The agent model comes from `LLM_MODEL`. The judge is selected explicitly:

| | Provider | Model var | Key var |
|---|---|---|---|
| `--judge-provider google` | Google | `RAGAS_LLM_MODEL` | `GEMINI_API_KEY` |
| `--judge-provider groq` | Groq | `RAGAS_JUDGE_MODEL` | `RAGAS_JUDGE_API_KEY` |

Credentials are read from the environment only, never accepted as flags. A
cross-family judge check (Groq `gpt-oss-120b` re-scoring the same outputs) was
run once, on 20 items of the schema-2 run — see EVALUATION.md finding 4 for
what it did and did not show; the schema-3 runs are Gemini-judged only.

---

## Continuous integration

Two workflows. `.github/workflows/ci.yml` runs on every push and PR to
`main`/`master` as three parallel jobs, none of which needs a secret:

**Backend (`build`)**
1. Install `requirements.txt` (CPU PyTorch extra index)
2. `flake8 .` with `--max-line-length 120 --ignore E501,W503`
3. `python -m eval.run_eval --dry-run`, `python -m eval.run_retrieval_eval --dry-run`
   and `python -m eval.ci_gate retrieval --dry-run` — the last one checks that
   every threshold in `eval/ci_gate.json` sits at or below the committed value
   it was set from, so the gate cannot be edited past what was measured
4. `pytest` (222 tests; no zero-test escape hatch — a vanished suite fails the build)

**Retrieval quality gate (`retrieval-gate`)**
1. `python -m ingestion.pipeline`: rebuild the index and the fact table from
   the committed filings — about a minute of embedding, cached on the
   filings' and the ingestion code's hash
2. `python -m eval.ci_gate retrieval`: the dense baseline and the shipped
   configuration scored against the 71 labelled items with the retriever-only
   runner (no LLM call). Seven thresholds: each hit rate at or below two
   items under the committed value, MRR and nDCG 0.03 under it, so a one- or
   two-item loss passes and a three-item loss fails; the PASS/FAIL table lands
   on the run's summary page and the per-item results are uploaded as a build
   artefact (EVALUATION.md, "CI quality gate")

**Frontend (`frontend`, in `web/`)**
1. `npm ci`
2. `npm run lint`
3. `npm test` (3 Vitest tests on the chat page's handling of streamed events, SSE client mocked; Node 22 — vitest 4 requires >=20.19)
4. `npm run build`

`.github/workflows/eval-judged.yml` is the paid half, run by hand from the
Actions tab and never on a push or a schedule: the agent and the judge over
ten fixed benchmark items on the same rebuilt index, thresholds calibrated from the
committed judged run over those items, about 60 judge calls and $0.20 per
run. It reads `GEMINI_API_KEY` (and `RAGAS_JUDGE_API_KEY` for the free Groq
cross-family judge option) from repository secrets; an incomplete run, or one
whose agent model, retrieval configuration or item set differs from the
calibration, fails the gate regardless of score.

---

## Deploy (free)

The whole stack runs on free tiers:

- **Frontend → Vercel (Hobby).** Import the repo, set **Root Directory** to `web/`, and set `NEXT_PUBLIC_API_BASE_URL` to the backend URL.
- **Backend → Hugging Face Spaces (Docker SDK).** Set `GEMINI_API_KEY` and `FRONTEND_ORIGINS` as Space secrets. The Space's own Dockerfile sets the measured retrieval configuration (`RETRIEVAL_RERANK=true`, `RETRIEVAL_FETCH_K=50`, `RETRIEVAL_TICKER_FILTER=inferred`) as image environment, pre-downloads the cross-encoder (~90 MB) at build time, and runs `python -m ingestion.xbrl` at build time (about three seconds) to create `data/facts.sqlite` from the LFS-shipped filings; Space variables of the same names override the image defaults. Reranking adds roughly a second of CPU per retrieval call on the free tier's two cores, and verification adds a second model call per answer.

  Note that this repo's [Dockerfile](Dockerfile) and the one in the deployed Space differ deliberately. Here, the image **rebuilds** the Chroma index at build time from the committed filings under `data/sec_filings/` using local MiniLM embeddings, so the index never has to live in git. Re-embedding the old 67,521-chunk index exceeded Hugging Face's build timeout on the free CPU builder, so the Space **ships a prebuilt index via Git LFS** and skips the rebuild; the rebuilt index (29 MB, about a minute to embed) would fit a build step, and moving the Space to that is a separate change.

**If the Space build fails with `exit code 128`.** The build job dies at the git/LFS stage before any Docker step runs — the build log shows `Build Queued` and nothing after — and the Space serves HTTP 503 until it is fixed. Observed three times during development.

Pushing an empty commit to retry sometimes clears it and sometimes does not. What reliably works is a **factory rebuild**, which discards the build cache:

- In the Space UI: **Settings → Factory rebuild**
- Or via the API with a write token:

```bash
curl -X POST -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/spaces/<user>/<space>/restart?factory=true"
```

This is worth knowing before sending anyone the demo link: a plain retry can leave it down, and the fix is not obvious from the error. `git lfs fsck` locally and Space storage were both clean each time, so it appears to be build-cache flakiness rather than repository corruption.

---

## Project structure

```
financial-research-agent/
├── .github/workflows/
│   ├── ci.yml                      # Every PR: lint, dry-runs, pytest, retrieval quality gate, frontend
│   └── eval-judged.yml             # Manual: agent + judge on 10 items, judged thresholds (~$0.20)
├── agent/
│   ├── financial_agent.py          # Direct in-process ReAct agent
│   ├── mcp_agent.py                # Same ReAct loop, tools sourced from MCP
│   ├── observations.py             # Tool-output parser + canonical chunk id, shared by API and eval
│   ├── contract.py                 # Output contract: claims + cited ids, deterministic checks, repair, refuse
│   ├── figures.py                  # What a figure is and when two match, shared by the verifier and the eval
│   ├── meter.py                    # Per-run latency, tokens, cost, tool timings, trace id
│   └── pricing.py                  # Dated price table behind every cost figure
├── api/
│   └── main.py                     # FastAPI app, MCP-first + direct fallback
├── data/
│   ├── sec_filings/                # The five FY2025 10-K submissions, committed (84 MB)
│   ├── chroma_db/                  # Persistent vector index (gitignored; rebuilt by the pipeline)
│   └── facts.sqlite                # Tagged XBRL facts (gitignored; built by the pipeline or ingestion.xbrl)
├── eval/
│   ├── benchmark.csv               # 71 labelled Q&A rows (full benchmark)
│   ├── benchmark_smoke.csv         # 5 of those rows, for --dry-run and CI
│   ├── benchmark_chunks.json       # Which index chunks hold each item's reference passage
│   ├── chunk_labels.py             # Builds benchmark_chunks.json from the index (+ overrides)
│   ├── chunk_labels_overrides.json # The 3 hand-resolved labels, with reasons
│   ├── run_eval.py                 # RAGAS harness (resumable, checkpointed, schema 3)
│   ├── run_retrieval_eval.py       # Retriever-only metrics, no LLM calls
│   ├── retrieval_metrics.py        # hit/recall@k, MRR, nDCG over relevance groups
│   ├── figure_match.py             # Ground-truth figures reproduced in the answer
│   ├── experiment.py               # Config hash, benchmark version, prior-run lookup
│   ├── leaderboard.py              # Regenerates results/LEADERBOARD.md
│   ├── ci_gate.py                  # The quality gate: thresholds vs measured, PASS/FAIL, step summary
│   ├── ci_gate.json                # Thresholds, each naming the committed file it was set from
│   ├── ablation.py                 # Renders the retrieval ablation matrix from results files
│   ├── recompute_deterministic.py  # Re-derives the judge-free metrics on any results file
│   ├── EVALUATION.md               # Method, findings, limitations
│   ├── results/                    # Committed per-item evidence + LEADERBOARD.md (tracked)
│   └── cache/                      # Agent-output cache (gitignored)
├── ingestion/
│   ├── downloader.py               # SEC EDGAR fetcher
│   ├── submission.py               # The EDGAR envelope: which <DOCUMENT> is the 10-K
│   ├── cleaner.py                  # 10-K document only → plain text (finding 22)
│   ├── audit.py                    # What the index holds, class by class
│   ├── chunker.py                  # Recursive splitter, 512 chars / 50 overlap
│   ├── embedder.py                 # MiniLM → ChromaDB upsert
│   ├── xbrl.py                     # Inline XBRL → data/facts.sqlite (6,089 tagged facts)
│   └── pipeline.py                 # locate the committed filing → clean → chunk → embed → facts
├── mcp_server/
│   └── server.py                   # FastMCP server, streamable-HTTP transport
├── retrieval/
│   ├── query_engine.py             # Single-shot RAG (no agent loop)
│   ├── retriever.py                # The one retrieval call, behind RetrievalConfig
│   ├── sparse.py                   # BM25 over the same chunks (bm25 / hybrid modes)
│   ├── rerank.py                   # Cross-encoder reranker
│   ├── tickers.py                  # Which company a question names (the inferred filter)
│   └── facts.py                    # Fact lookup, concept/segment resolution, calculator
├── tests/                          # 222 tests; no network, key or index needed
│   ├── test_ingestion.py           # chunker, cleaner, embedder (32)
│   ├── test_submission_audit.py    # the EDGAR envelope, 10-K isolation, the audit classifier (6)
│   ├── test_retrieval.py           # query_engine retrieval + prompt path (18)
│   ├── test_terminal_failures.py   # empty-answer / recursion-limit guard (33)
│   ├── test_query_stream.py        # SSE streaming contract (2)
│   ├── test_query_meta.py          # the meter on /query and the meta stream event (2)
│   ├── test_meter.py               # latency, tokens, cost, tool timings, trace id (6)
│   ├── test_eval_instrument.py     # observation parser, retrieval metrics, figure check, labels, leaderboard (27)
│   ├── test_eval_harness.py        # per-chunk contexts, cache upgrade, the generate / cache / stop loop (12)
│   ├── test_retriever.py           # fusion, BM25, ticker inference, retrieval switches (13)
│   ├── test_tool_wiring.py         # tool observations round-trip through the parser (4)
│   ├── test_ablation.py            # ablation matrix rendering (2)
│   ├── test_facts.py               # inline XBRL parser, fact store, resolver, calculator, segment filter (13)
│   ├── test_facts_wiring.py        # fact/calc observations, API citations, the two tools (8)
│   ├── test_ci_gate.py             # gate thresholds and judged checks (14)
│   ├── test_contract.py            # the checks by name, repair then refuse, API verdict, harness scoring (22)
│   ├── test_api_edges.py           # question bound, one deadline, stream cancellation (4)
│   └── test_mcp_contract.py        # the MCP server in-process: discovery, schemas, observation format (4)
├── web/                            # Next.js chat client (see web/README.md)
├── docs/
│   ├── DECISIONS.md                # Every design decision with its evidence
│   └── adr/                        # Architecture decision records
├── LICENSE                         # MIT
├── conftest.py                     # Test setup: tracing and verification forced off
├── SECURITY.md
├── ROADMAP.md                      # Seven upgrades done, three next steps, each from a finding
├── docker-compose.yml              # api-server + mcp-server
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Known limitations

- **Table chunking.** The recursive character splitter breaks 10-K tables across chunk boundaries, so numeric questions that depend on multi-row context (e.g. segment breakdowns) can retrieve partial rows. A dedicated table-aware splitter (or a layout-preserving parser like Unstructured) would close this gap.
- **n = 71 for the headline runs (66 for the before/after model comparison) establishes no statistical significance**, and five of seven question-type strata are n ≤ 8 (`multi_hop` is a single item in the whole benchmark). The per-type breakdown is directional at best. Free-tier quota previously capped the reported run at n = 8 — the Gemini free tier allows 20 requests per day, per model, per project, measured from live 429 bodies — and the harness is checkpointed and resumable because of it; see [eval/EVALUATION.md](eval/EVALUATION.md) findings 6 and 10.
- **The reported runs are same-family judged, and the cross-family check is thin.** Every judged run used a `gemini-3.6-flash` judge against a Gemini agent, so same-model-family bias applies to the headline numbers. A cross-family check was run — 20 of those items re-scored by Groq `openai/gpt-oss-120b` — and the two judges broadly agree (mean absolute divergence 0.025–0.061; agreement within 0.1 on 80–95% of items). The disagreement concentrates on the three `comparative` items, where the Gemini judge gave a flat 1.00 and the Groq judge 0.71–0.75. That is a signal worth acting on, not proof of bias: n = 3. See [eval/EVALUATION.md](eval/EVALUATION.md) finding 4.
- **`answer_relevancy` is not reproducible to the third decimal.** RAGAS overrides the judge's temperature to 0.3 for any metric requesting n > 1 generations, which `answer_relevancy` always does. `faithfulness` and `context_recall` are stable run-to-run; small `answer_relevancy` differences are noise.
- **Chunk labels mark the reference passage, not every passage that could answer.** The retrieval metrics score against the chunk(s) holding the passage the benchmark author cited. On 12 of 71 items the dense baseline produced every ground-truth figure without ever retrieving a labelled chunk, because the same fact appears elsewhere in the filing. Retrieval scores are therefore a lower bound, and the figure check is the metric that says whether the answer was right.
- **Results files before schema 3 scored `context_recall` over context blobs, not chunks.** Those files (`eval/results/*-<commit>.json`) are kept and listed separately on the leaderboard; their `context_recall` is not comparable with schema-3 runs.
- **Free-tier cold start.** The backend Space sleeps after inactivity; the first request after a sleep takes ~30–60 s to wake the container before answers stream. This is a demo-scale, single-user deployment — not sized for concurrent load.
- **Five dependency advisories remain open, and none has an upstream fix.** `npm audit` reports **0 vulnerabilities** — the `vitest` chain was cleared by moving to vitest 4 on Node 22, and every patched Python advisory (`langchain`, `langchain-text-splitters`, `langchain-openai`, `lxml`, `mcp`) has been taken. What is left is four ChromaDB advisories (2 critical, 2 high) and one `ragas` advisory, all of which have **no patched release published upstream**, so no version bump clears them. The ChromaDB pin is additionally verified to read the prebuilt index shipped in the deployed Space, so moving it would need an index-compatibility re-check rather than a routine bump.
- **Test coverage is real but not complete.** 222 backend tests plus 3 frontend Vitest tests. Covered: the chunker and cleaner (including the iXBRL-preamble heuristic), the embedder's batching and citation metadata, the retrieval query path and every retrieval switch, the fact store and calculator, the `/query` and `/query/stream` contracts including `meta` and `verification`, both terminal-failure states, list-shaped message content through every entry point that flattens it, the meter, the output contract's checks and repair-then-refuse loop, the CI gate, and the evaluation instrument (observation parsing, chunk labelling, retrieval metrics, the figure check, the cache upgrade and the leaderboard). The MCP server is tested in-process over the SDK's in-memory transport (discovery, schemas, observation format). Still untested: `ingestion/downloader.py` (network-bound) and `ingestion/pipeline.py` (the orchestration wrapper). The MCP path is also the one the deployed backend never exercises — `/health` reports `mcp_server: false` in production, so it runs the direct-agent fallback.
- **Chunked streaming, not per-token LLM streaming.** `/query/stream` runs the agent to completion and then streams the final answer word-by-word, rather than surfacing raw Gemini token deltas via `astream_events`. This trades true first-token latency for reliable isolation of only the final answer (the agent emits model-stream events on every tool-calling turn).

---

## License

MIT — see [LICENSE](LICENSE).
