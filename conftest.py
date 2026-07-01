# conftest.py
#
# Ensure the repository root is importable as the top-level package path so
# `from api import main`, `import agent`, etc. resolve regardless of how pytest
# is invoked (bare `pytest` vs `python -m pytest`).
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
