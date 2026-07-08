# Security Policy

## Reporting a vulnerability

This is a personal portfolio project. If you find a security issue, please open a
[GitHub issue](https://github.com/shubham8kale/financial-research-agent/issues)
**without** including exploit details, and I will follow up for specifics.
Please do not test vulnerabilities against the live demo deployment — it runs on
free-tier infrastructure with strict quotas.

## Scope

- FastAPI backend (`api/`, `agent/`, `retrieval/`, `ingestion/`, `mcp_server/`)
- Next.js frontend (`web/`)
- Docker image and CI workflow

## Out of scope

- Denial of service / quota exhaustion of the free-tier demo (known limitation)
- Vulnerabilities in third-party services (Gemini API, Hugging Face, Vercel)
- The demo corpus itself (public SEC filings)

## Secrets

No credentials are committed to this repository. Configuration is provided via
environment variables (`.env` locally, platform secrets in deployment); see
`.env.example` for the required names.
