# tests/test_thread_memory.py
#
# agent/memory.py: the bounded, thread-safe, in-process store of served turns.
# The clock is injected, so TTL and idle expiry are tested without sleeping.

import threading

import pytest

from agent import memory
from agent.memory import (HISTORY_PREFACE, MAX_ANSWER_CHARS, MAX_QUESTION_CHARS, ThreadMemory, Turn, compose_message,
                          memory_enabled)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(**kw):
    clock = Clock()
    return ThreadMemory(clock=clock, **kw), clock


def test_a_served_turn_is_remembered_and_comes_back_oldest_first():
    mem, _ = make()
    assert mem.history("thread-aaaa") == []
    assert mem.remember("thread-aaaa", "What was Apple's revenue?", "$416,161 million.", "verified")
    assert mem.remember("thread-aaaa", "And Microsoft?", "$281,724 million.", "skipped")
    assert mem.history("thread-aaaa") == [Turn("What was Apple's revenue?", "$416,161 million."),
                                          Turn("And Microsoft?", "$281,724 million.")]


@pytest.mark.parametrize("status", ["refused", "unverified", None, "error", ""])
def test_an_answer_that_did_not_pass_verification_is_never_stored(status):
    mem, _ = make()
    assert mem.remember("thread-aaaa", "q", "a draft the contract refused", status) is False
    assert mem.history("thread-aaaa") == [] and len(mem) == 0


def test_an_empty_answer_or_a_missing_thread_id_is_never_stored():
    mem, _ = make()
    assert mem.remember("thread-aaaa", "q", "   ", "verified") is False
    assert mem.remember(None, "q", "a", "verified") is False and mem.remember("", "q", "a", "verified") is False
    assert mem.history(None) == [] and mem.history("") == [] and len(mem) == 0


def test_a_turn_is_truncated_and_the_thread_keeps_only_the_most_recent_turns():
    mem, _ = make(max_turns=3)
    mem.remember("thread-aaaa", "q" * 900, "a" * 3000, "verified")
    (turn,) = mem.history("thread-aaaa")
    assert len(turn.question) == MAX_QUESTION_CHARS + 1 and turn.question.endswith("…")
    assert len(turn.answer) == MAX_ANSWER_CHARS + 1 and MAX_QUESTION_CHARS == 500 and MAX_ANSWER_CHARS == 1200
    for i in range(5):
        mem.remember("thread-aaaa", f"q{i}", f"a{i}", "verified")
    assert [t.question for t in mem.history("thread-aaaa")] == ["q2", "q3", "q4"]


def test_a_thread_idle_past_the_ttl_is_forgotten_and_use_keeps_it_alive():
    mem, clock = make(ttl_seconds=100.0)
    mem.remember("thread-aaaa", "q", "a", "verified")
    clock.now += 60
    assert mem.history("thread-aaaa")                 # reading counts as use: the idle clock restarts
    clock.now += 60
    assert mem.history("thread-aaaa")                 # 120 s after the first turn, only 60 s idle
    clock.now += 101
    assert mem.history("thread-aaaa") == [] and len(mem) == 0
    mem.remember("thread-aaaa", "fresh", "a", "verified")
    assert [t.question for t in mem.history("thread-aaaa")] == ["fresh"]       # an expired thread starts over


def test_the_least_recently_used_thread_is_evicted_at_the_cap():
    mem, clock = make(max_threads=3)
    for name in ("thread-a000", "thread-b000", "thread-c000"):
        clock.now += 1
        mem.remember(name, "q", "a", "verified")
    clock.now += 1
    assert mem.history("thread-a000")                  # a is now the most recently used
    mem.remember("thread-d000", "q", "a", "verified")  # evicts b, the least recently used
    assert mem.history("thread-b000") == [] and mem.history("thread-a000") and mem.history("thread-d000")
    assert len(mem) == 3


def test_two_threads_never_see_each_others_turns():
    mem, _ = make()
    mem.remember("thread-aaaa", "about Apple", "apple answer", "verified")
    mem.remember("thread-bbbb", "about Meta", "meta answer", "verified")
    assert [t.question for t in mem.history("thread-aaaa")] == ["about Apple"]
    assert [t.question for t in mem.history("thread-bbbb")] == ["about Meta"]
    assert mem.history("thread-cccc") == []


def test_a_disabled_memory_stores_and_returns_nothing():
    mem, _ = make(enabled=False)
    assert mem.remember("thread-aaaa", "q", "a", "verified") is False and mem.history("thread-aaaa") == []


def test_forget_drops_a_thread():
    mem, _ = make()
    mem.remember("thread-aaaa", "q", "a", "verified")
    mem.forget("thread-aaaa")
    mem.forget("thread-nope")
    assert mem.history("thread-aaaa") == []


def test_concurrent_use_is_safe_and_the_bounds_hold():
    mem = ThreadMemory(max_turns=4, max_threads=10)
    errors = []

    def worker(w):
        try:
            for i in range(200):
                tid = f"thread-{(w + i) % 25:04d}"
                mem.remember(tid, f"q{w}-{i}", f"a{w}-{i}", "verified")
                assert len(mem.history(tid)) <= 4
        except Exception as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors and len(mem) <= 10


def test_the_composed_message_is_the_history_block_then_the_current_question():
    turns = [Turn("What was Apple's net income?", "$112,010 million."), Turn("And its EPS?", "$7.46.")]
    text = compose_message(turns, "And Microsoft?")
    assert text.startswith(HISTORY_PREFACE)
    assert text.index("[Earlier turn 1] Question: What was Apple's net income?") < text.index("[Earlier turn 2] Answer: $7.46.")
    assert text.rstrip().endswith("Current question:\nAnd Microsoft?")
    # the preface says what the contract depends on: nothing from history is reusable as a figure
    assert "Do not reuse any figure" in HISTORY_PREFACE and "call the tools again for every figure" in HISTORY_PREFACE
    assert "reference only" in HISTORY_PREFACE


def test_configuration_comes_from_the_environment_with_safe_fallbacks(monkeypatch):
    monkeypatch.setenv("THREAD_MEMORY_MAX_TURNS", "2")
    monkeypatch.setenv("THREAD_MEMORY_TTL_SECONDS", "45.5")
    monkeypatch.setenv("THREAD_MEMORY_MAX_THREADS", "7")
    mem = ThreadMemory.from_env()
    assert (mem.max_turns, mem.ttl_seconds, mem.max_threads, mem.enabled) == (2, 45.5, 7, True)
    for var in ("THREAD_MEMORY_MAX_TURNS", "THREAD_MEMORY_TTL_SECONDS", "THREAD_MEMORY_MAX_THREADS"):
        monkeypatch.setenv(var, "not-a-number")
    mem = ThreadMemory.from_env()
    assert (mem.max_turns, mem.ttl_seconds, mem.max_threads) == (memory.DEFAULT_MAX_TURNS, memory.DEFAULT_TTL_SECONDS,
                                                                 memory.DEFAULT_MAX_THREADS) == (6, 1800.0, 500)
    for var in ("THREAD_MEMORY_MAX_TURNS", "THREAD_MEMORY_MAX_THREADS"):
        monkeypatch.setenv(var, "-3")                  # a non-positive bound is not a bound
    assert ThreadMemory.from_env().max_turns == 6 and ThreadMemory.from_env().max_threads == 500


def test_the_off_switch(monkeypatch):
    monkeypatch.delenv("THREAD_MEMORY", raising=False)
    assert memory_enabled() is True and ThreadMemory.from_env().enabled is True
    for off in ("off", "OFF", "0", "false", "no"):
        monkeypatch.setenv("THREAD_MEMORY", off)
        assert memory_enabled() is False and ThreadMemory.from_env().enabled is False
    monkeypatch.setenv("THREAD_MEMORY", "on")
    assert memory_enabled() is True


def test_an_unknown_value_is_an_error_not_a_silent_on(monkeypatch):
    for on in ("on", "ON", "true", "1", "yes", ""):
        monkeypatch.setenv("THREAD_MEMORY", on)
        assert memory_enabled() is True
    monkeypatch.setenv("THREAD_MEMORY", "disabled")           # a typo must not leave user text being stored
    with pytest.raises(ValueError, match="THREAD_MEMORY"):
        memory_enabled()
    with pytest.raises(ValueError, match="THREAD_MEMORY"):
        ThreadMemory.from_env()
