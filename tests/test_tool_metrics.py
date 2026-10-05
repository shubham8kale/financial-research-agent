# tests/test_tool_metrics.py
#
# eval/tool_metrics.py on synthetic records, one metric and its edge cases at a
# time, plus the committed cost-v3 and contract-v3 files pinned to the baseline
# numbers the upgrade was measured against.  No model, no network, no index.

import json
import random

import pytest

from agent.observations import (TOOL_ARGUMENT_ERROR_PREFIX, argument_error_text, argument_error_tool,
                                parse_observation)
from eval import run_eval, tool_metrics as tm

FACT, SEARCH, COMPARE, COMPUTE, LIST = ("lookup_financial_fact", "search_filings", "compare_companies",
                                        "compute_metric", "list_available_companies")


def call(name, *, t0=None, ms=5.0, error=False, args=None, message=None):
    c = {"name": name, "ms": ms, "error": error}
    if t0 is not None:
        c["t0_ms"] = t0
    if args is not None:
        c["args"] = args
    if message:
        c["error_message"] = message
    return c


def record(item_id, calls, *, qtype="numerical", llm_calls=None, observations=None, verification=None, latency=1000.0,
           terminal=None, figure=None, cost=0.001):
    n = llm_calls if llm_calls is not None else len(calls) + 1
    return {"id": item_id, "question_type": qtype, "meter": {"tool_calls": calls}, "llm_calls": n, "latency_ms": latency,
            "cost_usd": cost, "observations": observations or [], "verification": verification,
            "terminal_failure": terminal, "recursion_limit_hit": terminal == "recursion_limit",
            "figure": figure or {"applicable": False}}


def label(first=(FACT,), allowed=(FACT, SEARCH), required=(), tickers=("AAPL",), year=2025, years_ok=None):
    d = {"first_tool_ok": list(first), "allowed_tools": list(allowed), "required_tools": list(required),
         "tickers": list(tickers), "fiscal_year": year, "rationale": "x"}
    if years_ok:
        d["fiscal_years_ok"] = years_ok
    return d


def metrics(rec, lab, store=None):
    return tm.item_metrics(rec, lab, store)


# ── percentiles ────────────────────────────────────────────────────────────────

def test_percentile_is_the_harnesss_nearest_rank_and_linear_is_interpolated():
    rng = random.Random(7)
    for n in (1, 2, 5, 70, 71):
        values = [rng.uniform(100, 9000) for _ in range(n)]
        for pct in (50, 95):
            assert tm.percentile(values, pct) == run_eval._percentile(values, pct)
    assert tm.percentile([], 50) is None and tm.percentile_linear([], 50) is None
    assert tm.percentile_linear([1, 2, 3, 4], 50) == 2.5
    assert tm.percentile_linear([10, 20], 95) == 19.5
    assert tm.percentile_linear([7], 95) == 7


# ── call_validity ───────────────────────────────────────────────────────────────

def test_call_validity_counts_framework_errors_and_the_argument_error_marker_by_tool():
    marker = argument_error_text(FACT, "concept is required; for example 'net income'")
    rec = record("qa_1", [call(FACT, error=True, args={"ticker": "AAPL"}), call(FACT, args={"ticker": "AAPL"}),
                          call(SEARCH, args={"query": "q"})],
                 observations=["error text", marker, "[1] ticker=AAPL  chunk_idx=1\n    text"])
    m = metrics(rec, label())
    assert m["by_tool"] == {FACT: {"n": 2, "failed": 2}, SEARCH: {"n": 1, "failed": 0}}   # 1 framework error + 1 marker
    s = tm.summarize([m])
    assert s["call_validity"]["overall"] == {"n": 1, "of": 3, "rate": 0.3333}
    assert s["call_validity"]["by_tool"][FACT] == {"n": 0, "of": 2, "rate": 0.0}
    assert s["call_validity"]["failed_by_tool"] == {FACT: 2}
    assert s["call_validity"]["items_with_a_failed_call"] == {"n": 1, "of": 1, "rate": 1.0}


def test_a_marker_never_fails_more_calls_than_were_made():
    marker = argument_error_text(FACT, "concept is required")
    m = metrics(record("qa_1", [call(FACT)], observations=[marker, marker]), label())
    assert m["by_tool"][FACT] == {"n": 1, "failed": 1}


def test_the_argument_error_marker_round_trips_and_carries_no_source():
    text = argument_error_text(FACT, "concept is required")
    assert text.startswith(TOOL_ARGUMENT_ERROR_PREFIX) and argument_error_tool(text) == FACT
    assert argument_error_tool("[1] ticker=AAPL  fact_id=3\n    x") is None and argument_error_tool("") is None
    assert argument_error_tool(None) is None
    assert parse_observation(text) == []            # nothing to cite, nothing to score as a context


# ── first_tool_ok and tool_set_ok ───────────────────────────────────────────────

def test_the_first_call_is_the_earliest_start_not_the_first_to_finish():
    calls = [call(SEARCH, t0=40.0, ms=3.0), call(FACT, t0=2.0, ms=500.0)]       # the lookup started first, finished last
    m = metrics(record("qa_1", calls), label())
    assert m["first_tool"] == FACT and m["first_tool_method"] == "t0" and m["first_tool_ok"] is True


def test_on_an_older_file_the_first_recorded_call_is_used_and_flagged_as_completion_order():
    m = metrics(record("qa_1", [call(SEARCH), call(FACT)]), label(first=(FACT,)))
    assert m["first_tool"] == SEARCH and m["first_tool_method"] == "completion" and m["first_tool_ok"] is False


def test_tool_set_ok_needs_every_tool_allowed_and_every_required_tool_present():
    lab = label(allowed=(FACT, SEARCH, COMPUTE), required=(COMPUTE,))
    ok = metrics(record("a", [call(FACT), call(COMPUTE)]), lab)
    assert (ok["tool_set_ok"], ok["allowed_only_ok"], ok["missing_required"], ok["disallowed"]) == (True, True, [], [])
    no_calc = metrics(record("b", [call(FACT)]), lab)
    assert (no_calc["tool_set_ok"], no_calc["allowed_only_ok"], no_calc["missing_required"]) == (False, True, [COMPUTE])
    wrong = metrics(record("c", [call(FACT), call(COMPUTE), call(LIST)]), lab)
    assert (wrong["tool_set_ok"], wrong["allowed_only_ok"], wrong["disallowed"]) == (False, False, [LIST])


def test_an_item_with_no_calls_has_no_first_tool_and_fails_only_a_requirement():
    none = metrics(record("a", []), label())
    assert none["first_tool"] is None and none["first_tool_ok"] is None and none["tool_set_ok"] is True
    required = metrics(record("b", []), label(allowed=(FACT, COMPUTE), required=(COMPUTE,)))
    assert required["tool_set_ok"] is False and required["missing_required"] == [COMPUTE]
    s = tm.summarize([none, required])
    assert s["first_tool_ok"] == {"n": 0, "of": 0, "rate": None}        # no first call to judge
    assert s["tool_set_ok"] == {"n": 1, "of": 2, "rate": 0.5}


def test_an_unlabelled_item_is_measured_for_calls_but_not_for_labels():
    m = metrics(record("qa_x", [call(FACT)]), None)
    assert m["tool_set_ok"] is None and m["first_tool_ok"] is None and m["arg_checks"] == []
    assert tm.summarize([m])["tool_set_ok"] == {"n": 0, "of": 0, "rate": None}


# ── batching ────────────────────────────────────────────────────────────────────

def test_calls_whose_windows_overlap_are_one_step_and_a_later_call_is_the_next():
    calls = [call(FACT, t0=10.0, ms=50.0), call(FACT, t0=12.0, ms=3.0), call(FACT, t0=14.0, ms=40.0),   # one step of three
             call(SEARCH, t0=900.0, ms=800.0)]                                                         # the next step
    b = metrics(record("qa_1", calls), label())["batching"]
    assert b["method"] == "t0" and b["steps"] == 2 and b["batched_steps"] == 1
    assert b["calls_in_batched_steps"] == 3 and b["batched"] is True and b["step_sizes"] == [3, 1]


def test_a_sibling_that_started_after_a_fast_call_ended_is_still_the_same_step():
    # a 1 ms lookup can be over before the next worker thread starts; the padding on each end absorbs that
    same = [call(FACT, t0=5.0, ms=1.0), call(FACT, t0=5.0 + 1.0 + 20.0, ms=2.0)]
    assert metrics(record("a", same), label())["batching"]["steps"] == 1
    apart = [call(FACT, t0=5.0, ms=1.0), call(FACT, t0=5.0 + 1.0 + tm.OVERLAP_SLACK_MS + 10.0, ms=2.0)]
    assert metrics(record("b", apart), label())["batching"]["steps"] == 2


def test_a_serial_trajectory_is_not_batched():
    calls = [call(SEARCH, t0=0.0, ms=800.0), call(SEARCH, t0=2000.0, ms=800.0), call(SEARCH, t0=4000.0, ms=800.0)]
    b = metrics(record("qa_1", calls), label())["batching"]
    assert b["steps"] == 3 and b["batched_steps"] == 0 and b["batched"] is False and b["calls_in_batched_steps"] == 0


def test_without_start_offsets_batching_is_the_calls_over_steps_heuristic():
    # 5 calls, 3 model calls: 2 tool steps issued 5 calls, so some step made several
    m = metrics(record("qa_1", [call(FACT)] * 5, llm_calls=3), label())
    assert m["batching"] == {"method": "heuristic", "n_calls": 5, "steps": 2, "batched_steps": None,
                             "calls_in_batched_steps": None, "batched": True, "calls_beyond_one_per_step": 3}
    assert m["more_calls_than_steps"] is True
    serial = metrics(record("qa_2", [call(FACT)] * 2, llm_calls=3), label())
    assert serial["batching"]["batched"] is False and serial["more_calls_than_steps"] is False


def test_the_heuristic_does_not_count_the_structuring_calls_as_agent_steps():
    # llm_calls 5 = 3 agent calls + 2 structuring attempts, so 2 tool steps for 3 calls: batched
    rec = record("qa_1", [call(FACT)] * 3, llm_calls=5, verification={"status": "verified", "attempts": 2})
    assert tm.agent_llm_calls(rec) == 3 and metrics(rec, label())["batching"]["batched"] is True
    skipped = record("qa_2", [call(FACT)] * 3, llm_calls=5, verification={"status": "skipped", "attempts": 0})
    assert tm.agent_llm_calls(skipped) == 5


def test_aggregate_batching_reports_calls_and_items_for_a_file_with_offsets():
    items = [metrics(record("a", [call(FACT, t0=0.0, ms=10.0), call(FACT, t0=1.0, ms=10.0), call(SEARCH, t0=900.0)]), label()),
             metrics(record("b", [call(SEARCH, t0=0.0)]), label())]
    b = tm.summarize(items)["batched"]
    assert b["method"] == "t0" and b["steps"] == 3 and b["batched_steps"] == 1
    assert b["calls_in_batched_steps"] == {"n": 2, "of": 4, "rate": 0.5}
    assert b["items_with_a_batched_step"] == {"n": 1, "of": 2, "rate": 0.5}


# ── redundant calls ─────────────────────────────────────────────────────────────

def test_redundant_calls_are_identical_tool_and_arguments_whatever_the_key_order():
    calls = [call(SEARCH, args={"query": "a"}), call(SEARCH, args={"query": "a"}), call(SEARCH, args={"query": "b"}),
             call(FACT, args={"ticker": "AAPL", "concept": "x"}), call(FACT, args={"concept": "x", "ticker": "AAPL"})]
    m = metrics(record("qa_1", calls), label())
    assert m["redundant_calls"] == 2
    s = tm.summarize([m, metrics(record("qa_2", [call(SEARCH, args={"query": "z"})]), label())])
    assert s["redundant_calls"] == {"items_measured": 2, "redundant_calls": 2, "items_with_one": 1}


def test_redundant_calls_are_not_measured_without_arguments():
    m = metrics(record("qa_1", [call(SEARCH), call(SEARCH)]), label())
    assert m["redundant_calls"] is None and tm.summarize([m])["redundant_calls"] is None


# ── arg_validity ────────────────────────────────────────────────────────────────

class FakeStore:
    """Stands in for retrieval.facts.FactStore: 'revenue' resolves and returns a row, 'goodwill' resolves to nothing for the year."""

    def __init__(self):
        self.calls = []

    def lookup(self, ticker, concept, fiscal_year=None, segment=None):
        self.calls.append((ticker, concept, fiscal_year, segment))
        if ticker == "BAD":
            raise KeyError(ticker)
        if concept == "revenue":
            return [object()], {"concepts": ["us-gaap:Revenues"]}
        if concept == "goodwill":
            return [], {"concepts": ["us-gaap:Goodwill"]}
        return [], {"concepts": []}


def test_arg_validity_checks_ticker_year_and_concept_per_lookup_call():
    store = FakeStore()
    calls = [call(FACT, args={"ticker": "AAPL", "concept": "revenue", "fiscal_year": 2025}),
             call(FACT, args={"ticker": "MSFT", "concept": "revenue", "fiscal_year": 2025}),          # not this item's company
             call(FACT, args={"ticker": "AAPL", "concept": "goodwill", "fiscal_year": 2024}),         # resolves, no row, wrong year
             call(FACT, args={"ticker": "AAPL", "concept": "frobnicate"}),                            # resolves to nothing, year omitted
             call(FACT, args={"ticker": "AAPL", "fiscal_year": 2025}),                                # concept missing
             call(SEARCH, args={"query": "q"})]                                                       # not a lookup
    m = metrics(record("qa_1", calls), label(), store)
    rows = m["arg_checks"]
    assert len(rows) == 5
    assert [r["ticker_ok"] for r in rows] == [True, False, True, True, True]
    assert [r["fiscal_year"] for r in rows] == ["exact", "exact", "wrong", "omitted", "exact"]
    assert [r["concept_present"] for r in rows] == [True, True, True, True, False]
    assert [r["concept_returns_rows"] for r in rows] == [True, True, False, False, None]
    assert [r["concept_resolved"] for r in rows] == [True, True, True, False, None]
    assert store.calls[0] == ("AAPL", "revenue", 2025, None)        # the call's own arguments reach the resolver
    v = tm.summarize([m])["arg_validity"]
    assert v["lookup_calls"] == 5 and v["ticker_ok"] == {"n": 4, "of": 5, "rate": 0.8}
    assert v["concept_present"] == {"n": 4, "of": 5, "rate": 0.8}
    assert v["concept_returns_rows"] == {"n": 2, "of": 4, "rate": 0.5} and v["concept_resolved"] == {"n": 3, "of": 4, "rate": 0.75}
    assert (v["fiscal_year_exact"]["n"], v["fiscal_year_omitted"]["n"], v["fiscal_year_wrong"]["n"]) == (3, 1, 1)
    assert v["fiscal_year_exact"]["of"] == 5


def test_a_growth_question_may_look_up_the_base_year_and_an_unnamed_year_is_not_checked():
    growth = label(year=2025, years_ok=[2024, 2025])
    calls = [call(FACT, args={"ticker": "AAPL", "concept": "revenue", "fiscal_year": 2024}),
             call(FACT, args={"ticker": "AAPL", "concept": "revenue", "fiscal_year": "2025"})]       # a numeric string is read as the year
    assert [r["fiscal_year"] for r in metrics(record("a", calls), growth)["arg_checks"]] == ["exact", "exact"]
    unnamed = metrics(record("b", [call(FACT, args={"ticker": "AAPL", "concept": "revenue", "fiscal_year": 2019})]),
                      label(year=None))
    assert unnamed["arg_checks"][0]["fiscal_year"] is None
    s = tm.summarize([unnamed])["arg_validity"]
    assert s["fiscal_year_exact"] == {"n": 0, "of": 0, "rate": None}


def test_a_resolver_that_raises_marks_the_concept_unresolved_instead_of_crashing():
    m = metrics(record("a", [call(FACT, args={"ticker": "BAD", "concept": "revenue"})]), label(tickers=("BAD",)), FakeStore())
    assert m["arg_checks"][0]["concept_returns_rows"] is False and m["arg_checks"][0]["concept_resolved"] is False


def test_without_a_store_the_concept_check_is_skipped_and_without_arguments_arg_validity_is_absent():
    m = metrics(record("a", [call(FACT, args={"ticker": "AAPL", "concept": "revenue", "fiscal_year": 2025})]), label(), None)
    assert m["arg_checks"][0]["concept_returns_rows"] is None
    assert tm.summarize([m])["arg_validity"]["concept_returns_rows"] == {"n": 0, "of": 0, "rate": None}
    old = metrics(record("b", [call(FACT)]), label(), FakeStore())
    assert old["arg_checks"] == [] and tm.summarize([old])["arg_validity"] is None


# ── aggregation, selection and the file level ───────────────────────────────────

def test_summarize_reports_latency_both_ways_and_the_distribution_of_calls():
    items = [metrics(record("a", [call(FACT)], latency=1000.0), label()),
             metrics(record("b", [call(FACT)], latency=3000.0), label()),
             metrics(record("c", [call(FACT)] * 2, latency=17000.0, terminal="recursion_limit"), label())]
    s = tm.summarize(items)
    assert s["calls_per_item"] == {1: 2, 2: 1} and s["tool_calls"] == 4
    assert s["latency_excluding_terminal_failures"]["n"] == 2 and s["latency_including_terminal_failures"]["n"] == 3
    assert s["latency_excluding_terminal_failures"]["mean_ms"] == 2000.0
    assert s["latency_excluding_terminal_failures"]["p50_ms_linear"] == 2000.0
    assert s["terminal_failures"] == {"n": 1, "recursion_limit": 1}


def test_compute_selects_ids_splits_by_question_type_and_skips_records_with_no_call_record():
    labels = {"a": label(), "b": label(), "c": label()}
    payload = {"results": [record("a", [call(FACT)], qtype="numerical"), record("b", [call(SEARCH)], qtype="list"),
                           {"id": "c", "question_type": "list", "latency_ms": 5.0}, record("z", [call(FACT)])]}
    full = tm.compute(payload, labels)
    assert [i["id"] for i in full["items"]] == ["a", "b", "z"]
    assert full["ids_without_a_tool_call_record"] == ["c"] and full["unlabelled_ids"] == ["z"]
    assert set(full["by_question_type"]) == {"numerical", "list"}
    sub = tm.compute(payload, labels, ids=["b", "c"])
    assert [i["id"] for i in sub["items"]] == ["b"] and sub["overall"]["tool_calls"] == 1


def test_a_results_file_with_no_tool_call_record_is_detected():
    assert tm.has_tool_calls({"results": [record("a", [call(FACT)])]}) is True
    assert tm.has_tool_calls({"results": [{"id": "a", "meter": {"latency_ms": 5}}, {"id": "b"}]}) is False
    assert tm.has_tool_calls({}) is False


def test_the_figure_primary_and_verification_columns_come_from_the_stored_record():
    items = [metrics(record("a", [call(FACT)], figure={"applicable": True, "figure_primary": True},
                            verification={"status": "verified", "attempts": 1}), label()),
             metrics(record("b", [call(FACT)], figure={"applicable": True, "figure_primary": False},
                            verification={"status": "refused", "attempts": 2}), label()),
             metrics(record("c", [call(FACT)]), label())]
    s = tm.summarize(items)
    assert s["figure_primary"] == {"n": 1, "of": 2, "rate": 0.5} and s["verification"] == {"refused": 1, "verified": 1}


def test_compare_puts_the_run_and_its_baseline_side_by_side_over_the_same_items():
    labels = {"a": label(), "b": label()}
    now = {"results": [record("a", [call(FACT, t0=0.0), call(FACT, t0=1.0)], llm_calls=2, latency=1500.0),
                       record("b", [call(SEARCH, t0=0.0)], llm_calls=2, latency=2500.0, qtype="list")]}
    before = {"results": [record("a", [call(FACT), call(FACT)], llm_calls=3, latency=4000.0),
                          record("b", [call(SEARCH)], llm_calls=2, latency=2600.0, qtype="list")]}
    out = tm.compare(tm.compute(now, labels), tm.compute(before, labels))
    assert out["current"]["batched"]["calls_in_batched_steps"] == {"n": 2, "of": 3, "rate": 0.6667}
    assert out["baseline"]["batched"]["method"] == "heuristic"
    assert out["current"]["items_with_more_calls_than_steps"]["n"] == 1 and out["baseline"]["items_with_more_calls_than_steps"]["n"] == 0
    assert out["current"]["model_calls"]["agent_mean"] == 2.0 and out["baseline"]["model_calls"]["agent_mean"] == 2.5
    assert out["current"]["by_question_type"]["numerical"]["latency_excluding_terminal_failures"]["p50_ms"] == 1500.0


def test_the_command_line_writes_the_metrics_with_the_labels_hash_and_skips_old_files(tmp_path, capsys):
    results = tmp_path / "run.json"
    payload = {"run_id": "x-abc", "label": "x", "git_commit": "abc", "git_dirty": False,
               "config": {"config_hash": "h", "prompt_version": "p", "agent_model": "m", "benchmark_version": "b"},
               "results": [record("qa_0009", [call(FACT, t0=0.0, args={"ticker": "AAPL", "concept": "net income",
                                                                       "fiscal_year": 2025})], qtype="numerical")]}
    results.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "out.json"
    assert tm.main([str(results), "--out", str(out), "--no-facts"]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tool_metrics_version"] == tm.TOOL_METRICS_VERSION and data["source"]["run_id"] == "x-abc"
    assert data["labels"]["sha256"] == tm.file_sha256(tm.LABELS_FILE) and data["method"]["concept_check"].startswith("skipped")
    assert data["metrics"]["overall"]["first_tool_ok"] == {"n": 1, "of": 1, "rate": 1.0}
    assert "tool metrics v" in capsys.readouterr().out

    old = tmp_path / "old.json"
    old.write_text(json.dumps({"results": [{"id": "qa_0009", "tools_used": {FACT: 1}}]}), encoding="utf-8")
    skipped = tmp_path / "skipped.json"
    assert tm.main([str(old), "--out", str(skipped)]) == 0
    assert not skipped.exists() and "skipped" in capsys.readouterr().out


# ── the committed files, pinned to the baseline the upgrade is measured against ───────────────────────────────────

@pytest.fixture(scope="module")
def committed():
    cost = json.loads((tm.EVAL_DIR / "results" / "cost-v3-2d69cde009fc.json").read_text(encoding="utf-8"))
    contract = json.loads((tm.EVAL_DIR / "results" / "contract-v3-76b8f532c332.json").read_text(encoding="utf-8"))
    labels, _ = tm.load_labels()
    return tm.compute(cost, labels)["overall"], tm.compute(contract, labels)["overall"]


def test_cost_v3_reproduces_the_baseline_tool_counts(committed):
    ov, _ = committed
    assert ov["n_items"] == 71 and ov["tool_calls"] == 131
    assert ov["tools"] == {COMPUTE: 8, LIST: 6, FACT: 58, SEARCH: 59}
    assert ov["calls_per_item"] == {1: 46, 2: 8, 3: 7, 4: 6, 5: 3, 9: 1}
    assert ov["call_validity"]["failed_by_tool"] == {FACT: 7}
    assert ov["call_validity"]["overall"] == {"n": 124, "of": 131, "rate": 0.9466}
    assert ov["call_validity"]["items_with_a_failed_call"]["n"] == 6
    assert ov["more_calls_than_steps"]["ids"] == ["qa_0007", "qa_0012", "qa_0034", "qa_0043", "qa_0053", "qa_0060",
                                                  "qa_0062", "qa_0069"]
    assert ov["terminal_failures"] == {"n": 1, "recursion_limit": 1}
    assert ov["figure_primary"] == {"n": 45, "of": 45, "rate": 1.0}
    assert ov["batched"]["method"] == "heuristic"          # the file predates the start offsets


def test_contract_v3_reproduces_the_baseline_latency_and_cost(committed):
    _, ov = committed
    lat = ov["latency_excluding_terminal_failures"]
    assert lat["n"] == 70 and round(lat["p50_ms_linear"]) == 3690 and round(lat["p95_ms_linear"]) == 8972
    assert round(lat["mean_ms"]) == 4453
    assert round(ov["cost_usd"]["mean"], 5) == 0.00207 and round(ov["cost_usd"]["total"], 3) == 0.147
    assert ov["verification"] == {"verified": 70} and ov["model_calls"]["agent_mean"] == 2.68
    incl = ov["latency_including_terminal_failures"]
    assert incl["n"] == 71 and round(incl["p50_ms"] / 1000, 1) == 3.7 and round(incl["p95_ms"] / 1000, 1) == 9.1
