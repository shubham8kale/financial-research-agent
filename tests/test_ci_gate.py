# tests/test_ci_gate.py
#
# The CI quality gate (eval/ci_gate.py).  Pinned here: a threshold miss fails and a pass passes,
# with the committed value carried alongside; the retrieval gate scores every
# configuration in the gate file and writes per-item evidence; the judged gate
# refuses an incomplete or differently-configured run; and the committed gate
# file is consistent with the committed results it was calibrated from — so a
# threshold can never be edited above what was measured.

import json
from pathlib import Path

from eval import ci_gate

REPO = Path(__file__).resolve().parent.parent


def _rows(n=10):
    return [{"id": f"qa_{i:04d}", "question": f"q{i}", "ticker": "AAPL", "question_type": "numerical",
             "difficulty": "easy", "requires_table": "False"} for i in range(1, n + 1)]


def _labels(n=10):
    return {"index_chunk_count": 100,
            "items": {f"qa_{i:04d}": {"groups": [[f"AAPL_10K_chunk_{i}"]]} for i in range(1, n + 1)}}


# ── compare / render ─────────────────────────────────────────────────────────

def test_compare_marks_misses_and_carries_the_committed_value():
    checks = ci_gate.compare("dense", {"hit@5": 0.5, "mrr": 0.3}, {"hit@5": 0.48, "mrr": 0.35}, committed={"hit@5": 0.507})
    assert [c.passed for c in checks] == [True, False]
    assert checks[0].committed == 0.507 and checks[1].committed is None
    text = ci_gate.render("t", checks, ["a note"])
    assert "| dense | hit@5 | 0.507 | 0.500 | ≥ 0.480 | PASS |" in text
    assert "| dense | mrr | — | 0.300 | ≥ 0.350 | FAIL |" in text
    assert "**FAIL** — 1 of 2 checks passed." in text and "- a note" in text


def test_a_metric_the_run_did_not_produce_fails_rather_than_passing_vacuously():
    (check,) = ci_gate.compare("x", {}, {"hit@5": 0.1})
    assert check.value is None and not check.passed


def test_lower_is_better_metrics_compare_the_other_way():
    (check,) = ci_gate.compare("judged", {"terminal_failures": 2}, {"terminal_failures": 1})
    assert not check.passed and not check.higher_is_better
    assert "≤ 1" in ci_gate.render("t", [check])


# ── retrieval gate ───────────────────────────────────────────────────────────

def test_retrieval_gate_scores_every_configuration_and_writes_evidence(tmp_path):
    gate = {"retrieval": {"k": 25, "configs": [
        {"label": "good", "retrieval": {"mode": "dense", "k": 25}, "thresholds": {"hit@5": 0.5}},
        {"label": "bad", "retrieval": {"mode": "dense", "k": 25, "rerank": True}, "thresholds": {"hit@5": 0.95}},
    ]}}
    seen = []

    def factory(rc_dict, k):
        seen.append(rc_dict)

        def fn(question, k, ticker):   # the labelled chunk ranks first on even items, never on odd ones
            i = int(question[1:])
            return [f"AAPL_10K_chunk_{i}"] if i % 2 == 0 else ["AAPL_10K_chunk_999"]
        return fn

    passed, summary, checks = ci_gate.run_retrieval_gate(gate, tmp_path, retriever_factory=factory,
                                                         rows=_rows(), labels=_labels())
    assert not passed
    assert [c["rerank"] if "rerank" in c else False for c in seen] == [False, True]
    by = {(c.scope, c.metric): c for c in checks}
    assert by[("good", "hit@5")].passed and by[("good", "hit@5")].value == 0.5
    assert not by[("bad", "hit@5")].passed and by[("bad", "hit@5")].value == 0.5
    payload = json.loads((tmp_path / "retrieval-ci-good.json").read_text(encoding="utf-8"))
    assert payload["result_kind"] == "retrieval" and payload["config"]["gate"] == "ci"
    assert payload["config"]["retrieval"]["rerank"] is False
    assert payload["aggregates"]["overall"]["hit@5"] == 0.5 and len(payload["results"]) == 10
    assert "| good | hit@5 |" in summary and "| bad | hit@5 |" in summary


def test_emit_summary_appends_to_the_github_step_summary(tmp_path, monkeypatch, capsys):
    target = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    ci_gate.emit_summary("### hello\n")
    assert target.read_text(encoding="utf-8").startswith("### hello")
    assert "### hello" in capsys.readouterr().out


# ── judged gate ──────────────────────────────────────────────────────────────

def _judged_payload(gate, **overrides):
    """The committed calibration run restricted to the gate's subset."""
    with open(REPO / gate["judged"]["calibrated_from"], encoding="utf-8") as f:
        payload = json.load(f)
    sub = ci_gate.subset_payload(payload, gate["judged"]["item_ids"])
    sub.update(overrides)
    return sub


def test_judged_gate_passes_the_run_it_was_calibrated_from():
    gate = ci_gate.load_gate()
    passed, summary, checks = ci_gate.run_judged_gate(gate, _judged_payload(gate))
    assert passed, summary
    assert {c.metric for c in checks} == {"faithfulness", "answer_relevancy", "context_recall",
                                          "figure_primary_rate", "terminal_failures", "unexplained_nan"}


def test_judged_gate_fails_when_faithfulness_collapses():
    gate = ci_gate.load_gate()
    sub = _judged_payload(gate)
    for r in sub["results"]:
        r["scores"]["faithfulness"] = 0.0
    sub = ci_gate.subset_payload(sub, gate["judged"]["item_ids"])   # rebuild the aggregates
    passed, _, checks = ci_gate.run_judged_gate(gate, sub)
    assert not passed
    assert [c.metric for c in checks if not c.passed] == ["faithfulness"]


def test_judged_gate_refuses_an_incomplete_run():
    gate = ci_gate.load_gate()
    sub = _judged_payload(gate)
    sub["run_status"]["complete"] = False
    sub["aggregates_withheld_reason"] = "scored 7 of 10 requested items"
    passed, summary, _ = ci_gate.run_judged_gate(gate, sub)
    assert not passed and "scored 7 of 10" in summary


def test_judged_gate_refuses_a_run_of_a_different_configuration():
    gate = ci_gate.load_gate()
    sub = _judged_payload(gate)
    sub["config"]["retrieval"]["rerank"] = False
    sub["config"]["agent_model"] = "gemini-2.5-flash-lite"
    passed, summary, _ = ci_gate.run_judged_gate(gate, sub)
    assert not passed
    assert "retrieval.rerank is False" in summary and "agent_model is 'gemini-2.5-flash-lite'" in summary


def test_judged_gate_refuses_a_run_under_another_verify_mode():
    gate = ci_gate.load_gate()
    sub = _judged_payload(gate)
    sub["config"]["verify_mode"] = "strict"
    sub["config"]["contract_version"] = "abc"
    passed, summary, _ = ci_gate.run_judged_gate(gate, sub)
    assert not passed and "verify_mode is 'strict', calibrated on 'off'" in summary


def test_judged_values_count_agent_errors_and_unexplained_nans():
    gate = ci_gate.load_gate()
    sub = _judged_payload(gate)
    assert ci_gate.judged_values(sub)["unexplained_nan"] == 0
    # one item errored inside the agent (recorded, not a terminal-failure outcome) and one judge NaN with no cause
    sub["results"][0]["error"] = "boom"
    sub["results"][1]["scores"]["faithfulness"] = None
    sub = ci_gate.subset_payload(sub, gate["judged"]["item_ids"])
    values = ci_gate.judged_values(sub)
    assert values["terminal_failures"] == 1 and values["unexplained_nan"] == 1
    passed, summary, checks = ci_gate.run_judged_gate(gate, sub)
    assert not passed
    assert "| judged | unexplained_nan | — | 1 | ≤ 0 | FAIL |" in summary


def test_judged_gate_warns_but_does_not_fail_on_another_judge():
    gate = ci_gate.load_gate()
    sub = _judged_payload(gate)
    sub["config"]["judge_model"] = "openai/gpt-oss-120b"
    passed, summary, _ = ci_gate.run_judged_gate(gate, sub)
    assert passed and "WARNING" in summary


# ── the committed gate file ──────────────────────────────────────────────────

def test_committed_gate_file_is_consistent_with_the_committed_evidence():
    gate = ci_gate.load_gate()
    assert ci_gate.validate_gate(gate) == []
    # every retrieval threshold names a committed file whose value it sits under
    for cfg in gate["retrieval"]["configs"]:
        committed = ci_gate.committed_overall(cfg["committed"])
        for metric, minimum in cfg["thresholds"].items():
            assert minimum <= committed[metric], (cfg["label"], metric)


def test_validate_gate_reports_a_threshold_above_its_calibration():
    gate = ci_gate.load_gate()
    gate["retrieval"]["configs"][0]["thresholds"]["hit@5"] = 0.99
    gate["judged"]["thresholds"]["faithfulness"] = 1.01
    gate["judged"]["expect"]["verify_mode"] = "strict"    # the calibration run predates the contract
    problems = ci_gate.validate_gate(gate)
    assert any("dense: threshold hit@5 0.99 is above" in p for p in problems)
    assert any("judged: threshold faithfulness 1.01 is above" in p for p in problems)
    assert any("calibration run does not match `expect`: verify_mode is 'off'" in p for p in problems)
