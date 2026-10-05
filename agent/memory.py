# agent/memory.py
#
# PURPOSE
# -------
# Per-thread conversation memory for the API, so a follow-up such as "and
# Microsoft?" can be answered.  The API is otherwise stateless: every request
# is one question and one fresh agent run.
#
# THE DESIGN, AND WHY (docs/DECISIONS.md, "Conversation memory")
# --------------------------------------------------------------
# * It stores only the TEXT of questions and answers that were actually served,
#   never a tool trace.  A trace is thousands of tokens per turn; the text of a
#   served answer is a few hundred characters.
# * Earlier turns reach the model as ONE human message: a labelled history
#   block followed by the current question.  Nothing is added to the graph, the
#   output contract or the evaluation harness, and a request with no thread id
#   is byte-for-byte the request it was before this module existed.  It
#   behaves identically on the direct and the MCP path because it only builds
#   the text the agent is given.
# * The verification contract stays turn-scoped.  A figure in an answer is
#   checked against the observations of THIS turn, so a figure from an earlier
#   turn could never pass: the history block therefore tells the model to call
#   the tools again for every figure it states, and the contract refuses an
#   answer that does not.
# * Memory is in process and ephemeral by design.  The Space's disk is
#   ephemeral and the free-tier rule forbids a paid store; it is bounded in
#   turns, threads and time, and it forgets on restart.  A persistent store
#   would be a separate decision.
#
# Only an answer that passed verification (or ran with verification skipped)
# is remembered; a refusal, an unverified draft or a terminal failure never is.

import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable

# A stored turn is bounded so a long thread stays a few thousand tokens at most.
MAX_QUESTION_CHARS = 500
MAX_ANSWER_CHARS = 1200

DEFAULT_MAX_TURNS = 6
DEFAULT_TTL_SECONDS = 1800.0
DEFAULT_MAX_THREADS = 500

# Verification verdicts whose answer is remembered.  "skipped" is VERIFY_MODE=off (nothing was checked, and that
# is what the owner chose); "unverified" (warn mode) and "refused" are answers that did not pass.
REMEMBERED_STATUSES = frozenset({"verified", "skipped"})

HISTORY_PREFACE = (
    "Earlier turns of this conversation are shown below for reference only, so you can tell what the current question "
    "refers to (a company, a year or a metric it leaves out). Do not reuse any figure from them: call the tools again "
    "for every figure you state in your answer, even one an earlier answer already gave. Answer only the current question."
)


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


@dataclass(frozen=True)
class Turn:
    """One served exchange, as text."""
    question: str
    answer: str


def compose_message(turns: list[Turn], question: str) -> str:
    """The single human message the agent is given when a thread has earlier turns: history block, then the question."""
    lines = [HISTORY_PREFACE, ""]
    for i, t in enumerate(turns, start=1):
        lines += [f"[Earlier turn {i}] Question: {t.question}", f"[Earlier turn {i}] Answer: {t.answer}", ""]
    lines += ["Current question:", question]
    return "\n".join(lines)


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name) or default)
    except ValueError:
        return default
    return value if value > 0 else default


def memory_enabled() -> bool:
    """THREAD_MEMORY=on|off (default on).  Memory only ever acts on a request that carries a thread id.

    Anything but on or off is an error rather than a silent on: a typo ("disabled") that left memory storing user
    text would be the wrong way round to fail.
    """
    raw = (os.getenv("THREAD_MEMORY") or "on").strip().lower()
    if raw in ("on", "true", "1", "yes"):
        return True
    if raw in ("off", "false", "0", "no"):
        return False
    raise ValueError(f"THREAD_MEMORY must be on or off, got {raw!r}")


class ThreadMemory:
    """Bounded, thread-safe, in-process store of recent served turns per conversation thread.

    Bounds: at most *max_turns* turns per thread (the oldest drop first), a thread idle for *ttl_seconds* is
    forgotten, and at most *max_threads* threads are kept (least recently used evicted).  *clock* is injectable
    so tests can move time without sleeping.
    """

    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS, ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 max_threads: int = DEFAULT_MAX_THREADS, enabled: bool = True,
                 clock: Callable[[], float] = time.monotonic):
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self.max_threads = max_threads
        self.enabled = enabled
        self._clock = clock
        self._lock = threading.Lock()
        # thread id -> (turns, last used); ordered least recently used first
        self._threads: OrderedDict[str, tuple[deque, float]] = OrderedDict()

    @classmethod
    def from_env(cls, clock: Callable[[], float] = time.monotonic) -> "ThreadMemory":
        return cls(max_turns=_env_int("THREAD_MEMORY_MAX_TURNS", DEFAULT_MAX_TURNS),
                   ttl_seconds=_env_float("THREAD_MEMORY_TTL_SECONDS", DEFAULT_TTL_SECONDS),
                   max_threads=_env_int("THREAD_MEMORY_MAX_THREADS", DEFAULT_MAX_THREADS),
                   enabled=memory_enabled(), clock=clock)

    def _expire(self, now: float) -> None:
        # ordered by last use, so the expired threads are all at the front
        while self._threads:
            oldest = next(iter(self._threads))
            if now - self._threads[oldest][1] <= self.ttl_seconds:
                break
            del self._threads[oldest]

    def history(self, thread_id: str | None) -> list[Turn]:
        """The thread's remembered turns, oldest first; empty when memory is off, the id is empty or the thread is unknown."""
        if not self.enabled or not thread_id:
            return []
        with self._lock:
            now = self._clock()
            self._expire(now)
            entry = self._threads.get(thread_id)
            if entry is None:
                return []
            self._threads[thread_id] = (entry[0], now)
            self._threads.move_to_end(thread_id)
            return list(entry[0])

    def remember(self, thread_id: str | None, question: str, answer: str, verification_status: str | None) -> bool:
        """Store one served turn; False (and nothing stored) unless it passed verification or verification was skipped."""
        if not self.enabled or not thread_id or verification_status not in REMEMBERED_STATUSES:
            return False
        if not (answer or "").strip():
            return False
        turn = Turn(_clip(question, MAX_QUESTION_CHARS), _clip(answer, MAX_ANSWER_CHARS))
        with self._lock:
            now = self._clock()
            self._expire(now)
            turns = self._threads[thread_id][0] if thread_id in self._threads else deque(maxlen=self.max_turns)
            turns.append(turn)
            self._threads[thread_id] = (turns, now)
            self._threads.move_to_end(thread_id)
            while len(self._threads) > self.max_threads:
                self._threads.popitem(last=False)
        return True

    def forget(self, thread_id: str) -> None:
        with self._lock:
            self._threads.pop(thread_id, None)

    def __len__(self) -> int:
        with self._lock:
            self._expire(self._clock())
            return len(self._threads)
