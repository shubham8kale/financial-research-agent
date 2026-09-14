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
| Embeddings | HuggingFace `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim) |
| LLM | Google Gemini, set by `LLM_MODEL`. Default `gemini-3.1-flash-lite` across the agent, MCP agent and query engine |
| Tool protocol | Model Context Protocol (MCP), streamable-HTTP transport |
| API | FastAPI + Uvicorn |
| Frontend | Next.js (App Router) + TypeScript + Tailwind CSS |
| Streaming | Server-Sent Events over `POST /query/stream` (fetch + ReadableStream) |
| Backend tests | pytest — 85 tests, no network / API key / index required |
| Frontend tests | Vitest + React Testing Library — 2 tests |
| Packaging | Docker, docker-compose |
| Evaluation | RAGAS 0.4.3 (faithfulness, answer_relevancy, context_recall) — see [eval/EVALUATION.md](eval/EVALUATION.md) |
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

71 labelled questions over five FY2025 10-K filings, seven question types, scored
with RAGAS. The before/after runs below cover items 1-66; five `temporal` items
were added later and run separately (EVALUATION.md finding 2). **Full method, findings and limitations:
[eval/EVALUATION.md](eval/EVALUATION.md).** Per-item evidence — every answer,
every retrieved context, every score — is committed under
[eval/results/](eval/results/).

Two runs of the same 66 items, **agent model the only variable**. Terminal
failures (empty answer or recursion limit) are counted as 0 in both, not excluded.

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

- **faithfulness** — every claim in the answer must be grounded in a retrieved passage
- **answer_relevancy** — does the answer actually address the question
- **context_recall** — did the retriever surface the passages the ground-truth answer depends on

### Run it

```bash
# No LLM calls at all — validates benchmark parsing, argparse and imports. This is CI.
python -m eval.run_eval --dry-run

# The 5-item smoke benchmark, end to end.
python -m eval.run_eval --smoke --judge-provider groq

# The reported 66-item run.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval   --judge-provider google --judge-model gemini-3.6-flash --label rerun66

# Cross-family re-score from cache — zero generation calls.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval   --score-only --judge-provider groq --label crossjudge20
```

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
4. `pytest` (85 tests; no zero-test escape hatch — a vanished suite fails the build)

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
- **Backend → Hugging Face Spaces (Docker SDK).** Set `GEMINI_API_KEY` and `FRONTEND_ORIGINS` as Space secrets.

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
│   └── mcp_agent.py                # Same ReAct loop, tools sourced from MCP
├── api/
│   └── main.py                     # FastAPI app, MCP-first + direct fallback
├── data/
│   └── chroma_db/                  # Persistent vector index (gitignored)
├── eval/
│   ├── benchmark.csv               # 71 labelled Q&A rows (full benchmark)
│   ├── benchmark_smoke.csv         # 5 of those rows, for --dry-run and CI
│   ├── run_eval.py                 # RAGAS harness (resumable, checkpointed)
│   ├── EVALUATION.md               # Method, findings, limitations
│   ├── results/                    # Committed per-item evidence (tracked)
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
│   └── query_engine.py             # Single-shot RAG (no agent loop)
├── tests/                          # 85 tests; no network, key or index needed
│   ├── test_ingestion.py           # chunker, cleaner, embedder (32)
│   ├── test_retrieval.py           # query_engine retrieval + prompt path (16)
│   ├── test_terminal_failures.py   # empty-answer / recursion-limit guard (33)
│   └── test_query_stream.py        # SSE streaming contract (2)
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
- **`context_recall` is measured over context blobs, not chunks.** The eval harness captures each tool observation as one context string, and an observation already concatenates all k = 5 passages. That makes `context_recall` coarser than a per-chunk measurement would be — a blob containing one relevant passage among five scores as recalled.
- **Free-tier cold start.** The backend Space sleeps after inactivity; the first request after a sleep takes ~30–60 s to wake the container before answers stream. This is a demo-scale, single-user deployment — not sized for concurrent load.
- **Five dependency advisories remain open, and none has an upstream fix.** `npm audit` reports **0 vulnerabilities** — the `vitest` chain was cleared by moving to vitest 4 on Node 22, and every patched Python advisory (`langchain`, `langchain-text-splitters`, `langchain-openai`, `lxml`, `mcp`) has been taken. What is left is four ChromaDB advisories (2 critical, 2 high) and one `ragas` advisory, all of which have **no patched release published upstream**, so no version bump clears them. The ChromaDB pin is additionally verified to read the prebuilt index shipped in the deployed Space, so moving it would need an index-compatibility re-check rather than a routine bump.
- **Test coverage is real but not complete.** 85 backend tests plus 2 frontend Vitest tests. Covered: the chunker and cleaner (including the iXBRL-preamble heuristic), the embedder's batching and citation metadata, the retrieval query path, the `/query` and `/query/stream` contracts, both terminal-failure states, and list-shaped message content through every entry point that flattens it. Still untested: `mcp_server/server.py` and the MCP tool contract in `agent/mcp_agent.py` — its `arun_agent` answer contract is covered, but the tool wiring is not — plus `ingestion/downloader.py` (network-bound) and `ingestion/pipeline.py` (the orchestration wrapper). The MCP path is also the one the deployed backend never exercises — `/health` reports `mcp_server: false` in production, so it runs the direct-agent fallback.
- **Chunked streaming, not per-token LLM streaming.** `/query/stream` runs the agent to completion and then streams the final answer word-by-word, rather than surfacing raw Gemini token deltas via `astream_events`. This trades true first-token latency for reliable isolation of only the final answer (the agent emits model-stream events on every tool-calling turn).
