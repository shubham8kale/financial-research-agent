# tests/test_eval_instrument.py
#
# Unit tests for the judge-free half of the evaluation instrument (upgrade 1):
#
#   agent/observations.py       one parser for tool observations, shared by the
#                               API's citations and the harness's contexts
#   eval/retrieval_metrics.py   hit/recall@k, MRR, nDCG over relevance GROUPS
#   eval/figure_match.py        ground-truth figures reproduced in the answer
#   eval/experiment.py          config hash, benchmark version, prior-run lookup
#   eval/chunk_labels.py        locating reference passages in the index
#   eval/leaderboard.py         rendering results files into LEADERBOARD.md
#
# No network, no API key, no index.  Everything here is pure, and the
# behaviours pinned are the ones that would silently corrupt a measurement:
# a passage that parses into the wrong chunk id, a recall that counts an
# overlap neighbour as a miss, a wrong-year figure that passes, a config hash
# that ignores the retriever, a leaderboard that mixes schema-2 and schema-3.

import json

import pytest

from agent.observations import (
    ObservedChunk,
    chunk_id,
    has_chunk_markers,
    observation_text,
    parse_chunk_id,
    parse_observation,
    retrieved_chunk_ids,
)
from eval import chunk_labels, leaderboard
from eval.experiment import benchmark_version, config_hash, find_existing_result
from eval.figure_match import extract_figures, figure_match
from eval.retrieval_metrics import (
    aggregate,
    group_hit_ranks,
    hit_at_k,
    item_metrics,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

SEARCH_OBS = (
    "[1] ticker=AAPL  chunk_idx=395\n"
    "    Apple Inc. | 2025 Form 10-K | 35 The following table shows disaggregated net sales\n"
    "\n"
    "[2] ticker=MSFT  chunk_idx=146\n"
    "    Azure is a comprehensive set of cloud services\n"
    "    that spans a second line\n"
)
COMPARE_OBS = (
    "=== AAPL ===\n"
    "  [1] chunk_idx=395\n"
    "      Total net sales 416,161\n"
    "\n"
    "  [2] chunk_idx=396\n"
    "      iPhone 209,586\n"
    "\n"
    "=== MSFT ===\n"
    "No results found for this ticker.\n"
)
TICKER_LIST_OBS = "AAPL, MSFT, GOOGL, AMZN, META"


# ── observations ─────────────────────────────────────────────────────────────

def test_chunk_id_round_trip():
    assert chunk_id("aapl", 395) == "AAPL_10K_chunk_395"
    assert parse_chunk_id("AAPL_10K_chunk_395") == ("AAPL", 395)
    assert parse_chunk_id("not-an-id") is None


def test_parse_search_filings_observation():
    chunks = parse_observation(SEARCH_OBS)
    assert [c.chunk_id for c in chunks] == ["AAPL_10K_chunk_395", "MSFT_10K_chunk_146"]
    assert chunks[0].text.startswith("Apple Inc. | 2025 Form 10-K")
    # multi-line snippets are joined into one passage
    assert chunks[1].text == "Azure is a comprehensive set of cloud services that spans a second line"


def test_parse_compare_companies_observation_takes_ticker_from_section():
    chunks = parse_observation(COMPARE_OBS)
    assert [c.chunk_id for c in chunks] == ["AAPL_10K_chunk_395", "AAPL_10K_chunk_396"]
    assert all(isinstance(c, ObservedChunk) for c in chunks)


def test_observation_without_markers_yields_no_chunks():
    assert parse_observation(TICKER_LIST_OBS) == []
    assert has_chunk_markers(TICKER_LIST_OBS) is False
    assert has_chunk_markers(SEARCH_OBS) is True


def test_observation_text_flattens_mcp_blocks():
    blocks = [{"type": "text", "text": "[1] ticker=AAPL  chunk_idx=1\n    hello"}, {"type": "image"}, "tail"]
    flat = observation_text(blocks)
    assert flat.startswith("[1] ticker=AAPL") and flat.endswith("tail")
    assert observation_text("plain") == "plain"


def test_retrieved_chunk_ids_dedupes_in_first_seen_order():
    ids = retrieved_chunk_ids([SEARCH_OBS, COMPARE_OBS, TICKER_LIST_OBS])
    assert ids == ["AAPL_10K_chunk_395", "MSFT_10K_chunk_146", "AAPL_10K_chunk_396"]


# ── retrieval metrics ────────────────────────────────────────────────────────

def test_group_is_satisfied_by_any_member():
    ranked = ["A", "B", "C"]
    # overlap neighbours B and X both hold the passage; retrieving B is a hit
    assert group_hit_ranks(ranked, [["X", "B"]]) == [2]
    assert group_hit_ranks(ranked, [["X"]]) == [None]


def test_recall_counts_groups_not_chunks():
    ranks = group_hit_ranks(["A", "B"], [["A", "Z"], ["Q"]])
    assert recall_at_k(ranks, 5) == 0.5
    assert hit_at_k(ranks, 5) == 1.0
    assert hit_at_k(ranks, 0) == 0.0


def test_mrr_and_ndcg():
    assert reciprocal_rank([3, None]) == pytest.approx(1 / 3)
    assert reciprocal_rank([None]) == 0.0
    # single group at rank 1 is a perfect ranking
    assert ndcg_at_k([1], 5) == pytest.approx(1.0)
    # two groups, found at ranks 1 and 3: dcg = 1 + 1/log2(4); ideal = 1 + 1/log2(3)
    assert ndcg_at_k([1, 3], 5) == pytest.approx((1 + 0.5) / (1 + 1 / 1.5849625), rel=1e-6)
    # a hit beyond k contributes nothing
    assert ndcg_at_k([7], 5) == 0.0


def test_item_metrics_and_aggregate_keys():
    m = item_metrics(["A", "B", "C", "D", "E", "F"], [["C"], ["Z"]])
    assert m["hit@5"] == 1.0 and m["recall@5"] == 0.5 and m["first_hit_rank"] == 3
    assert m["recall@25"] == 0.5  # Z is never retrieved
    agg = aggregate([m, item_metrics(["Z"], [["Z"]])])
    assert agg["n_items"] == 2
    assert agg["hit@5"] == 1.0 and agg["recall@5"] == 0.75
    assert aggregate([])["n_items"] == 0 and aggregate([])["mrr"] is None


# ── figure match ─────────────────────────────────────────────────────────────

def test_extract_figures_handles_currency_scale_percent_and_dates():
    figs = extract_figures("Revenue was $200.97 billion, up 22% for the year ended December 31, 2025.")
    assert [f.raw for f in figs] == ["$200.97 billion", "22%", "2025"]
    assert figs[0].scaled == pytest.approx(200.97e9) and figs[0].unscaled == 200.97
    assert figs[1].percent is True


def test_figure_match_scaled_equivalence_and_unit_omission():
    assert figure_match("$1.2 billion", "It was $1,200 million.")["figure_exact"] is True
    assert figure_match("$128,725 million", "AWS net sales were 128,725 in 2025.")["figure_exact"] is True


def test_figure_match_catches_prior_year_figure():
    gt = "Google Cloud revenues were $58,705 million for the year ended December 31, 2025"
    wrong = figure_match(gt, "Google Cloud revenue was $43,229 million in 2025.")
    assert wrong["figure_exact"] is False and wrong["missing"] == ["$58,705 million"]
    assert wrong["figure_recall"] == 0.5
    right = figure_match(gt, "Google Cloud revenues were $58,705 million in 2025.")
    assert right["figure_exact"] is True and right["figure_recall"] == 1.0


def test_figure_match_percent_kind_is_not_confused_with_plain_number():
    assert figure_match("22%", "growth of 22 units")["figure_exact"] is False
    assert figure_match("22%", "growth of 22 percent")["figure_exact"] is True


def test_figure_match_not_applicable_without_figures():
    out = figure_match("Apple is incorporated in California.", "California")
    assert out["applicable"] is False and out["figure_exact"] is None and out["figure_recall"] is None


def test_form_and_item_numbers_are_not_figures():
    assert extract_figures("The 10-K does not disclose it; see Item 7A and Form 8-K, Note 4.") == []
    # a real figure next to a form name still counts
    assert [f.raw for f in extract_figures("Per the 10-K, revenue was $416,161 million in 2025.")] == [
        "$416,161 million", "2025",
    ]
    gt = "I cannot find a specific 2025 Reality Labs revenue figure in the provided 10-K."
    out = figure_match(gt, "Reality Labs revenue was $2,207 million in 2025.")
    assert out["n_expected"] == 1 and out["figure_exact"] is True   # only 2025 is a figure here


# ── experiment bookkeeping ───────────────────────────────────────────────────

def test_config_hash_is_order_independent_and_sensitive_to_retriever():
    a = {"agent_model": "m", "retrieval": {"mode": "dense", "k": 5}}
    b = {"retrieval": {"k": 5, "mode": "dense"}, "agent_model": "m"}
    assert config_hash(a) == config_hash(b)
    assert config_hash(a) != config_hash({**a, "retrieval": {"mode": "hybrid", "k": 5}})
    assert len(config_hash(a)) == 12


def test_benchmark_version_changes_with_content(tmp_path):
    bench = tmp_path / "b.csv"
    bench.write_text("id,question\nqa_1,what\n", encoding="utf-8")
    v1 = benchmark_version(bench)
    bench.write_text("id,question\nqa_1,what else\n", encoding="utf-8")
    assert benchmark_version(bench) != v1
    assert v1.startswith("sha256:")


def test_find_existing_result_ignores_partial_runs(tmp_path):
    (tmp_path / "partial.json").write_text(json.dumps({
        "config": {"config_hash": "abc"}, "run_status": {"complete": False}}), encoding="utf-8")
    assert find_existing_result(tmp_path, "abc") is None
    (tmp_path / "done.json").write_text(json.dumps({
        "config": {"config_hash": "abc"}, "run_status": {"complete": True}}), encoding="utf-8")
    assert find_existing_result(tmp_path, "abc").name == "done.json"
    assert find_existing_result(tmp_path / "missing", "abc") is None


# ── chunk labels ─────────────────────────────────────────────────────────────

CHUNKS = [
    ("AAPL_10K_chunk_1", chunk_labels.normalize(
        "Apple Inc. (Exact name of Registrant) California 94-2404110 (State of incorporation) 95014")),
    ("AMZN_10K_chunk_5", chunk_labels.normalize("AWS 90,757 107,556 128,725 Total")),
    ("AMZN_10K_chunk_6", chunk_labels.normalize("segment: AWS 90,757 107,556 128,725")),
]


def test_split_references_accepts_both_separators():
    assert chunk_labels.split_references("a || b ;; c") == ["a", "b", "c"]
    assert chunk_labels.split_references("") == []


def test_locate_exact_groups_all_matching_chunks():
    ids, method = chunk_labels.locate("AWS 90,757   107,556 128,725", CHUNKS)
    assert method == "exact" and sorted(ids) == ["AMZN_10K_chunk_5", "AMZN_10K_chunk_6"]


def test_locate_falls_back_to_prefix_and_records_it():
    ref = "Apple Inc. (Exact name of Registrant) California 94-2404110 (State or other jurisdiction)"
    ids, method = chunk_labels.locate(ref, CHUNKS)
    assert method == "prefix" and ids == ["AAPL_10K_chunk_1"]
    assert chunk_labels.locate("nothing like this at all in the index", CHUNKS) == ([], "none")


def test_label_item_methods_and_overrides():
    row = {"id": "qa_1", "reference_contexts": "AWS 90,757 107,556 128,725 || missing passage"}
    item = chunk_labels.label_item(row, CHUNKS)
    assert item["method"] == "partial" and len(item["groups"]) == 1 and item["n_references"] == 2
    manual = chunk_labels.label_item(row, CHUNKS, {"groups": [["X_10K_chunk_1"]], "note": "by hand"})
    assert manual["method"] == "manual" and manual["groups"] == [["X_10K_chunk_1"]]
    gone = chunk_labels.label_item(row, CHUNKS, {"unlocatable": True, "note": "not in index"})
    assert gone["method"] == "unlocatable" and gone["groups"] == []
    summary = chunk_labels.summarize({"a": item, "b": manual, "c": gone})
    assert summary == {"n_items": 3, "by_method": {"manual": 1, "partial": 1, "unlocatable": 1},
                       "n_scorable": 2, "n_multi_chunk_groups": 1}


def test_scorable_groups_reads_nested_labels():
    labels = {"items": {"qa_1": {"groups": [["A"]]}, "qa_2": {"groups": []}}}
    assert chunk_labels.scorable_groups(labels, "qa_1") == [["A"]]
    assert chunk_labels.scorable_groups(labels, "qa_2") == []
    assert chunk_labels.scorable_groups(labels, "qa_9") == []


# ── leaderboard ──────────────────────────────────────────────────────────────

def _gen_payload(schema, cfg_hash, faith):
    return {
        "schema_version": schema, "run_id": f"run-{cfg_hash}", "timestamp": "2026-09-27T00:00:00+00:00",
        "config": {"agent_model": "agent-x", "judge_model": "judge-y", "judge_provider": "google",
                   "config_hash": cfg_hash},
        "run_status": {"complete": True},
        "aggregates": {"overall": {
            "n_items": 3,
            "faithfulness": {"mean_failures_as_zero": faith},
            "answer_relevancy": {"mean_failures_as_zero": 0.5},
            "context_recall": {"mean_failures_as_zero": 0.25},
            "deterministic": {"figure_exact_rate": 0.6667, "agent_hit_rate": 1.0},
        }},
        "results": [],
    }


def test_leaderboard_separates_schema_3_retrieval_and_legacy(tmp_path):
    (tmp_path / "new.json").write_text(json.dumps(_gen_payload(3, "aaa", 0.9)), encoding="utf-8")
    (tmp_path / "old.json").write_text(json.dumps(_gen_payload(2, "bbb", 0.8)), encoding="utf-8")
    (tmp_path / "ret.json").write_text(json.dumps({
        "schema_version": 3, "result_kind": "retrieval", "run_id": "retrieval-dense-ccc",
        "timestamp": "2026-09-27T00:00:00+00:00",
        "config": {"retrieval": {"mode": "dense", "k": 25, "ticker_filter": False}, "config_hash": "ccc"},
        "run_status": {"complete": True},
        "aggregates": {"overall": {"n_items": 69, "hit@5": 0.7, "recall@5": 0.65, "mrr": 0.5,
                                   "ndcg@5": 0.55, "recall@25": 0.9},
                       "latency_ms": {"p50": 12.5, "p95": 30.0}},
        "results": [],
    }), encoding="utf-8")
    (tmp_path / "partial.json").write_text(json.dumps({
        "schema_version": 3, "run_status": {"complete": False}, "aggregates": None,
        "aggregates_withheld_reason": "stopped at item 4"}), encoding="utf-8")
    (tmp_path / "junk.json").write_text("{not json", encoding="utf-8")

    out = leaderboard.write_leaderboard(tmp_path)
    text = out.read_text(encoding="utf-8")
    gen_section = text.split("## Retrieval runs")[0]
    legacy_section = text.split("## Legacy generation runs")[1]
    assert "run-aaa" in gen_section and "run-bbb" not in gen_section
    assert "run-bbb" in legacy_section
    assert "retrieval-dense-ccc" in text and "0.6500" in text and "12.5" in text
    assert "0.6667" in gen_section  # figure_exact reaches the table
    assert "stopped at item 4" in text
