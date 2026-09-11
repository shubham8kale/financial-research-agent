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
# Ingestion does not import from agent, and agent is the only cross-layer import
# retrieval makes: query_engine imports content_text, the single shared
# definition of "flatten a model's message content to the text a user sees".
# That helper lives here rather than in a new shared package because the
# vocabulary it belongs to — content_text, classify_terminal_state,
# raise_for_terminal_state — is already the thing every layer imports from this
# module (both CLI entry points, both API routes, the MCP agent and the eval
# harness). The API and the eval harness had each grown their own copy of the
# flattening logic; the two CLI entry points and the MCP agent had none and
# broke on list-shaped content, which is the bug this consolidation fixes.
