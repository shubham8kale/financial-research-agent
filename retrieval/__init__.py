# retrieval/__init__.py
#
# This file marks `retrieval/` as a Python package, allowing its modules to
# be imported as:
#   from retrieval.query_engine import ask
#
# Separating `retrieval/` from `ingestion/` enforces the pipeline's one-way
# data flow: ingestion writes to the vector store, retrieval reads from it.
# Neither package imports from the other, which means either half can be
# swapped, tested, or scaled independently.
