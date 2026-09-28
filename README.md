# Financial Research Agent

An agentic RAG system that answers natural-language questions about SEC 10-K filings with source-grounded citations, exposed through both a FastAPI REST interface and a Model Context Protocol (MCP) server — with a streamed, full-stack Next.js chat UI on top.

---

## Live demo

- **App:** <https://financial-research-agent-pi.vercel.app>
- **Full stack:** `Next.js UI → SSE → FastAPI (/query/stream) → LangGraph ReAct agent → ChromaDB + Gemini`
- **Cold start:** the backend runs on a free tier and sleeps after inactivity — the **first request may take ~30–60 s** to wake the container, after which answers stream token-by-token. Please don't load-test the live link (Gemini free-tier RPM limits).
- **The deployed backend is a separate repository, redeployed deliberately.** It lives in
  its own Hugging Face Space repo rather than being built from this one on every push, so
  the two can drift. Its application code is currently **in sync with `main`** — same
  agent, API, retrieval and ingestion modules, same `gemini-3.1-flash-lite` default, same
  terminal-failure guard, same pinned dependencies. What deliberately differs is the
  deployment machinery: the Space ships a **prebuilt Chroma index via Git LFS**, because
  re-embedding 67K chunks at image-build time exceeds Hugging Face's build timeout on the
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
   │    ChromaDB      │   persistent vector store, 67,521 chunks
   │ (data/chroma_db) │   metadata: ticker, chunk_idx, source path
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │  ReAct Agent     │   LangGraph create_react_agent, tool-calling loop
   │ (agent/…)        │   tools: search_filings, list_companies, compare
   └────────┬─────────┘
            ▼
   ┌──────────────────┐       ┌──────────────────┐
   │  MCP Server      │◀──────│  MCP Agent       │   tools exposed over
   │ (streamable-HTTP)│       │ (adapter client) │   streamable-HTTP JSON-RPC
   └────────┬─────────┘       └────────┬─────────┘
            ▼                          ▼
   ┌────────────────────────────────────────────┐
   │           FastAPI (api/main.py)            │   POST /query, GET /health
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

The browser client uses `fetch` + `ReadableStream` (not `EventSource`, since the request POSTs a JSON body) to parse `token` / `sources` / `done` events, rendering the answer live with inline citations. The `/query/stream` endpoint runs the agent to completion (same MCP-first, direct-agent fallback and 120 s timeout as `/query`, env-configurable via `AGENT_TIMEOUT_SECONDS`), then streams the final answer word-by-word — chosen over `astream_events` because isolating only the final-answer tokens across the tool-calling loop proved brittle.

---

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangChain, LangGraph (`create_react_agent`) |
| Vector store | ChromaDB (embedded, persistent SQLite + binary index) |
| Sparse index | `rank-bm25` over the same chunks, built from Chroma in ~10 s, pickled under `data/` (hybrid mode only) |
| Embeddings | HuggingFace `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim) |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` (local CPU, ~90 MB), switched on by `RETRIEVAL_RERANK` |
| LLM | Google Gemini, set by `LLM_MODEL`. Default `gemini-3.1-flash-lite` across the agent, MCP agent and query engine |
| Tool protocol | Model Context Protocol (MCP), streamable-HTTP transport |
| API | FastAPI + Uvicorn |
| Frontend | Next.js (App Router) + TypeScript + Tailwind CSS |
| Streaming | Server-Sent Events over `POST /query/stream` (fetch + ReadableStream) |
| Backend tests | pytest — 117 tests, no network / API key / index required |
| Frontend tests | Vitest + React Testing Library — 2 tests |
| Packaging | Docker, docker-compose |
| Evaluation | RAGAS 0.4.3 (faithfulness, answer_relevancy, context_recall) scored per chunk, plus judge-free retrieval metrics (hit/recall@k, MRR, nDCG@5 against labelled chunks) and a ground-truth figure check — see [eval/EVALUATION.md](eval/EVALUATION.md) |
| Hosting | Vercel (frontend) + Hugging Face Spaces (backend), both free tier |
| CI | GitHub Actions (backend lint + dry-run, frontend lint + tests + build) |

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
#    download needed. Embeds 67,521 chunks locally on CPU. Measured on a cold
#    clone: 25 minutes, 67,521 chunks, ~370 MB on disk. Only re-run this if
#    data/chroma_db/ is missing.
python -m ingestion.pipeline

# 5. Ask the agent a question
python -m agent.financial_agent
# or, for a single-shot RAG query without the agent loop:
python -m retrieval.query_engine "What was Apple's total revenue in fiscal 2025?"
```

Get a free Gemini API key at <https://aistudio.google.com/app/apikey>.

### Retrieval switches

Every retrieval the agent's tools make goes through
[retrieval/retriever.py](retrieval/retriever.py), configured once per process
from `RETRIEVAL_*` environment variables (documented in
[.env.example](.env.example)). Unset, it is dense top-5 search — the shipped
behaviour, byte for byte. Each switch was measured on its own and in
combination with `python -m eval.run_retrieval_eval` before anything was
turned on; the matrix is in [eval/EVALUATION.md](eval/EVALUATION.md).

| Variable | Values | What it does |
|---|---|---|
| `RETRIEVAL_MODE` | `dense` (default), `bm25`, `hybrid` | Dense similarity, exact-token BM25, or both fused with Reciprocal Rank Fusion |
| `RETRIEVAL_RERANK` | `false` (default), `true` | Re-order `RETRIEVAL_FETCH_K` candidates with a CPU cross-encoder, keep the top k |
| `RETRIEVAL_FETCH_K` | integer, default `25` | Candidates per source before fusion or reranking |
| `RETRIEVAL_TICKER_FILTER` | `none` (default), `inferred` | Restrict search to the one company the question names; no restriction when it names zero or several |
| `RETRIEVAL_RRF_K`, `RETRIEVAL_DENSE_WEIGHT`, `RETRIEVAL_SPARSE_WEIGHT` | | Hybrid fusion knobs |

---

## Quick start — Docker

```bash
docker-compose up --build
```

This starts two services sharing the `./data` volume:

- `mcp-server` on port **8000** (MCP streamable-HTTP endpoint)
- `api-server` on port **8080** (FastAPI)

The first build is slow (CPU PyTorch + transformers download). Subsequent runs are cached.

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
      "source_file": "AAPL_10K_chunk_395"
    }
  ],
  "tokens_used": null
}
```

Each source is `{text, ticker, source_file}` — the chunk index is encoded in
`source_file` as `TICKER_10K_chunk_N`, and the SSE endpoint splits it out into a
separate `chunk_idx` field. There is no `backend` field: which agent served the
request is logged server-side, not returned. `tokens_used` is currently always
`null`.

Optional `ticker` field narrows retrieval to a single company.

### `POST /query/stream`

Same request body as `/query`, but streams the answer as Server-Sent Events
(`text/event-stream`) — this is what the web UI consumes.

```bash
curl -N -X POST http://localhost:8080/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "What were Apple total net sales in the most recent fiscal year?", "ticker": "AAPL"}'
```

Each line is one JSON event: `{"type":"token","text":...}` (repeated),
then `{"type":"sources","items":[...]}`, then `{"type":"done"}`
(or `{"type":"error","message":...}`). CORS origins are controlled by the
`FRONTEND_ORIGINS` env var (comma-separated; defaults include `http://localhost:3000`).

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
configuration, no API call. Seventeen configurations were measured this way
(the full matrix and findings 16–17 are in EVALUATION.md); the two that matter:

| | hit@5 | recall@5 | MRR | nDCG@5 | recall@25 | p50 latency |
|---|---|---|---|---|---|---|
| dense top-5, as originally shipped | 0.507 | 0.489 | 0.357 | 0.373 | 0.680 | 17.5 ms |
| **dense + cross-encoder rerank over 50 + inferred ticker filter** (ships now) | **0.634** | **0.606** | **0.496** | **0.507** | **0.750** | 893 ms |
| the same, table items only (n = 26) | 0.500 | | | | | |
| the same, non-table items (n = 45) | 0.711 | | | | | |

Hybrid BM25 + dense fusion scored below plain dense (0.479) and is kept in
the code, off by default, as a measured negative result. Labels mark the
cited reference passage, not every chunk stating the fact, so these are lower
bounds (EVALUATION.md finding 13). Before the reranker, the agent's own query
wording found a labelled chunk on 43.7% of items (`agent_hit_rate`), below
the 50.7% the plain question achieved.

### Figure check (no LLM)

Every figure in the ground truth must appear in the answer; years must match
exactly. On the 52 items whose ground truth contains a figure, the cached
`gemini-3.1-flash-lite` answers pass **63.5%** (`figure_exact_rate`). This is
the check that scores a right-figure-wrong-year answer as a failure where
faithfulness scored it 1.00 (finding 14).

### Answer quality, schema 3 (contexts scored per chunk, with provenance)

The cached `gemini-3.1-flash-lite` answers for all 71 items, re-judged by
`gemini-3.6-flash` with each retrieved chunk as its own context. Terminal
failures (7, all recursion-limit) count as 0. Evidence and per-stratum rows:
[baseline-v3-aea128d62403.json](eval/results/baseline-v3-aea128d62403.json).

| n | faithfulness | answer relevancy | context recall | figure_exact | agent_hit |
|---|---|---|---|---|---|
| 71 | **0.826** | 0.764 | 0.718 | 0.635 (n = 52) | 0.437 |

On the 62 answers shared with the schema-2 run below, the new instrument
returned the same `context_recall` on every item and faithfulness within two
items of the old score. It also found that stripping the `[META 10-K, chunk
412]` header from a context costs 0.13 faithfulness on identical answers,
because the chunk text says "the Company" and only the header says which one
(EVALUATION.md finding 15). This is the baseline every retrieval change is
compared against.

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

Judge `gemini-3.6-flash`, k=5, prompt `sha256:d1bedac20eb2`, RAGAS 0.4.3, 100%
metric coverage on both runs.

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
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval --score-only --judge-provider google --judge-model gemini-3.6-flash --label baseline-v3

# A full generation + judge run.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval --judge-provider google --judge-model gemini-3.6-flash --label rerun

# Cross-family re-score from cache — zero generation calls.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval --score-only --judge-provider groq --label crossjudge

# Regenerate the leaderboard from every results file.
python -m eval.leaderboard
```

Every run records a hash of its resolved configuration. A configuration that
already has a complete results file is reported, not re-run, unless `--force`
is passed.

The run is **checkpointed and resumable**, keyed on
`(item id, agent model, prompt version)`, so a quota wall costs one item rather
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
cross-family judge check (Groq `gpt-oss-120b` re-scoring the same outputs) is run
as standard practice — see EVALUATION.md finding 4 for what it did and did not
show.

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push and PR to `main`/`master` as two
parallel jobs:

**Backend (`build`)**
1. Install `requirements.txt` (CPU PyTorch extra index)
2. `flake8 .` with `--max-line-length 120 --ignore E501,W503`
3. `python -m eval.run_eval --dry-run`
4. `pytest` (117 tests; no zero-test escape hatch — a vanished suite fails the build)

**Frontend (`frontend`, in `web/`)**
1. `npm ci`
2. `npm run lint`
3. `npm test` (2 Vitest tests on the SSE streaming client; Node 22 — vitest 4 requires >=20.19)
4. `npm run build`

No secrets are required — the backend dry-run path makes no LLM calls.

---

## Deploy (free)

The whole stack runs on free tiers:

- **Frontend → Vercel (Hobby).** Import the repo, set **Root Directory** to `web/`, and set `NEXT_PUBLIC_API_BASE_URL` to the backend URL.
- **Backend → Hugging Face Spaces (Docker SDK).** Set `GEMINI_API_KEY` and `FRONTEND_ORIGINS` as Space secrets. To run the measured retrieval configuration rather than plain dense search, also set `RETRIEVAL_RERANK=true`, `RETRIEVAL_FETCH_K=50` and `RETRIEVAL_TICKER_FILTER=inferred` as Space variables; the cross-encoder (~90 MB) downloads on first start, which adds to the cold start once per rebuild, and reranking adds roughly a second of CPU per retrieval call on the free tier's two cores.

  Note that this repo's [Dockerfile](Dockerfile) and the one in the deployed Space differ deliberately. Here, the image **rebuilds** the Chroma index at build time from the committed filings under `data/sec_filings/` using local MiniLM embeddings, so the ~360–370 MB index never has to live in git. Re-embedding 67,521 chunks exceeds Hugging Face's build timeout on the free CPU builder, so the Space instead **ships a prebuilt index via Git LFS** and skips the rebuild. Copying this Dockerfile into the Space would produce a build that times out.

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
├── .github/workflows/ci.yml        # GitHub Actions: lint, eval dry-run, pytest, frontend
├── agent/
│   ├── financial_agent.py          # Direct in-process ReAct agent
│   ├── mcp_agent.py                # Same ReAct loop, tools sourced from MCP
│   └── observations.py             # Tool-output parser + canonical chunk id, shared by API and eval
├── api/
│   └── main.py                     # FastAPI app, MCP-first + direct fallback
├── data/
│   └── chroma_db/                  # Persistent vector index (gitignored)
├── eval/
│   ├── benchmark.csv               # 71 labelled Q&A rows (full benchmark)
│   ├── benchmark_smoke.csv         # 5 of those rows, for --dry-run and CI
│   ├── benchmark_chunks.json       # Which index chunks hold each item's reference passage
│   ├── chunk_labels.py             # Builds benchmark_chunks.json from the index (+ overrides)
│   ├── chunk_labels_overrides.json # The 2 hand-resolved labels, with reasons
│   ├── run_eval.py                 # RAGAS harness (resumable, checkpointed, schema 3)
│   ├── run_retrieval_eval.py       # Retriever-only metrics, no LLM calls
│   ├── retrieval_metrics.py        # hit/recall@k, MRR, nDCG over relevance groups
│   ├── figure_match.py             # Ground-truth figures reproduced in the answer
│   ├── experiment.py               # Config hash, benchmark version, prior-run lookup
│   ├── leaderboard.py              # Regenerates results/LEADERBOARD.md
│   ├── EVALUATION.md               # Method, findings, limitations
│   ├── results/                    # Committed per-item evidence + LEADERBOARD.md (tracked)
│   └── cache/                      # Agent-output cache (gitignored)
├── ingestion/
│   ├── downloader.py               # SEC EDGAR fetcher
│   ├── cleaner.py                  # HTML/iXBRL stripping
│   ├── chunker.py                  # Recursive splitter, 512 chars / 50 overlap
│   ├── embedder.py                 # MiniLM → ChromaDB upsert
│   └── pipeline.py                 # download → clean → chunk → embed
├── mcp_server/
│   └── server.py                   # FastMCP server, streamable-HTTP transport
├── retrieval/
│   ├── query_engine.py             # Single-shot RAG (no agent loop)
│   └── retriever.py                # The one retrieval call, behind RetrievalConfig
├── tests/                          # 117 tests; no network, key or index needed
│   ├── test_ingestion.py           # chunker, cleaner, embedder (32)
│   ├── test_retrieval.py           # query_engine retrieval + prompt path (18)
│   ├── test_terminal_failures.py   # empty-answer / recursion-limit guard (33)
│   ├── test_query_stream.py        # SSE streaming contract (2)
│   ├── test_eval_instrument.py     # observation parser, retrieval metrics, figure check, labels, leaderboard (24)
│   └── test_eval_harness.py        # per-chunk contexts, cache upgrade, deterministic aggregation (8)
├── ROADMAP.md                      # Three next steps, each from a finding
├── docker-compose.yml              # api-server + mcp-server
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Known limitations

- **Table chunking.** The recursive character splitter breaks 10-K tables across chunk boundaries, so numeric questions that depend on multi-row context (e.g. segment breakdowns) can retrieve partial rows. A dedicated table-aware splitter (or a layout-preserving parser like Unstructured) would close this gap.
- **n = 66 for the reported runs establishes no statistical significance**, and five of seven question-type strata are n ≤ 8 (`multi_hop` is a single item in the whole benchmark). The per-type breakdown is directional at best. Free-tier quota previously capped the reported run at n = 8 — the Gemini free tier allows 20 requests per day, per model, per project, measured from live 429 bodies — and the harness is checkpointed and resumable because of it; see [eval/EVALUATION.md](eval/EVALUATION.md) findings 6 and 10.
- **The reported runs are same-family judged, and the cross-family check is thin.** Both 66-item runs used a `gemini-3.6-flash` judge against a Gemini agent, so same-model-family bias applies to the headline numbers. A cross-family check was run — 20 of those items re-scored by Groq `openai/gpt-oss-120b` — and the two judges broadly agree (mean absolute divergence 0.025–0.061; agreement within 0.1 on 80–95% of items). The disagreement concentrates on the three `comparative` items, where the Gemini judge gave a flat 1.00 and the Groq judge 0.71–0.75. That is a signal worth acting on, not proof of bias: n = 3. See [eval/EVALUATION.md](eval/EVALUATION.md) finding 4.
- **`answer_relevancy` is not reproducible to the third decimal.** RAGAS overrides the judge's temperature to 0.3 for any metric requesting n > 1 generations, which `answer_relevancy` always does. `faithfulness` and `context_recall` are stable run-to-run; small `answer_relevancy` differences are noise.
- **Chunk labels mark the reference passage, not every passage that could answer.** The retrieval metrics score against the chunk(s) holding the passage the benchmark author cited. On 12 of 71 items the agent produced the right figure without ever retrieving a labelled chunk, because the same fact appears elsewhere in the filing. Retrieval scores are therefore a lower bound, and the figure check is the metric that says whether the answer was right.
- **Results files before schema 3 scored `context_recall` over context blobs, not chunks.** Those files (`eval/results/*-<commit>.json`) are kept and listed separately on the leaderboard; their `context_recall` is not comparable with schema-3 runs.
- **Free-tier cold start.** The backend Space sleeps after inactivity; the first request after a sleep takes ~30–60 s to wake the container before answers stream. This is a demo-scale, single-user deployment — not sized for concurrent load.
- **Five dependency advisories remain open, and none has an upstream fix.** `npm audit` reports **0 vulnerabilities** — the `vitest` chain was cleared by moving to vitest 4 on Node 22, and every patched Python advisory (`langchain`, `langchain-text-splitters`, `langchain-openai`, `lxml`, `mcp`) has been taken. What is left is four ChromaDB advisories (2 critical, 2 high) and one `ragas` advisory, all of which have **no patched release published upstream**, so no version bump clears them. The ChromaDB pin is additionally verified to read the prebuilt index shipped in the deployed Space, so moving it would need an index-compatibility re-check rather than a routine bump.
- **Test coverage is real but not complete.** 117 backend tests plus 2 frontend Vitest tests. Covered: the chunker and cleaner (including the iXBRL-preamble heuristic), the embedder's batching and citation metadata, the retrieval query path, the `/query` and `/query/stream` contracts, both terminal-failure states, list-shaped message content through every entry point that flattens it, and the evaluation instrument (observation parsing, chunk labelling, retrieval metrics, the figure check, the cache upgrade and the leaderboard). Still untested: `mcp_server/server.py` and the MCP tool contract in `agent/mcp_agent.py` — its `arun_agent` answer contract is covered, but the tool wiring is not — plus `ingestion/downloader.py` (network-bound) and `ingestion/pipeline.py` (the orchestration wrapper). The MCP path is also the one the deployed backend never exercises — `/health` reports `mcp_server: false` in production, so it runs the direct-agent fallback.
- **Chunked streaming, not per-token LLM streaming.** `/query/stream` runs the agent to completion and then streams the final answer word-by-word, rather than surfacing raw Gemini token deltas via `astream_events`. This trades true first-token latency for reliable isolation of only the final answer (the agent emits model-stream events on every tool-calling turn).
