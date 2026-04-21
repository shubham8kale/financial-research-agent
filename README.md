# Financial Research Agent

An agentic RAG system that answers natural-language questions about SEC 10-K filings with source-grounded citations, exposed through both a FastAPI REST interface and a Model Context Protocol (MCP) server.

---

## Architecture

```
       SEC EDGAR
           │
           ▼
   ┌──────────────────┐
   │   Ingestion      │   downloader → cleaner → chunker → embedder
   │ (ingestion/…)    │   (HTML strip, 500-char chunks, MiniLM embeddings)
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │    ChromaDB      │   persistent vector store, 67K+ chunks
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

---

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangChain, LangGraph (`create_react_agent`) |
| Vector store | ChromaDB (embedded, persistent SQLite + binary index) |
| Embeddings | HuggingFace `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim) |
| LLM | Google Gemini 2.5 Flash-Lite (`gemini-2.5-flash-lite`) |
| Tool protocol | Model Context Protocol (MCP), streamable-HTTP transport |
| API | FastAPI + Uvicorn |
| Packaging | Docker, docker-compose |
| Evaluation | RAGAS 0.2 (faithfulness, answer_relevancy, context_recall) |
| CI | GitHub Actions (lint + dry-run on every push/PR) |

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

# 4. Build the vector index (first run only — downloads filings + embeds)
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

---

## Evaluation

`eval/run_eval.py` runs the direct ReAct agent against a small labelled benchmark and scores the run with RAGAS 0.2.

**Metrics**
- **faithfulness** — every claim in the answer must be grounded in a retrieved passage
- **answer_relevancy** — does the answer actually address the question
- **context_recall** — did the retriever surface the passages the ground-truth answer depends on

**Run it**

```bash
# Validate the pipeline end-to-end without any LLM calls
python -m eval.run_eval --dry-run

# Full run: agent + RAGAS scoring
python -m eval.run_eval

# Score-only: reuse agent outputs from eval/results.json, re-run RAGAS only
#   (useful after a RAGAS quota exhaustion — no agent re-cost)
python -m eval.run_eval --score-only
```

The judge LLM is controlled by `RAGAS_LLM_MODEL` (separate from `LLM_MODEL`) so the judge can use a different Gemini quota bucket from the agent.

**Baseline (5-question benchmark, `gemini-2.5-flash-lite` agent + judge)**

| Metric | Score |
|---|---|
| faithfulness | 0.80 |
| answer_relevancy | (variable — see results.json) |
| context_recall | 0.80 |

Per-question scores and full agent transcripts are written to `eval/results.json` (gitignored).

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push and PR to `main`/`master`:

1. Install `requirements.txt` (CPU PyTorch extra index)
2. `flake8 .` with `--max-line-length 120 --ignore E501,W503`
3. `python -m eval.run_eval --dry-run`
4. `pytest` (exit code 5 = no tests collected is treated as pass)

No secrets are required — the dry-run path makes no LLM calls.

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
│   ├── benchmark.csv               # 5 labelled Q&A rows for evaluation
│   ├── run_eval.py                 # RAGAS harness (dry-run / score-only flags)
│   └── results.json                # Per-run outputs + scores (gitignored)
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
├── debug_agent.py                  # Diagnostic script: prints every message
├── docker-compose.yml              # api-server + mcp-server
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Known limitations

- **Table chunking.** The recursive character splitter breaks 10-K tables across chunk boundaries, so numeric questions that depend on multi-row context (e.g. segment breakdowns) can retrieve partial rows. A dedicated table-aware splitter (or a layout-preserving parser like Unstructured) would close this gap.
- **Free-tier rate limits.** The Gemini free tier enforces per-minute RPM/TPM caps. The eval pipeline sleeps 5 s between agent calls to stay under them; a full run still takes ~1 minute for 5 questions. Bulk evaluation needs a paid key or parallel quota buckets (hence the separate `RAGAS_LLM_MODEL` env var).
- **Same-model judge bias.** Scoring with the same family (Gemini) that generated the answers can inflate faithfulness and relevancy (the judge agrees with its own priors). A cross-family judge (e.g. Claude or an OpenAI model) would give a more independent signal; worth doing before reporting externally.
