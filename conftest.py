# conftest.py
#
# Ensure the repository root is importable as the top-level package path so
# `from api import main`, `import agent`, etc. resolve regardless of how pytest
# is invoked (bare `pytest` vs `python -m pytest`).
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# Tests must never send traces. A developer's .env may set LANGSMITH_TRACING=true
# (agent modules call load_dotenv() at import), and langchain would then try to
# upload every tool invocation the suite makes. Forced off before any test
# module imports langchain; the harness and CLI entry points are unaffected.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

# Tests must never make a model call either.  The output contract's
# structuring call (agent/contract.py) runs on every served answer when
# VERIFY_MODE is strict (the default), and a developer's .env may hold a real
# key; off here means a fake agent's answer is served as-is.  Tests of the
# contract itself set the mode explicitly and replace the model with a fake.
os.environ["VERIFY_MODE"] = "off"
