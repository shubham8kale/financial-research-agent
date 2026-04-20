# AI Financial Research Agent

Agentic RAG system for SEC 10-K filing analysis. Built with LangChain, LangGraph, ChromaDB, Gemini, FastAPI, MCP, and Docker.

## Quick Start

### Prerequisites
- Docker Desktop installed and running
- Gemini API key (set in `.env`)

### Run with Docker
```bash
docker-compose up --build
```

Wait ~15 seconds for both services to start, then:
```bash
# Health check
curl http://localhost:8080/health

# Query
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What was Apple total revenue in fiscal year 2024?"}'
```

### Fresh Setup (no pre-built index)
If the `data/chroma_db/` directory is empty, run the ingestion pipeline:
```bash
docker exec financial-research-agent-mcp-server-1 python -m ingestion.pipeline
```
This downloads SEC 10-K filings and builds the ChromaDB vector index (~10 min).

## Architecture
- **MCP Server** (port 8000): Exposes search tools via Model Context Protocol
- **API Server** (port 8080): FastAPI REST endpoint with MCP-first, direct-agent fallback
- **ChromaDB**: Vector store with 67K+ chunks from 5 company 10-K filings (AAPL, MSFT, GOOGL, AMZN, META)
- **Gemini 2.5 Flash Lite**: LLM for ReAct agent reasoning
- **all-MiniLM-L6-v2**: Local embedding model for semantic search

## API Endpoints
- `GET /health` — Service health and MCP server reachability
- `POST /query` — Submit a question, returns answer with source citations

## Environment Variables
- `GEMINI_API_KEY` — Google Gemini API key
- `LLM_MODEL` — Gemini model name (default: gemini-2.5-flash-lite)
- `MCP_SERVER_URL` — MCP server URL (set automatically in Docker)
- `SEC_USER_AGENT_EMAIL` — Email for SEC EDGAR API compliance
