# tests/test_contract.py
#
# The output contract and the fail-closed verifier (agent/contract.py), and
# their wiring into the API and the eval harness.  Pinned here: each
# deterministic check by name; that a claim may round its source but not be
# more precise than it; that years are exempt; one repair then refuse (strict)
# or flag (warn) or skip (off); that the structuring calls are metered with
# the agent's own; that /query and the stream carry the verdict and mark the
# cited sources; and that the harness tags its cache and scores the draft
# apart from what was served.  No model, key or index is needed: the
# structuring model is a fake that returns the records the test scripts.

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from agent import contract
from agent.contract import AnswerContract, Claim, Verification
from api import main as api_main
from eval import run_eval

CHUNK = "[1] ticker=AAPL  chunk_idx=42\n    Total net sales were $416,161 million in fiscal 2025, up 6% from $391,035 million."
FACT = "[1] ticker=AMZN  fact_id=7\n    AWS net sales, FY2025: $128,725 million (us-gaap:Revenues, segment AWS)"
CALC = "[1] calc=pct_change\n    pct_change with a=128725, b=107556 = 19.68"
OBS = [CHUNK, FACT, CALC]


def _contract(*claims, not_disclosed=False):
    return AnswerContract(claims=[Claim(text=t, sources=list(s)) for t, s in claims], not_disclosed=not_disclosed)


class _FakeStructured:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def invoke(self, messages, config=None):
        self.calls.append((messages, config))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeLLM:
    """with_structured_output(...) returns a queue of scripted results; each is (parsed, raw, error)."""

    def __init__(self, *results):
        self.structured = _FakeStructured(
            [r if isinstance(r, Exception) else {"parsed": r[0], "raw": r[1], "parsing_error": r[2] if len(r) > 2 else None}
             for r in results])

    def with_structured_output(self, schema, include_raw=False):
        assert schema is AnswerContract and include_raw
        return self.structured


def _raw(tokens_in=900, tokens_out=80):
    return AIMessage(content="", usage_metadata={"input_tokens": tokens_in, "output_tokens": tokens_out,
                                                 "total_tokens": tokens_in + tokens_out})


# ── observations and checks ──────────────────────────────────────────────────

def test_observations_by_id_keys_chunks_facts_and_calcs():
    obs = contract.observations_by_id(OBS)
    assert set(obs) == {"AAPL_10K_chunk_42", "AMZN_10K_fact_7", "calc_pct_change"}
    assert "$416,161 million" in obs["AAPL_10K_chunk_42"] and "19.68" in obs["calc_pct_change"]


def test_a_record_whose_figures_are_in_its_cited_sources_verifies():
    obs = contract.observations_by_id(OBS)
    c = _contract(("Apple's total net sales were $416,161 million in fiscal 2025.", ["AAPL_10K_chunk_42"]),
                  ("AWS net sales grew 19.68% to $128,725 million.", ["AMZN_10K_fact_7", "calc_pct_change"]))
    failures, stats = contract.check_contract(c, "Apple's total net sales were $416,161 million in fiscal 2025. "
                                                 "AWS net sales grew 19.68% to $128,725 million.", obs)
    assert failures == []
    assert stats == {"n_claims": 2, "n_figures": 3, "n_supported": 3}   # the year is not a figure to support


def test_each_failure_is_named():
    obs = contract.observations_by_id(OBS)
    c = _contract(("Net sales were $416,161 million.", ["AAPL_10K_chunk_99"]),        # not retrieved this turn
                  ("Operating income was $133,000 million.", []),                       # a figure with no source
                  ("AWS net sales were $128,725 million.", ["AAPL_10K_chunk_42"]))     # wrong source for the figure
    draft = "Net sales were $416,161 million. Operating income was $133,000 million. AWS net sales were $128,725 million. EPS was $7.46."
    failures, stats = contract.check_contract(c, draft, obs)
    checks = [(f["check"], f["detail"]) for f in failures]
    assert ("unknown_source", "AAPL_10K_chunk_99") in checks
    assert ("uncited_figure", "$133,000 million") in checks
    assert ("unsupported_figure", "$128,725 million") in checks
    assert ("dropped_figure", "$7.46") in checks                                        # in the draft, in no claim
    # the claim with the unknown source still has its figure checked against nothing → unsupported too
    assert ("unsupported_figure", "$416,161 million") in checks
    assert stats["n_figures"] == 3 and stats["n_supported"] == 0


def test_a_claim_may_round_its_source_but_not_exceed_its_precision():
    obs = {"A_10K_chunk_1": "Revenue was $26,448 million.", "A_10K_chunk_2": "Revenue was about $26.4 billion."}
    ok = _contract(("Revenue was $26.4 billion.", ["A_10K_chunk_1"]))
    assert contract.check_contract(ok, "Revenue was $26.4 billion.", obs)[0] == []
    too_precise = _contract(("Revenue was $26,448 million.", ["A_10K_chunk_2"]))
    failures, _ = contract.check_contract(too_precise, "Revenue was $26,448 million.", obs)
    assert [f["check"] for f in failures] == ["unsupported_figure"]
    pct = _contract(("Margin was 14%.", ["A_10K_chunk_3"]))
    assert contract.check_contract(pct, "Margin was 14%.", {"A_10K_chunk_3": "margin of 13.51%"})[0] == []


def test_years_are_exempt_and_a_not_disclosed_answer_verifies_with_no_figures():
    obs = {"A_10K_chunk_1": "The Company does not report Vision Pro revenue separately."}
    c = _contract(("The 10-K does not disclose Vision Pro revenue for fiscal 2025.", ["A_10K_chunk_1"]), not_disclosed=True)
    failures, stats = contract.check_contract(c, "The 10-K does not disclose Vision Pro revenue for fiscal 2025.", obs)
    assert failures == [] and stats["n_figures"] == 0


def test_figure_grounding_needs_no_citations():
    g = contract.figure_grounding("Sales were $416,161 million and EPS was $7.46 in 2025.", OBS)
    assert g == {"applicable": True, "n_figures": 2, "n_grounded": 1, "grounded": False, "ungrounded": ["$7.46"]}
    assert contract.figure_grounding("Nothing is disclosed for 2025.", OBS)["applicable"] is False


# ── the loop: structure, repair once, refuse or flag ─────────────────────────

GOOD = _contract(("Total net sales were $416,161 million.", ["AAPL_10K_chunk_42"]))
BAD = _contract(("Total net sales were $416,161 million.", ["AAPL_10K_chunk_7"]))
DRAFT = "Total net sales were $416,161 million."


def test_verified_on_the_first_attempt_returns_the_draft_and_meters_the_call():
    llm = _FakeLLM((GOOD, _raw()))
    served, v, extra = contract.verify_answer("q", DRAFT, OBS, llm=llm, mode="strict", callbacks=["cb"])
    assert served == DRAFT
    assert (v.status, v.attempts, v.repaired, v.cited) == ("verified", 1, False, ["AAPL_10K_chunk_42"])
    assert (v.n_claims, v.n_figures, v.n_supported) == (1, 1, 1)
    assert extra and extra[0].usage_metadata["input_tokens"] == 900
    messages, config = llm.structured.calls[0]
    assert config == {"callbacks": ["cb"]} and "[AAPL_10K_chunk_42]" in messages[1][1] and DRAFT in messages[1][1]


def test_one_repair_attempt_is_shown_the_failures():
    llm = _FakeLLM((BAD, _raw()), (GOOD, _raw()))
    served, v, extra = contract.verify_answer("q", DRAFT, OBS, llm=llm, mode="strict")
    assert served == DRAFT and v.status == "verified" and v.attempts == 2 and v.repaired
    assert len(extra) == 2
    repair_prompt = llm.structured.calls[1][0][1][1]
    assert "failed verification" in repair_prompt and "AAPL_10K_chunk_7" in repair_prompt


def test_strict_mode_refuses_after_the_repair_fails_and_names_the_reason():
    llm = _FakeLLM((BAD, _raw()), (BAD, _raw()))
    served, v, _ = contract.verify_answer("q", DRAFT, OBS, llm=llm, mode="strict")
    assert v.status == "refused" and v.attempts == 2 and not v.repaired
    assert served != DRAFT and served.startswith("I could not verify my draft answer")
    assert "AAPL_10K_chunk_7" in served and "$416,161 million" in served


def test_warn_mode_serves_the_draft_flagged_and_off_makes_no_call():
    llm = _FakeLLM((BAD, _raw()), (BAD, _raw()))
    served, v, _ = contract.verify_answer("q", DRAFT, OBS, llm=llm, mode="warn")
    assert served == DRAFT and v.status == "unverified" and v.failures
    llm = _FakeLLM()
    served, v, extra = contract.verify_answer("q", DRAFT, OBS, llm=llm, mode="off")
    assert served == DRAFT and v.status == "skipped" and extra == [] and llm.structured.calls == []


def test_a_failed_structuring_call_is_a_verification_failure_not_a_crash():
    llm = _FakeLLM(RuntimeError("503 from the model (quotaId x, model gemini-3.1-flash-lite)"),
                   (None, _raw(), ValueError("bad json")))
    served, v, extra = contract.verify_answer("q", DRAFT, OBS, llm=llm, mode="strict")
    assert v.status == "refused" and v.attempts == 2
    assert v.failures[0] == {"check": "no_contract", "claim": "", "detail": "the record could not be parsed"}
    assert "bad json" in v.error                                        # server side
    assert len(extra) == 1                                              # only the second attempt returned a raw message
    assert "no usable record" in served
    # provider and parser text never reach a client: not in the served text, not in the public verdict
    for leak in ("bad json", "503", "quotaId", "gemini"):
        assert leak not in served
    assert "error" not in v.public_dict() and v.as_dict()["error"] == v.error


def test_an_empty_record_for_a_non_empty_draft_does_not_verify():
    empty = AnswerContract(claims=[])
    failures, stats = contract.check_contract(empty, "Net sales were $416,161 million.", contract.observations_by_id(OBS))
    assert [f["check"] for f in failures] == ["empty_record", "dropped_figure"]
    assert stats["n_claims"] == 0
    assert contract.check_contract(empty, "   ", {})[0] == []


def test_verify_mode_reads_the_environment(monkeypatch):
    monkeypatch.delenv("VERIFY_MODE", raising=False)
    assert contract.verify_mode() == "strict"
    monkeypatch.setenv("VERIFY_MODE", "Warn")
    assert contract.verify_mode() == "warn"
    monkeypatch.setenv("VERIFY_MODE", "loud")
    with pytest.raises(ValueError):
        contract.verify_mode()


def test_contract_version_is_a_hash_of_prompt_and_schema():
    assert len(contract.CONTRACT_VERSION) == 12
    assert Verification(status="verified", mode="strict").as_dict()["contract_version"] == contract.CONTRACT_VERSION


# ── the API ──────────────────────────────────────────────────────────────────

class _FakeAgent:
    async def ainvoke(self, payload, config=None):
        return {"messages": [
            AIMessage(content="", usage_metadata={"input_tokens": 1500, "output_tokens": 40, "total_tokens": 1540}),
            ToolMessage(content=CHUNK, tool_call_id="c1"),
            ToolMessage(content=FACT, tool_call_id="c2"),
            AIMessage(content=DRAFT, usage_metadata={"input_tokens": 2500, "output_tokens": 60, "total_tokens": 2560}),
        ]}


def _events(text):
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(api_main.financial_agent, "LLM_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.delenv("VERIFY_MODE", raising=False)
    api_main.app.state.mcp_agent = None
    api_main.app.state.direct_agent = _FakeAgent()
    return TestClient(api_main.app)


def test_query_carries_the_verdict_marks_cited_sources_and_meters_the_structuring_call(api, monkeypatch):
    monkeypatch.setattr(contract, "get_llm", lambda: _FakeLLM((GOOD, _raw(900, 80))))
    body = api.post("/query", json={"question": "Apple net sales?"}).json()
    assert body["answer"] == DRAFT
    assert body["verification"]["status"] == "verified" and body["verification"]["cited"] == ["AAPL_10K_chunk_42"]
    cited = {s["source_file"]: s["cited"] for s in body["sources"]}
    assert cited == {"AAPL_10K_chunk_42": True, "AMZN_10K_fact_7": False}
    assert "error" not in body["verification"]
    # the meter counts the structuring call with the agent's own two
    assert body["meta"]["llm_calls"] == 3 and body["meta"]["input_tokens"] == 4900 and body["tokens_used"] == 5080


def test_query_serves_the_refusal_when_strict_verification_fails(api, monkeypatch):
    monkeypatch.setattr(contract, "get_llm", lambda: _FakeLLM((BAD, _raw()), (BAD, _raw())))
    resp = api.post("/query", json={"question": "Apple net sales?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["verification"]["status"] == "refused" and body["answer"].startswith("I could not verify")
    assert all(not s["cited"] for s in body["sources"])


def test_warn_mode_serves_the_draft_but_marks_no_source_as_cited(api, monkeypatch):
    monkeypatch.setenv("VERIFY_MODE", "warn")
    monkeypatch.setattr(contract, "get_llm", lambda: _FakeLLM((BAD, _raw()), (BAD, _raw())))
    body = api.post("/query", json={"question": "Apple net sales?"}).json()
    assert body["answer"] == DRAFT and body["verification"]["status"] == "unverified"
    assert all(not s["cited"] for s in body["sources"])   # an unverified record's citations are claims, not evidence


def test_stream_emits_verification_between_sources_and_meta(api, monkeypatch):
    monkeypatch.setattr(contract, "get_llm", lambda: _FakeLLM((GOOD, _raw())))
    events = _events(api.post("/query/stream", json={"question": "Apple net sales?"}).text)
    types = [e["type"] for e in events]
    assert types.index("sources") < types.index("verification") < types.index("meta") < types.index("done")
    verification = next(e for e in events if e["type"] == "verification")
    assert verification["status"] == "verified" and verification["n_supported"] == 1
    sources = next(e for e in events if e["type"] == "sources")["items"]
    assert [s["cited"] for s in sources] == [True, False]
    assert "".join(e["text"] for e in events if e["type"] == "token") == DRAFT


def test_verification_off_skips_the_structuring_call(api, monkeypatch):
    monkeypatch.setenv("VERIFY_MODE", "off")
    monkeypatch.setattr(contract, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
    body = api.post("/query", json={"question": "Apple net sales?"}).json()
    assert body["verification"]["status"] == "skipped" and body["answer"] == DRAFT and body["meta"]["llm_calls"] == 2


# ── the harness ──────────────────────────────────────────────────────────────

def test_a_quota_wall_inside_the_structuring_call_stops_the_run():
    quota = Verification(status="refused", mode="strict", error="ResourceExhausted: 429 quota exceeded")
    with pytest.raises(RuntimeError, match="429"):
        run_eval._raise_if_quota_wall(quota)
    run_eval._raise_if_quota_wall(Verification(status="refused", mode="strict", error="ValueError: bad json"))
    run_eval._raise_if_quota_wall(Verification(status="verified", mode="strict"))
    run_eval._raise_if_quota_wall(None)


def test_cache_key_tags_the_contract_but_leaves_old_keys_alone():
    base = run_eval._cache_key("qa_1", "m", "p")
    assert run_eval._cache_key("qa_1", "m", "p", "", "") == base
    assert run_eval._cache_key("qa_1", "m", "p", "r1", "strict-abcd1234") == base + "|rc=r1|vc=strict-abcd1234"


def test_deterministic_block_scores_the_draft_apart_from_the_served_answer():
    verified = {"id": "a", "ground_truth": "$416,161 million", "answer": DRAFT, "draft_answer": DRAFT, "observations": OBS,
                "verification": {"status": "verified", "repaired": False, "failures": []}}
    refused = {"id": "b", "ground_truth": "$7.46", "answer": "I could not verify my draft answer: the figure $7.46 cites no source.",
               "draft_answer": "EPS was $7.46.", "observations": OBS,
               "verification": {"status": "refused", "repaired": False,
                                "failures": [{"check": "uncited_figure", "claim": "EPS was $7.46.", "detail": "$7.46"}]}}
    old = {"id": "c", "ground_truth": "19.68%", "answer": "Growth was 19.68%.", "observations": OBS}   # cached before the contract
    rows = [verified, refused, old]
    run_eval.attach_deterministic_metrics(rows, labels=None)
    assert refused["figure"]["figure_primary"] is False and refused["figure_draft"]["figure_primary"] is True
    assert verified["figure_draft"] is None and old["grounding"]["grounded"] is True
    assert refused["grounding"] == {"applicable": True, "n_figures": 1, "n_grounded": 0, "grounded": False, "ungrounded": ["$7.46"]}
    block = run_eval._deterministic_block(rows)
    assert block["figure_primary_rate"] == round(2 / 3, 4) and block["figure_primary_rate_draft"] == 1.0
    assert block["n_grounding_applicable"] == 3 and block["grounded_rate"] == round(2 / 3, 4)
    assert block["n_verified_applicable"] == 2 and block["verified_rate"] == 0.5 and block["refused_rate"] == 0.5
    assert block["verification_failures"] == {"uncited_figure": 1}
