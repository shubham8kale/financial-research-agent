# tests/test_eval_harness.py
#
# Tests for the parts of eval/run_eval.py that changed in results schema 3:
# observations are captured raw, contexts are split per chunk, cached schema-2
# records are upgraded in place, and the judge-free metrics are attached and
# aggregated.  No agent, no judge, no index — records are built by hand.
#
# The behaviour pinned first is the one that motivated the change: a cached
# record from the old harness (contexts = whole observation blobs) must yield
# the same per-chunk contexts a fresh run would, or the re-scored baseline is
# not a baseline.

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from eval import run_eval

OBS_A = (
    "[1] ticker=AAPL  chunk_idx=395\n"
    "    Total net sales were $416,161 million in fiscal 2025.\n"
    "\n"
    "[2] ticker=AAPL  chunk_idx=396\n"
    "    iPhone net sales were $209,586 million.\n"
)
OBS_B = "AAPL, MSFT, GOOGL, AMZN, META"

LABELS = {"items": {
    "qa_hit": {"groups": [["AAPL_10K_chunk_396", "AAPL_10K_chunk_397"]]},
    "qa_miss": {"groups": [["MSFT_10K_chunk_1"]]},
}}


def _messages():
    return [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "search_filings", "args": {"query": "x"}, "id": "t1"}]),
        ToolMessage(content=OBS_A, tool_call_id="t1"),
        AIMessage(content="", tool_calls=[{"name": "list_available_companies", "args": {}, "id": "t2"}]),
        ToolMessage(content=[{"type": "text", "text": OBS_B}], tool_call_id="t2"),
        AIMessage(content="Total net sales were $416,161 million."),
    ]


def test_extract_observations_keeps_every_tool_message_verbatim():
    obs = run_eval._extract_observations(_messages())
    assert obs == [OBS_A, OBS_B]


def test_split_contexts_is_per_chunk_and_drops_passage_free_observations():
    ctx = run_eval.split_contexts([OBS_A, OBS_B])
    # provenance stays on the context: the agent saw the ticker in the header
    assert ctx == [
        "[AAPL 10-K, chunk 395] Total net sales were $416,161 million in fiscal 2025.",
        "[AAPL 10-K, chunk 396] iPhone net sales were $209,586 million.",
    ]


def test_record_stores_observations_contexts_and_chunk_ids():
    row = {"id": "qa_hit", "question": "q", "ground_truth": "$209,586 million", "question_type": "numerical"}
    rec = run_eval._record(row, "agent-x", "sha256:abc", answer="iPhone net sales were $209,586 million.",
                           observations=[OBS_A, OBS_B], n_messages=6)
    assert rec["n_observations"] == 2 and rec["n_contexts"] == 2
    assert rec["retrieved_chunk_ids"] == ["AAPL_10K_chunk_395", "AAPL_10K_chunk_396"]
    assert rec["terminal_failure"] is None


def test_upgrade_record_derives_chunk_view_from_schema_2_cache_entry():
    old = {"id": "qa_hit", "answer": "a", "ground_truth": "g", "contexts": [OBS_A, OBS_B], "n_contexts": 2}
    new = run_eval._upgrade_record(old)
    assert new["observations"] == [OBS_A, OBS_B]
    assert new["contexts"] == run_eval.split_contexts([OBS_A, OBS_B])
    assert new["retrieved_chunk_ids"] == ["AAPL_10K_chunk_395", "AAPL_10K_chunk_396"]
    assert old["contexts"] == [OBS_A, OBS_B]  # the cache entry itself is untouched
    # a record cached before the guard existed gets its terminal state classified
    assert new["terminal_failure"] is None and new["recursion_limit_hit"] is False
    limit = run_eval._upgrade_record({"id": "x", "answer": "Sorry, need more steps to process this request.",
                                      "ground_truth": "g", "contexts": []})
    assert limit["recursion_limit_hit"] is True
    # already-upgraded records pass through unchanged
    assert run_eval._upgrade_record(new)["contexts"] == new["contexts"]


def test_attach_deterministic_metrics_scores_figures_and_agent_hits():
    records = [
        {"id": "qa_hit", "ground_truth": "$209,586 million", "answer": "iPhone: $209,586 million",
         "retrieved_chunk_ids": ["AAPL_10K_chunk_395", "AAPL_10K_chunk_396"]},
        {"id": "qa_miss", "ground_truth": "$58,705 million", "answer": "$43,229 million",
         "retrieved_chunk_ids": ["AAPL_10K_chunk_395"]},
        {"id": "qa_nolabel", "ground_truth": "California", "answer": "California",
         "retrieved_chunk_ids": []},
    ]
    run_eval.attach_deterministic_metrics(records, LABELS)
    hit, miss, nolabel = records
    assert hit["figure"]["figure_exact"] is True
    assert hit["agent_retrieval"] == {"labelled": True, "n_groups": 1, "hit": True,
                                      "recall": 1.0, "mrr": 0.5, "first_rank": 2}
    assert miss["figure"]["figure_exact"] is False and miss["figure"]["missing"] == ["$58,705 million"]
    assert miss["agent_retrieval"]["hit"] is False and miss["agent_retrieval"]["recall"] == 0.0
    assert nolabel["figure"]["applicable"] is False
    assert nolabel["agent_retrieval"]["labelled"] is False and nolabel["agent_retrieval"]["hit"] is None

    block = run_eval._deterministic_block(records)
    assert block["n_figure_applicable"] == 2 and block["figure_exact_rate"] == 0.5 and block["figure_primary_rate"] == 0.5
    assert block["n_labelled"] == 2 and block["agent_hit_rate"] == 0.5 and block["agent_mrr"] == 0.25


def test_attach_deterministic_metrics_without_labels_marks_unlabelled():
    records = [{"id": "qa_hit", "ground_truth": "1", "answer": "1", "retrieved_chunk_ids": ["A"]}]
    run_eval.attach_deterministic_metrics(records, None)
    assert records[0]["agent_retrieval"]["labelled"] is False
    assert run_eval._deterministic_block(records)["agent_hit_rate"] is None


def test_mean_block_carries_deterministic_block():
    rows = [{"id": "x", "scores": {"faithfulness": 1.0, "answer_relevancy": 0.5, "context_recall": None},
             "figure": {"applicable": True, "figure_recall": 1.0, "figure_exact": True},
             "agent_retrieval": {"labelled": True, "hit": True, "recall": 1.0, "mrr": 1.0}}]
    block = run_eval._mean_block(rows)
    assert block["faithfulness"]["mean"] == 1.0
    assert block["deterministic"]["figure_exact_rate"] == 1.0
    assert block["deterministic"]["agent_hit_rate"] == 1.0


def test_schema_and_unhashed_keys():
    assert run_eval.SCHEMA_VERSION == 3
    # throttling knobs must never change a config hash
    assert {"judge_requests_per_second", "agent_sleep_seconds"} <= set(run_eval._UNHASHED_CONFIG_KEYS)


# ── the generate / cache / stop loop ─────────────────────────────────────────

def _bench(n=3):
    return [{"id": f"qa_{i:04d}", "question": f"q{i}", "ground_truth": "$1 million", "question_type": "numerical",
             "ticker": "AAPL"} for i in range(1, n + 1)]


def _fake_capture(fail_on=None, exc=None):
    calls = []

    def capture(question, verify_mode="off"):
        calls.append((question, verify_mode))
        if question == fail_on:
            raise exc
        return (f"answer to {question}", ["[1] ticker=AAPL  chunk_idx=1\n    text says $1 million"], 3, {"meter": None})
    return capture, calls


def test_generate_outputs_reuses_cached_items_and_checkpoints_every_new_one(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "AGENT_SLEEP_SECONDS", 0)
    capture, calls = _fake_capture()
    monkeypatch.setattr(run_eval, "run_agent_capture", capture)
    tag = "strict-abcd1234"
    cached_key = run_eval._cache_key("qa_0001", "m", "p", "", tag)
    cache = {cached_key: {"id": "qa_0001", "question": "q1", "ground_truth": "$1 million", "answer": "cached",
                          "observations": [], "question_type": "numerical"}}
    cache_path = tmp_path / "cache.json"
    records, stop = run_eval.generate_outputs(_bench(3), "m", "p", cache, cache_path=cache_path,
                                              verify_mode="strict", contract_tag=tag)
    assert stop is None
    assert [c for c, _ in calls] == ["q2", "q3"] and all(m == "strict" for _, m in calls)
    assert records[0]["answer"] == "cached" and records[1]["answer"] == "answer to q2"
    on_disk = json.loads(cache_path.read_text(encoding="utf-8"))
    assert set(on_disk) == {run_eval._cache_key(f"qa_000{i}", "m", "p", "", tag) for i in (1, 2, 3)}
    # an entry cached under the untagged key (a run without the contract) is a different configuration
    assert run_eval._cache_key("qa_0001", "m", "p") not in on_disk


def test_generate_outputs_stops_on_a_quota_wall_without_caching_a_stub(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "AGENT_SLEEP_SECONDS", 0)
    capture, _ = _fake_capture(fail_on="q2", exc=RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"))
    monkeypatch.setattr(run_eval, "run_agent_capture", capture)
    cache_path = tmp_path / "cache.json"
    records, stop = run_eval.generate_outputs(_bench(3), "m", "p", {}, cache_path=cache_path)
    assert [r["id"] for r in records] == ["qa_0001"]
    assert stop and "quota" in stop and "qa_0002" in stop
    on_disk = json.loads(cache_path.read_text(encoding="utf-8"))
    assert list(on_disk) == [run_eval._cache_key("qa_0001", "m", "p")]      # no stub for the item that hit the wall


def test_generate_outputs_records_a_non_quota_failure_and_continues(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "AGENT_SLEEP_SECONDS", 0)
    capture, _ = _fake_capture(fail_on="q2", exc=ValueError("boom"))
    monkeypatch.setattr(run_eval, "run_agent_capture", capture)
    records, stop = run_eval.generate_outputs(_bench(3), "m", "p", {}, cache_path=tmp_path / "cache.json")
    assert stop is None and [r["id"] for r in records] == ["qa_0001", "qa_0002", "qa_0003"]
    assert records[1]["error"] == "boom" and records[1]["answer"].startswith("<agent error")
    assert records[1]["observations"] == [] and records[2]["answer"] == "answer to q3"


def test_record_reports_the_k_the_tools_retrieve(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_K", "7")
    rec = run_eval._record(_bench(1)[0], "m", "p", answer="a", observations=[], n_messages=0)
    assert rec["k"] == 7
    monkeypatch.delenv("RETRIEVAL_K")
    assert run_eval._record(_bench(1)[0], "m", "p", answer="a", observations=[], n_messages=0)["k"] == 5
