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

## Dependency advisories

Triaged 2026-10-05. Five Dependabot alerts are open and none has a patched
release upstream (the advisory pages list no fixed version: chromadb is
vulnerable through 1.5.9, ragas through 0.4.3), so no version bump clears any of
them. None of the vulnerable code is reachable in this deployment:

- **#41, GHSA-f4j7-r4q5-qw2c (critical, chromadb, CVE-2026-45829):** code
  injection through the Chroma HTTP server's `/api/v2` collections endpoint.
  This project opens the index in-process (`chromadb.PersistentClient`, and
  langchain `Chroma` with `persist_directory`) and runs no Chroma server, so the
  endpoint does not exist here.
- **#48, GHSA-36p7-vc44-83pf (critical, chromadb, CVE-2026-45833):** code
  injection through the HTTP server's collection-update endpoint, which needs a
  permission granted by an authorization provider. No Chroma server and no
  authorization provider is configured here.
- **#46, GHSA-2wm9-hf6c-p5cr (high, chromadb, CVE-2026-45830):** missing
  cross-tenant authorization for authenticated users of the HTTP server. There is
  no server, no authentication and no tenant other than the default one.
- **#47, GHSA-xph7-9rjv-w5fr (high, chromadb, CVE-2026-45831):** the server's
  `SimpleRBACAuthorizationProvider` ignores which tenant, database or collection
  a permission applies to. No authorization provider is configured here.
- **#40, GHSA-95ww-475f-pr4f (low, ragas, CVE-2026-6587):** server-side request
  forgery in the multi-modal faithfulness module
  (`ragas/metrics/collections/multi_modal_faithfulness/util.py`). That module is
  imported nowhere in this repository (a grep for `multi_modal` and `MultiModal`
  across `*.py` is empty), and ragas runs only in evaluation, never in the API.

Rule: an advisory is dismissed only with its reason written here and in
`requirements.txt`, and a patched upstream release reopens the question at the
next dependency refresh.

## Secrets

No credentials are committed to this repository. Configuration is provided via
environment variables (`.env` locally, platform secrets in deployment); see
`.env.example` for the required names.
