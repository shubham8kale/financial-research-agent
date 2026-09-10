# Financial Research Agent

An agentic RAG system that answers natural-language questions about SEC 10-K filings with source-grounded citations, exposed through both a FastAPI REST interface and a Model Context Protocol (MCP) server — with a streamed, full-stack Next.js chat UI on top.

---

## Live demo

- **App:** <https://financial-research-agent-pi.vercel.app>
- **Full stack:** `Next.js UI → SSE → FastAPI (/query/stream) → LangGraph ReAct agent → ChromaDB + Gemini`
- **Cold start:** the backend runs on a free tier and sleeps after inactivity — the **first request may take ~30–60 s** to wake the container, after which answers stream token-by-token. Please don't load-test the live link (Gemini free-tier RPM limits).

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
| LLM | Google Gemini, set by `LLM_MODEL` (agent default `gemini-2.5-flash`; `retrieval/query_engine.py` default `gemini-2.5-flash-lite`) |
| Tool protocol | Model Context Protocol (MCP), streamable-HTTP transport |
| API | FastAPI + Uvicorn |
| Frontend | Next.js (App Router) + TypeScript + Tailwind CSS |
| Streaming | Server-Sent Events over `POST /query/stream` (fetch + ReadableStream) |
| Frontend tests | Vitest + React Testing Library |
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

Returns service status and whether the MCP backend is reachable.

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "mcp_server": "reachable",
  "direct_agent": "ready"
}
```

### `POST /query`

Send a natural-language question, get a grounded answer with source citations.

```bash
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What was Apple total revenue in fiscal year 2025?"}'
```

```json
{
  "answer": "Apple's total net sales for fiscal 2025 were $416,161 million ...",
  "sources": [
    {"ticker": "AAPL", "chunk_idx": 142, "snippet": "Total net sales $ 416,161 ..."}
  ],
  "backend": "mcp"
}
```

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

Full method, findings and limitations: **[eval/EVALUATION.md](eval/EVALUATION.md)**.
Per-item evidence — every answer, every retrieved context, every score — is
committed under [eval/results/](eval/results/) and can be opened directly.

### The honest headline

**n = 8. This is a smoke test, not a benchmark.**

The full **66-item** labelled benchmark is committed at
[eval/benchmark.csv](eval/benchmark.csv) and is runnable as-is. It was not run in
full because of free-tier quota, not because of the harness: the Gemini free tier
allows **20 requests per day, per model, per project** (measured from live 429
response bodies — Google no longer publishes per-model free-tier numbers). At
~2.3 agent calls per item, 66 items is roughly **nine days** of generation. The
reported run is the 8 items that fit one day's bucket.

| Metric | Score | n | NaN |
|---|---|---|---|
| faithfulness | 0.955 | 8 | 0 |
| answer_relevancy | 0.903 | 8 | 0 |
| context_recall | 0.813 | 8 | 0 |

Agent `gemini-2.5-flash`, judge `openai/gpt-oss-120b` (Groq), k=5, RAGAS 0.4.3,
prompt version `sha256:d1bedac20eb2`. **100 % metric coverage** — no mean above
is taken over a partial column. Four of the six question-type strata are n = 1;
the per-type table in EVALUATION.md marks them as anecdotal.

Every score is comparable **only** within that pinned judge model ID. Free-tier
judge models get retired without notice, and this project already lost one
mid-flight.

### What the run actually found

- A `multi_hop` item failed at **retrieval**, then answered fluently and wrongly
  from boilerplate it did retrieve (context_recall 0.00). Fluent wrongness on a
  missed retrieval is the failure mode that matters most here.
- **Retrieval varies with the agent model** even at fixed k, embeddings and
  index — the agent composes its own search query, so the query text is model
  output. Running the same 8 items on a second model changed the retrieved
  passages on 7 of 8.
- An earlier run's `faithfulness 0.00` turned out to be LangGraph exhausting
  `recursion_limit`, not a retrieval failure. The harness now separates the two.
- `answer_relevancy` is **not reproducible to the third decimal**: RAGAS
  overrides the judge temperature to 0.3 for metrics that request n > 1
  generations.

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

# The reported 8-item run.
python -m eval.run_eval   --ids qa_0001,qa_0005,qa_0023,qa_0047,qa_0060,qa_0064,qa_0019,qa_0037   --judge-provider groq --label smoke8

# The full 66. Expect it to stop on a quota wall, write what completed, and
# print the command to resume — that is the designed behaviour.
python -m eval.run_eval --judge-provider groq
```

The run is **resumable and checkpointed after every item**, keyed on
`(item id, agent model, prompt version)`. A quota wall costs one item, not the
run. `--score-only` re-judges cached answers with a different judge at zero
generation cost; `--generate-only` accumulates generation across days, since
agent and judge quotas sit in independently-resetting buckets.

A partial run **withholds aggregates** — in stdout and in the JSON — so a
stopped run can never be mistaken for a finished one.

### Judge configuration

The agent model comes from `LLM_MODEL`. The judge is selected explicitly:

| | Provider | Model var | Key var |
|---|---|---|---|
| `--judge-provider google` | Google | `RAGAS_LLM_MODEL` | `GEMINI_API_KEY` |
| `--judge-provider groq` | Groq | `RAGAS_JUDGE_MODEL` | `RAGAS_JUDGE_API_KEY` |

Credentials are read from the environment only, never accepted as flags. The
reported run judges on Groq because 66 items needs ~396 judge calls, which will
not fit a 20/day Gemini bucket — and because a Groq judge against a Gemini agent
is cross-family, which addresses the same-model judge bias this README used to
list as an open weakness.

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push and PR to `main`/`master` as two
parallel jobs:

**Backend (`build`)**
1. Install `requirements.txt` (CPU PyTorch extra index)
2. `flake8 .` with `--max-line-length 120 --ignore E501,W503`
3. `python -m eval.run_eval --dry-run`
4. `pytest` (exit code 5 = no tests collected is treated as pass)

**Frontend (`frontend`, in `web/`)**
1. `npm ci`
2. `npm run lint`
3. `npm test` (Vitest streaming smoke test)
4. `npm run build`

No secrets are required — the backend dry-run path makes no LLM calls.

---

## Deploy (free)

The whole stack runs on free tiers:

- **Frontend → Vercel (Hobby).** Import the repo, set **Root Directory** to `web/`, and set `NEXT_PUBLIC_API_BASE_URL` to the backend URL.
- **Backend → Hugging Face Spaces (Docker SDK).** The [Dockerfile](Dockerfile) rebuilds the Chroma index at build time from the committed filings under `data/sec_filings/` (using local MiniLM embeddings), so the ~360-370 MB index never needs to live in git. Set `GEMINI_API_KEY` and `FRONTEND_ORIGINS` as Space secrets.

---

## Project structure

```
financial-research-agent/
├── .github/workflows/ci.yml        # GitHub Actions: lint + dry-run
├── agent/
│   ├── financial_agent.py          # Direct in-process ReAct agent
│   └── mcp_agent.py                # Same ReAct loop, tools sourced from MCP
├── api/
│   └── main.py                     # FastAPI app, MCP-first + direct fallback
├── data/
│   └── chroma_db/                  # Persistent vector index (gitignored)
├── eval/
│   ├── benchmark.csv               # 66 labelled Q&A rows (full benchmark)
│   ├── benchmark_smoke.csv         # 5 of those rows, for --dry-run and CI
│   ├── run_eval.py                 # RAGAS harness (resumable, checkpointed)
│   ├── EVALUATION.md               # Method, findings, limitations
│   ├── results/                    # Committed per-item evidence (tracked)
│   └── cache/                      # Agent-output cache (gitignored)
├── ingestion/
│   ├── downloader.py               # SEC EDGAR fetcher
│   ├── cleaner.py                  # HTML/iXBRL stripping
│   ├── chunker.py                  # Token-aware recursive splitter
│   ├── embedder.py                 # MiniLM → ChromaDB upsert
│   └── pipeline.py                 # download → clean → chunk → embed
├── mcp_server/
│   └── server.py                   # FastMCP server, streamable-HTTP transport
├── retrieval/
│   └── query_engine.py             # Single-shot RAG (no agent loop)
├── docker-compose.yml              # api-server + mcp-server
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Known limitations

- **Table chunking.** The recursive character splitter breaks 10-K tables across chunk boundaries, so numeric questions that depend on multi-row context (e.g. segment breakdowns) can retrieve partial rows. A dedicated table-aware splitter (or a layout-preserving parser like Unstructured) would close this gap.
- **Free-tier quota is the binding constraint on evaluation size.** The Gemini free tier allows **20 requests per day, per model, per project** — measured from live 429 bodies, since Google no longer publishes per-model free-tier numbers. At ~2.3 agent calls per item the committed 66-item benchmark is ~9 days of generation, which is why the reported evaluation is n = 8. Groq's free tier for the judge is token-bound (8k TPM / 200k TPD). The harness is checkpointed and resumable specifically so a multi-day run is viable; see [eval/EVALUATION.md](eval/EVALUATION.md).
- **Judge bias is mitigated but not measured.** The reported run uses a Groq `gpt-oss-120b` judge against a Gemini agent, so it is already cross-family — the same-model bias this section previously flagged does not apply to it. What is still missing is a *quantified* comparison: re-judging the same cached answers with a Gemini judge to measure how much the two disagree. That was scoped and not run (it needs a full day's Gemini bucket). Every number is currently one judge's opinion, with no inter-judge agreement measured.
- **`answer_relevancy` is not reproducible to the third decimal.** RAGAS overrides the judge's temperature to 0.3 for any metric requesting n > 1 generations, which `answer_relevancy` always does. `faithfulness` and `context_recall` are stable run-to-run; small `answer_relevancy` differences are noise.
- **`context_recall` is measured over context blobs, not chunks.** The eval harness captures each tool observation as one context string, and an observation already concatenates all k = 5 passages. That makes `context_recall` coarser than a per-chunk measurement would be — a blob containing one relevant passage among five scores as recalled.
- **Free-tier cold start.** The backend Space sleeps after inactivity; the first request after a sleep takes ~30–60 s to wake the container before answers stream. This is a demo-scale, single-user deployment — not sized for concurrent load.
- **Thin test coverage.** Two backend tests, both on the `/query/stream` SSE contract, plus two frontend Vitest tests. The chunker, cleaner, embedder, retrieval query path, MCP tool contract and the `/query` and `/health` routes have no unit tests. The CI `pytest` step still tolerates zero collected tests, which is now obsolete and should be tightened.
- **Chunked streaming, not per-token LLM streaming.** `/query/stream` runs the agent to completion and then streams the final answer word-by-word, rather than surfacing raw Gemini token deltas via `astream_events`. This trades true first-token latency for reliable isolation of only the final answer (the agent emits model-stream events on every tool-calling turn).
