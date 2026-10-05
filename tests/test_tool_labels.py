# tests/test_tool_labels.py
#
# eval/benchmark_tools.json: the tool-selection labels the tool-call metrics
# (eval/tool_metrics.py) score against.  The labels were written blind, so the
# tests do not judge them; they guard what a later edit could silently break:
# the file covers exactly the benchmark's items, names only tools that exist,
# and every item is internally consistent.

import json

from agent.financial_agent import (INDEXED_TICKERS, compare_companies, compute_metric, list_available_companies,
                                   lookup_financial_fact, search_filings)
from eval.run_eval import BENCHMARK_FILE, EVAL_DIR, load_benchmark

TOOLS_FILE = EVAL_DIR / "benchmark_tools.json"
REAL_TOOLS = {t.name for t in (search_filings, list_available_companies, compare_companies, lookup_financial_fact,
                               compute_metric)}


def _labels() -> dict:
    return json.loads(TOOLS_FILE.read_text(encoding="utf-8"))


def test_the_sidecar_covers_exactly_the_71_benchmark_items():
    ids = [r["id"] for r in load_benchmark(BENCHMARK_FILE)]
    items = _labels()["items"]
    assert len(ids) == 71
    assert set(items) == set(ids), sorted(set(items) ^ set(ids))


def test_it_names_only_tools_that_exist():
    labels = _labels()
    assert set(labels["tools"]) == REAL_TOOLS
    for item_id, item in labels["items"].items():
        for field in ("first_tool_ok", "allowed_tools", "required_tools"):
            assert set(item[field]) <= REAL_TOOLS, (item_id, field, set(item[field]) - REAL_TOOLS)


def test_every_item_is_internally_consistent():
    for item_id, item in _labels()["items"].items():
        assert item["first_tool_ok"], item_id
        assert set(item["first_tool_ok"]) <= set(item["allowed_tools"]), item_id      # a first call must be allowed
        assert set(item["required_tools"]) <= set(item["allowed_tools"]), item_id     # a required tool must be allowed
        assert item["tickers"] and set(item["tickers"]) <= set(INDEXED_TICKERS), item_id
        year = item["fiscal_year"]
        assert year is None or (isinstance(year, int) and 2020 <= year <= 2026), item_id
        if "fiscal_years_ok" in item:
            assert year in item["fiscal_years_ok"], item_id
        assert item["rationale"].strip(), item_id


def test_the_sidecar_is_not_part_of_the_benchmark_version():
    # benchmark_version hashes the benchmark CSV and the chunk labels only; editing a tool label must never orphan
    # a committed results file.  tool_metrics records this file's own sha256 instead.
    import inspect

    from eval import experiment
    from eval.chunk_labels import LABELS_FILE
    source = inspect.getsource(experiment.benchmark_version)
    assert "benchmark_tools" not in source and "TOOLS_FILE" not in source
    assert TOOLS_FILE.name not in (BENCHMARK_FILE.name, LABELS_FILE.name)
    assert experiment.benchmark_version(BENCHMARK_FILE, LABELS_FILE) == experiment.benchmark_version(BENCHMARK_FILE, LABELS_FILE)
