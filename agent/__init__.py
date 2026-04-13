# agent/__init__.py
#
# Marks `agent/` as a Python package so its modules can be imported as:
#   from agent.financial_agent import run_agent
#
# The agent layer sits above retrieval and ingestion in the dependency graph:
#   ingestion  →  vector store (ChromaDB)
#   retrieval  →  reads from vector store
#   agent      →  orchestrates retrieval via LangChain tools + LLM reasoning
#
# Nothing in ingestion or retrieval imports from agent, preserving a clean
# one-way dependency flow.
