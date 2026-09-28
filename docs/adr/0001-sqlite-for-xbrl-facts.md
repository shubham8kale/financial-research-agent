# ADR 0001: SQLite, not DuckDB, for the XBRL fact table

**Status:** accepted, 2026-09-28.
**Context:** upgrade 3 of the six-upgrade plan — structured financial facts next to the semantic index.

## Decision

The tagged facts parsed from the five 10-K submissions (`ingestion/xbrl.py`)
are stored in a single SQLite file, `data/facts.sqlite`, read through the
standard-library `sqlite3` module (`retrieval/facts.py`).

## Alternatives considered

**DuckDB.** The obvious analytical choice, and the one the original build
brief named. Columnar storage and vectorised execution matter when a fact
table spans hundreds of filings and queries scan or aggregate across them.
It adds a ~40 MB wheel to a free-tier image whose size is already a
constraint, and one more pinned dependency to track advisories for.

**The SEC companyfacts JSON API.** Would give the same facts for any filer
without parsing, but every evaluation number in this repository is meant to
be reproducible from the repository alone, at image build time, with no
network call. It remains the right fallback for filings the repo does not
carry, and is noted as such in `ingestion/xbrl.py`.

**Keeping facts in ChromaDB metadata.** Would avoid a second store, but a
lookup by (ticker, concept, fiscal year, segment) is a relational query, and
Chroma's metadata filter is neither indexed for it nor able to express it.

## Why SQLite

- **Scale.** 6,089 facts across five filings. Every query the tools make is a
  point lookup on an indexed key and returns in microseconds. There is no
  workload here that a columnar engine would speed up.
- **Zero cost.** Ships with Python; nothing to install, pin, audit or load
  into RAM on the free-tier Space.
- **Build time.** The table is rebuilt from scratch in about three seconds as
  the last step of `python -m ingestion.pipeline`, so the Docker image that
  builds the vector index gets the fact table for free.
- **Read-only at runtime.** The store opens the file in read-only mode, which
  is safe under the API's concurrent requests and needs no server.

## When to revisit

Move to DuckDB (or Postgres) when the fact table is expected to hold more than
a few dozen filings, when queries aggregate across companies or years rather
than look up single facts, or when a second writer needs the table while the
API reads it. The `FactStore` interface is the seam: nothing above it knows
which engine answers the query.
