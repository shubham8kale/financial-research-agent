# tests/test_multi_turn_probe.py
#
# eval/multi_turn_probe.json and eval/run_multi_turn_probe.py.  The data is
# checked against data/facts.sqlite when it is present (every ground-truth
# figure must be the tagged value it cites); the runner is driven with a stub
# API, so no model, key or index is needed.

import logging
import sqlite3

import pytest

from agent.memory import ThreadMemory
from eval import run_multi_turn_probe as probe_mod
from eval.figure_match import figure_match

PROBE = probe_mod.load_probe()
FACTS_DB = probe_mod.REPO_ROOT / "data" / "facts.sqlite"


# ── the probe file ─────────────────────────────────────────────────────────────

def test_the_probe_has_eight_short_conversations_over_all_five_companies():
    convs = PROBE["conversations"]
    assert len(convs) == 8 and [c["id"] for c in convs] == [f"mt_0{i}" for i in range(1, 9)]
    assert all(2 <= len(c["turns"]) <= 3 for c in convs)
    tickers = {f["ticker"] for c in convs for t in c["turns"] for f in t["facts"]}
    assert tickers == {"AAPL", "MSFT", "GOOGL", "AMZN", "META"}


def test_every_first_turn_stands_alone_and_every_later_turn_depends_on_history():
    for conv in PROBE["conversations"]:
        first, *later = conv["turns"]
        assert first["depends_on_history"] is False and later and all(t["depends_on_history"] for t in later), conv["id"]
    n_follow = sum(1 for c in PROBE["conversations"] for t in c["turns"] if t["depends_on_history"])
    assert n_follow == 11                                           # the N every doc must state


def test_every_turn_carries_its_ground_truth_and_a_source():
    for conv in PROBE["conversations"]:
        for t in conv["turns"]:
            assert t["question"].strip() and t["ground_truth"].strip() and t["source"].strip() and t["facts"], conv["id"]
            fig = figure_match(t["ground_truth"], t["ground_truth"])      # the check can read its own ground truth
            assert fig["applicable"] and fig["figure_primary"], (conv["id"], t["ground_truth"])
    # a follow-up must not state what it leaves out: none names a company or a year the first turn supplied
    follow_up = {t["question"] for c in PROBE["conversations"] for t in c["turns"] if t["depends_on_history"]}
    assert "And Microsoft's?" in follow_up and all(len(q) < 60 for q in follow_up)


@pytest.mark.skipif(not FACTS_DB.exists(), reason="needs data/facts.sqlite (python -m ingestion.xbrl)")
def test_every_ground_truth_figure_is_the_tagged_value_it_cites():
    con = sqlite3.connect(f"file:{FACTS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    for conv in PROBE["conversations"]:
        for t in conv["turns"]:
            for fact in t["facts"]:
                row = con.execute("SELECT ticker, concept, fiscal_year, value FROM facts WHERE id=?", (fact["id"],)).fetchone()
                assert row is not None, (conv["id"], fact["id"])
                assert (row["ticker"], row["concept"], row["fiscal_year"]) == (fact["ticker"], fact["concept"], fact["fiscal_year"])
                value = row["value"] / 1e6 if "value_millions" in fact else row["value"]
                expected = fact.get("value_millions", fact.get("value"))
                assert value == pytest.approx(expected), (conv["id"], fact["id"], value, expected)
                shown = f"{expected:,.0f}" if "value_millions" in fact else f"{expected:.2f}"
                if "derived" not in t:
                    assert shown in t["ground_truth"], (conv["id"], shown, t["ground_truth"])
            if "derived" in t:
                d = t["derived"]
                result = (d["a_millions"] - d["b_millions"]) / d["b_millions"] * 100
                assert round(result, 2) == d["result"] and f"{d['result']:.2f}%" in t["ground_truth"]


# ── the plan and its cost ──────────────────────────────────────────────────────

def test_the_plan_asks_every_turn_with_memory_and_only_the_follow_ups_in_isolation():
    plan = probe_mod.query_plan(PROBE)
    memory = [s for s in plan if s["mode"] == "memory"]
    isolated = [s for s in plan if s["mode"] == "isolated"]
    assert (len(memory), len(isolated), len(plan)) == (19, 11, 30)
    assert all(s["thread"] == s["conversation"] for s in memory) and all(s["thread"] is None for s in isolated)
    assert all(s["depends_on_history"] for s in isolated)
    assert [s["conversation"] for s in probe_mod.query_plan(PROBE, ["mt_02"])] == ["mt_02"] * 3
    cost = probe_mod.expected_cost(30)
    assert cost["expected_usd"] == 0.075 and cost["counted_usd"] == 0.0938          # 30 x $0.0025, x 1.25


def test_thread_ids_satisfy_the_api_pattern_and_differ_per_conversation_and_label():
    import re
    ids = {probe_mod.thread_id_for(lab, c) for lab in ("memory v1!", "x") for c in ("mt_01", "mt_02")}
    assert len(ids) == 4 and all(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", i) for i in ids)
    assert len(probe_mod.thread_id_for("l" * 100, "mt_01")) == 64


# ── scoring ────────────────────────────────────────────────────────────────────

def test_a_refused_empty_or_wrong_answer_is_not_correct_and_a_right_figure_is():
    gt = "Microsoft's total revenue in fiscal 2025 was $281,724 million."
    assert probe_mod.score_turn(gt, "Microsoft's revenue was $281,724 million in fiscal 2025.", "verified")["correct"] is True
    assert probe_mod.score_turn(gt, "I could not verify $281,724 million.", "refused")["correct"] is False
    assert probe_mod.score_turn(gt, "", "verified")["correct"] is False and probe_mod.score_turn(gt, None, None)["correct"] is False
    assert probe_mod.score_turn(gt, "Microsoft's revenue was $245,122 million.", "verified")["correct"] is False
    assert probe_mod.score_turn("It grew 21.50% from fiscal 2024 to fiscal 2025.", "Growth was 21.5% year over year.", "verified")["correct"]


# ── the runner, against a stub API ─────────────────────────────────────────────

class StubAPI:
    """Answers a follow-up correctly only when its thread carries the earlier turn, like the real memory would."""

    def __init__(self, memory, fail=None):
        self.memory, self.fail, self.calls = memory, fail or {}, []

    def __call__(self, question, thread_id):
        self.calls.append((question, thread_id))
        conv = next(c for c in PROBE["conversations"] if any(t["question"] == question for t in c["turns"]))
        turn = next(t for t in conv["turns"] if t["question"] == question)
        history = self.memory.history(thread_id) if thread_id else []
        status = self.fail.get(question)
        if status:
            return status, {}
        answerable = (not turn["depends_on_history"]) or bool(history)
        answer = turn["ground_truth"] if answerable else "I cannot tell what you are referring to."
        if thread_id:
            self.memory.remember(thread_id, question, answer, "verified")
        return 200, {"answer": answer, "verification": {"status": "verified"},
                     "meta": {"latency_ms": 100.0, "llm_calls": 3, "cost_usd": 0.002, "tools": {}, "thread_turns": len(history)}}


def run_stub(**kw):
    memory = ThreadMemory()
    api = StubAPI(memory, kw.pop("fail", None))
    out = probe_mod.run_probe(api, PROBE, "stub-run", memory=memory, sleep=lambda s: None, **kw)
    return api, memory, out


def test_with_memory_every_follow_up_is_answered_and_in_isolation_none_are():
    api, _, out = run_stub()
    s = probe_mod.summarize(PROBE, out["turns"])
    assert out["stop_reason"] is None and len(api.calls) == 30
    assert s["follow_ups_correct_with_memory"] == {"n": 11, "of": 11} and s["follow_ups_correct_in_isolation"] == {"n": 0, "of": 11}
    assert s["first_turns_correct"] == {"n": 8, "of": 8} and s["n_follow_up_turns"] == 11 and s["n_first_turns"] == 8
    assert "N is small" in s["note"] and "11 follow-up turns" in s["note"]
    # thread ids: one per conversation with memory, none in isolation
    with_mem = {thread for q, thread in api.calls if thread}
    assert len(with_mem) == 8 and sum(1 for _, t in api.calls if t is None) == 11
    assert s["cost_usd_actual"] == pytest.approx(30 * 0.002) and s["cost_usd_counted"] == pytest.approx(30 * 0.002 * 1.25)
    row = next(r for r in s["per_conversation"] if r["id"] == "mt_01")
    assert [t["with_memory"] for t in row["turns"]] == [True, True, True]
    assert [t["in_isolation"] for t in row["turns"]] == [None, False, False]


def test_a_failed_request_is_recorded_and_scores_as_not_correct():
    api, _, out = run_stub(fail={"And in 2024?": 502})
    bad = [r for r in out["turns"] if r["http_status"] == 502]
    assert bad and all(r["answer"] is None and r["score"]["correct"] is False for r in bad)
    s = probe_mod.summarize(PROBE, out["turns"])
    assert s["http_errors"] == len(bad) and s["follow_ups_correct_with_memory"]["n"] < 11


def test_a_cached_turn_is_not_asked_again_and_its_answer_is_put_back_into_memory():
    api, _, out = run_stub(ids=["mt_01"])
    cache = {r["key"]: r for r in out["turns"] if r["conversation"] == "mt_01" and r["turn"] < 2 or r["mode"] == "isolated"}
    memory = ThreadMemory()
    api2 = StubAPI(memory)
    resumed = probe_mod.run_probe(api2, PROBE, "stub-run", memory=memory, ids=["mt_01"], cache=dict(cache), sleep=lambda s: None)
    assert [q for q, _ in api2.calls] == ["And Alphabet's?"]          # only the one turn that was not cached
    assert resumed["turns"][2]["score"]["correct"] is True              # it saw the cached turns through memory
    assert api2.calls[0][1] == probe_mod.thread_id_for("stub-run", "mt_01")


def test_a_spend_cap_stops_the_run_before_it_overspends():
    api, _, out = run_stub(cap_counted_usd=0.01)
    assert out["stop_reason"] and "spend cap" in out["stop_reason"] and 0 < len(api.calls) < 30


def test_a_quota_refusal_in_the_server_log_stops_the_run_at_once():
    memory = ThreadMemory()
    watch = probe_mod.QuotaWatch()
    base = StubAPI(memory)

    def api(question, thread_id):
        out = base(question, thread_id)
        if len(base.calls) == 3:
            record = logging.LogRecord("api.main", logging.ERROR, __file__, 1, "Direct agent failed", None,
                                       (RuntimeError, RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"), None))
            watch.emit(record)
        return out

    out = probe_mod.run_probe(api, PROBE, "stub-run", memory=memory, sleep=lambda s: None, quota_watch=watch)
    assert len(base.calls) == 3 and "money or key" in out["stop_reason"]


def test_the_quota_watch_ignores_ordinary_warnings_and_reads_exception_text():
    watch = probe_mod.QuotaWatch()
    watch.emit(logging.LogRecord("x", logging.WARNING, __file__, 1, "MCP agent timed out", None, None))
    assert watch.hit is None
    watch.emit(logging.LogRecord("x", logging.ERROR, __file__, 1, "Direct agent failed", None,
                                 (RuntimeError, RuntimeError("Quota exceeded for this project"), None)))
    assert watch.hit == "quota"


def test_the_dry_run_prints_the_plan_and_the_expected_cost_and_calls_no_model(capsys):
    assert probe_mod.main(["--dry-run", "--label", "dry"]) == 0
    out = capsys.readouterr().out
    assert "30 requests (19 with memory, 11 follow-ups in isolation)" in out and "$0.075 actual" in out
    assert "[memory  ] mt_01 turn 1" in out and "[isolated] mt_05 turn 2" in out


def test_a_run_over_the_cap_is_refused_before_it_starts(capsys):
    assert probe_mod.main(["--label", "x", "--cap-usd", "0.01"]) == 1
    assert "NOT RUN" in capsys.readouterr().out


def test_every_question_in_the_probe_is_unique():
    questions = [t["question"] for c in PROBE["conversations"] for t in c["turns"]]
    assert len(questions) == 19 and len(set(questions)) == 19
