# ingestion/__init__.py
#
# This file marks the `ingestion` directory as a Python package, allowing
# its modules (downloader, chunker, embedder) to be imported cleanly with:
#   from ingestion.downloader import download_filings
#
# Keeping ingestion as its own package separates data-acquisition concerns
# from the rest of the agent (retrieval, generation, API layer). This makes
# it easy to swap out or extend just the ingestion pipeline without touching
# downstream code.
