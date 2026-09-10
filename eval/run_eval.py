# eval/run_eval.py
#
# PURPOSE
# -------
# Reproducible, resumable RAGAS evaluation harness for the direct ReAct agent in
# agent/financial_agent.py.  Runs a labelled benchmark of questions through the
# agent, captures the generated answer and the retrieved context passages (from
# ToolMessage observations), then scores the run with three RAGAS metrics:
# faithfulness, answer_relevancy, and context_recall.
#
# WHY THE DIRECT AGENT (NOT THE MCP CLIENT)?
# -------------------------------------------
# The MCP path adds a network hop (HTTP to the MCP server) which introduces
# flakiness and rate-limit interactions that are unrelated to answer quality.
# For eval we want to measure the agent + retriever + LLM, not transport.
#
# DESIGN: WHY THIS HARNESS IS RESUMABLE
# -------------------------------------
# A full 66-item run makes roughly 150 agent calls and 396 judge calls, against
# free-tier quotas measured in TENS of requests per day (see the quota section
# below).  Any real run therefore spans multiple days and multiple quota resets,
# so a 429 partway through must not discard the work that already succeeded:
#
#   1. Agent outputs are written to eval/cache/agent_outputs.json after EVERY
#      item, keyed by (item id, agent model id, prompt version).  A rerun skips
#      any item already cached under the same key — so resuming after a quota
#      wall costs only the items that had not completed.
#   2. The cache key includes the model and the prompt hash, so changing either
#      correctly invalidates the cached generation instead of silently scoring
#      stale outputs against a new configuration.
#   3. --score-only re-judges purely from cache (or from a prior results file),
#      making a judge-model comparison free of generation cost.
#   4. On quota exhaustion the run stops, persists what completed, and prints
#      the exact command to resume.
#
# DESIGN: WHY PARTIAL RUNS NEVER PRINT AGGREGATES
# -----------------------------------------------
# A mean over "the items that happened to finish" looks identical to a mean over
# a finished run, and that is precisely the failure mode this harness exists to
# prevent.  So aggregates are withheld — in stdout AND in the JSON — whenever
# the run did not generate every requested item.  Per-metric NaN coverage is
# always reported alongside every mean, so a metric that only scored on part of
# the column can never be mistaken for a complete one.
#
# THE FREE-TIER QUOTA CONSTRAINT (why runs are small and span days)
# -----------------------------------------------------------------
# Measured 2026-09-09 from live 429 response bodies, because Google no longer
# publishes per-model free-tier numbers in its rate-limit docs:
#
#   Gemini free tier : 20 requests/DAY, per model, per project.
#                      (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier,
#                       quotaValue 20).  Separate models have separate buckets.
#   Groq free tier   : gpt-oss-120b — 30 RPM, 1,000 RPD, 8,000 TPM, 200,000 TPD.
#
# Consequences that shape this harness:
#   * ~150 agent calls for 66 items = ~9 days of Gemini generation.  The full
#     66-item benchmark is committed and runnable, but a single-sitting 66-item
#     run is not possible on the free tier.  Reported runs use a stratified
#     subset; the harness reports n on every row so subset size is never hidden.
#   * The judge is pointed at Groq by default in reported runs precisely because
#     396 judge calls will not fit in a 20/day Gemini bucket.
#   * Scores are only ever comparable within a pinned judge model id — free-tier
#     judge models get deprecated without notice, and this project has already
#     lost one mid-flight.  See eval/EVALUATION.md.
#
# WHY HuggingFace MiniLM EMBEDDINGS AS THE RAGAS EMBEDDINGS?
# ----------------------------------------------------------
# answer_relevancy needs an embedding model to compare the generated answer
# against synthetic counter-questions.  Reusing the same all-MiniLM-L6-v2 model
# that indexed our corpus keeps the eval pipeline self-contained (no extra
# embedding API key needed) and means relevance is measured in the same vector
# space the retriever operates in.
#
# KNOWN MEASUREMENT ARTEFACTS (deliberately NOT "fixed" here)
# -----------------------------------------------------------
# * answer_relevancy is NOT deterministic even though the judge is constructed
#   with temperature=0.  RAGAS asks for `strictness` (3) sampled generations and
#   its LangchainLLMWrapper.agenerate_text() overrides the model's temperature
#   to 0.3 whenever n > 1 (ragas/llms/base.py::get_temperature).  We cannot set
#   it back without disabling the multi-sample behaviour the metric is defined
#   in terms of, so the honest move is to record it rather than hide it.
# * A judge model that returns only ONE candidate for an n=3 request silently
#   degrades answer_relevancy to a single sample and, when its JSON comes back
#   malformed, produces NaN.  This is what happened to the earlier
#   gemini-3.1-flash-lite-preview baseline.  JUDGE_BYPASS_N below forces N
#   separate single-candidate calls instead, which works on every provider.
# * _extract_contexts() captures each tool observation as ONE context string
#   (the observation already concatenates k passages).  RAGAS therefore scores
#   against observation-sized blobs, not individual chunks, which makes
#   context_recall coarser than a per-chunk measurement would be.  Changing this
#   would change what every score means, so it is documented, not altered.

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Make the repo root importable whether the script is run as
# `python -m eval.run_eval` or `python eval/run_eval.py`.  dotenv is a
# site-packages import and doesn't depend on this path manipulation, so it
# stays with the other top-of-file imports; only the agent/ingestion imports
# that DO depend on it are kept lazy inside the functions that need them.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)

# ── Paths & constants ────────────────────────────────────────────────────────

EVAL_DIR = Path(__file__).resolve().parent
BENCHMARK_FILE = EVAL_DIR / "benchmark.csv"            # 66 labelled items
SMOKE_BENCHMARK_FILE = EVAL_DIR / "benchmark_smoke.csv"  # 5 items, CI + --dry-run
RESULTS_DIR = EVAL_DIR / "results"
CACHE_FILE = EVAL_DIR / "cache" / "agent_outputs.json"

# Exit code used when a run stops because a provider quota was exhausted.  A
# distinct code (rather than a plain failure) lets a wrapper script tell
# "out of quota, resume later" apart from "the harness is broken".
EXIT_QUOTA_EXHAUSTED = 2

# Pause between successive agent invocations.  Each invocation makes 2–3 Gemini
# calls (tool-calling round trips), so spacing the top-level calls by 5 s keeps
# sustained RPM well below the free-tier ceiling.
AGENT_SLEEP_SECONDS = 5

# Judge throughput cap, in requests per second, applied via a LangChain
# InMemoryRateLimiter attached to the judge model.  RAGAS runs its metrics
# through a 16-worker pool by default and will burst straight past a free-tier
# limit; the limiter is what actually keeps a scoring pass inside quota.
#
# The right cap depends on WHICH limit binds first, and that differs by provider:
#
#   google : request-bound.  The free tier's binding constraint is a per-day
#            request count, with a per-minute ceiling well above what one
#            sequential scoring pass produces.  0.2 rps = 12 req/min.
#   groq   : TOKEN-bound.  gpt-oss-120b free tier is 8,000 tokens/minute, and a
#            judge call on this benchmark averages ~1.1k tokens, so 12 req/min
#            would demand ~13k tokens/min and 429 continuously.  0.1 rps
#            = 6 req/min ≈ 6.6k tokens/min, just inside the ceiling.
#
# EVAL_JUDGE_RPS overrides both when a key has different limits.
_JUDGE_RPS_BY_PROVIDER = {"google": 0.2, "groq": 0.1}
JUDGE_REQUESTS_PER_SECOND_OVERRIDE = (
    float(os.environ["EVAL_JUDGE_RPS"]) if os.getenv("EVAL_JUDGE_RPS") else None
)


def judge_rps(provider: str) -> float:
    """Return the requests-per-second cap to apply to *provider*'s judge."""
    if JUDGE_REQUESTS_PER_SECOND_OVERRIDE is not None:
        return JUDGE_REQUESTS_PER_SECOND_OVERRIDE
    return _JUDGE_RPS_BY_PROVIDER.get(provider, 0.1)


# Force RAGAS to issue N separate single-candidate calls instead of asking one
# call for N candidates.  See "KNOWN MEASUREMENT ARTEFACTS" above: the
# one-call-N-candidates path silently degrades to a single sample on providers
# that ignore n, which is how the earlier baseline produced NaN relevancy.
JUDGE_BYPASS_N = True

# RAGAS worker pool size.  Kept at 2, not the default 16, because throughput
# here is set by the rate limiter, not by concurrency.  Every worker's request
# queues behind the SAME limiter, so extra workers do not add throughput — they
# just lengthen how long each individual job waits, which is what turns into
# executor timeouts (see RAGAS_TIMEOUT_SECONDS).
RAGAS_MAX_WORKERS = 2

# Per-job timeout handed to RAGAS's executor.  The default (180 s) is far too
# short once a rate limiter is in play: faithfulness makes TWO sequential judge
# calls, each of which must wait its turn in the limiter queue behind every
# other in-flight job, and a 429 retry adds more. When a job exceeds this,
# RAGAS records the metric as NaN — which is indistinguishable, in the output,
# from a judge that genuinely could not score the item.  A timeout is a HARNESS
# artefact and must never be read as a model result, so this is set generously
# and every timeout is counted and reported (see _ExecutorErrorCounter).
RAGAS_TIMEOUT_SECONDS = int(os.getenv("EVAL_RAGAS_TIMEOUT", "900"))

# Seed recorded in every results file for provenance.  RAGAS uses it for its own
# sampling; it does NOT make an LLM judge deterministic (see artefacts above).
RAGAS_SEED = 42

METRIC_NAMES = ("faithfulness", "answer_relevancy", "context_recall")

# Question-type strata present in the 66-item benchmark, in reporting order.
QUESTION_TYPES = (
    "single_hop", "numerical", "multi_hop", "comparative", "negative", "list",
)

# A stratum this thin cannot support a claim.  Rows at or below this n are
# printed with an explicit "anecdotal" marker so no reader mistakes a
# single-item cell for a finding.
THIN_STRATUM_N = 4

# Below this fraction of non-NaN scores, a metric's mean is not reported as a
# headline number — the column is too incomplete to summarise.
MIN_METRIC_COVERAGE = 0.90

# LangGraph emits this when the tool-calling loop exhausts recursion_limit.
# Recording it matters: such an item is an agent-capability failure, not a
# retrieval failure, and the two should not be pooled silently.
RECURSION_LIMIT_MARKER = "need more steps to process this request"

# Substrings that identify a provider quota / rate-limit refusal across the
# providers this harness talks to (Google api_core, Groq, generic HTTP 429).
_QUOTA_MARKERS = (
    "resource_exhausted", "resourceexhausted", "429", "quota",
    "rate limit", "ratelimit", "too many requests",
)


def _is_quota_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like a provider quota / rate-limit refusal.

    Matched on the stringified exception rather than on exception classes
    because the same underlying 429 surfaces as google.api_core
    ResourceExhausted, a langchain ChatGoogleGenerativeAIError, an httpx
    HTTPStatusError or a groq.RateLimitError depending on which layer raises.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _QUOTA_MARKERS)


# ── Provenance ───────────────────────────────────────────────────────────────

def _git_commit() -> tuple[str, bool]:
    """Return (short commit sha, working-tree-is-dirty).

    Recorded in every results file so a score can always be traced back to the
    exact code that produced it.  Falls back to "nogit" outside a repository
    rather than failing the run.
    """
    def _run(*args: str) -> str:
        return subprocess.run(
            args, cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()

    try:
        sha = _run("git", "rev-parse", "--short", "HEAD")
        dirty = bool(_run("git", "status", "--porcelain"))
        return sha, dirty
    except Exception:  # noqa: BLE001 — provenance is best-effort, never fatal
        return "nogit", False


def _prompt_version() -> str:
    """Return a stable fingerprint of the agent's system prompt.

    Derived by hashing the live prompt text rather than stored as a hand-bumped
    constant, so it cannot drift out of sync with the prompt it describes: edit
    the prompt and the recorded version changes on the next run automatically.
    """
    from agent.financial_agent import _SYSTEM_PROMPT

    digest = hashlib.sha256(_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


# ── Benchmark loading ────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {"id", "question", "ground_truth", "question_type"}


def load_benchmark(path: Path) -> list[dict]:
    """Load a benchmark CSV into a list of row dicts.

    Requires id / question / ground_truth / question_type: `id` is the cache and
    results key, and `question_type` is the stratum every per-type table is cut
    on, so a file missing either cannot be evaluated meaningfully.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Benchmark file not found: {path}. Expected columns include: "
            f"{sorted(REQUIRED_COLUMNS)}"
        )
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and not REQUIRED_COLUMNS.issubset(rows[0].keys()):
        missing = sorted(REQUIRED_COLUMNS - set(rows[0].keys()))
        raise ValueError(
            f"{path.name} is missing required column(s) {missing}; "
            f"found {sorted(rows[0].keys())}"
        )
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"{path.name} has duplicate id(s): {dupes}")
    return rows


# ── Agent invocation + context extraction ────────────────────────────────────

def _extract_contexts(messages) -> list[str]:
    """Pull the retrieved passages out of the agent's ToolMessage observations.

    ToolMessage.content can arrive in two shapes depending on how the tool
    was produced:

      1. Plain ``str`` — what our in-process @tool functions (search_filings,
         compare_companies, list_available_companies) return.
      2. ``list[dict]`` with ``{"type": "text", "text": "..."}`` items —
         the shape MCP tool results take when wrapped by langchain-mcp-adapters.
         We handle it here defensively so the eval harness works identically
         whether someone swaps the agent to an MCP-backed version later.

    NOTE: each observation is kept as ONE context string.  See "KNOWN
    MEASUREMENT ARTEFACTS" at the top of this file.
    """
    # Imported lazily so `--dry-run` works without requiring langchain to be
    # importable (useful for quick pipeline sanity checks).
    from langchain_core.messages import ToolMessage

    contexts: list[str] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = msg.content
        if isinstance(content, str):
            if content.strip():
                contexts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text and isinstance(text, str) and text.strip():
                        contexts.append(text)
                elif isinstance(item, str) and item.strip():
                    contexts.append(item)
    return contexts


def run_agent_capture(question: str) -> tuple[str, list[str], int]:
    """Invoke the direct agent; return (final_answer, contexts, n_llm_messages).

    We call ``build_agent_executor`` + ``.invoke`` directly (rather than the
    higher-level ``run_agent`` helper) because we need access to the full
    message history — ``run_agent`` returns only the last message's content.
    """
    from agent.financial_agent import build_agent_executor

    agent = build_agent_executor()
    result = agent.invoke(
        {"messages": [("human", question)]},
        config={"recursion_limit": 20},
    )
    messages = result["messages"]
    final_answer = messages[-1].content or ""
    if not isinstance(final_answer, str):
        # Some LangChain versions return a list of content parts for the
        # AIMessage; flatten the text parts rather than str()-ing the list,
        # which would leak a Python repr into the scored answer.
        if isinstance(final_answer, list):
            parts = [
                p.get("text", "") if isinstance(p, dict) else str(p)
                for p in final_answer
            ]
            final_answer = "".join(parts)
        else:
            final_answer = str(final_answer)
    contexts = _extract_contexts(messages)
    return final_answer, contexts, len(messages)


# ── Agent-output cache ───────────────────────────────────────────────────────

def _cache_key(item_id: str, agent_model: str, prompt_version: str) -> str:
    """Build the cache key for one generated agent output.

    Keyed on all three axes that change what the agent produces, so a model
    swap or a prompt edit invalidates the entry instead of letting a stale
    generation be re-scored under a new configuration's label.
    """
    return f"{item_id}|{agent_model}|{prompt_version}"


def load_cache(path: Path = CACHE_FILE) -> dict:
    """Load the agent-output cache, tolerating a missing or corrupt file.

    A truncated cache (killed mid-write on a previous run) must not block a
    resume, so a JSON error degrades to an empty cache with a warning rather
    than raising.
    """
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cache at %s unreadable (%s) — starting empty.", path, exc)
        return {}


def save_cache(cache: dict, path: Path = CACHE_FILE) -> None:
    """Persist the cache atomically.

    Written to a sibling temp file and then os.replace()d into position, so a
    process killed mid-write (the exact scenario this cache exists for) can
    never leave a half-written file where a complete one used to be.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


# ── Persistence ──────────────────────────────────────────────────────────────

def save_results(payload: dict, path: Path) -> None:
    """Write a results JSON, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)
    logger.info("Saved results → %s", path)


# ── Aggregation ──────────────────────────────────────────────────────────────

def _is_number(val) -> bool:
    """True if *val* is a real (non-NaN) number RAGAS produced as a score."""
    return isinstance(val, (int, float)) and not isinstance(val, bool) and not math.isnan(val)


def metric_coverage(rows: list[dict]) -> dict[str, dict]:
    """Count scored vs NaN per metric across *rows*.

    Reported next to every mean.  RAGAS returns NaN when a metric raises or
    when the judge's structured output is unusable, and a NaN is NOT a zero —
    pooling the two would understate a metric while looking like a full column.
    """
    out = {}
    for m in METRIC_NAMES:
        vals = [r.get("scores", {}).get(m) for r in rows]
        scored = [v for v in vals if _is_number(v)]
        out[m] = {
            "n_scored": len(scored),
            "n_nan": len(vals) - len(scored),
            "coverage": round(len(scored) / len(vals), 4) if vals else 0.0,
        }
    return out


def _mean_block(rows: list[dict]) -> dict:
    """Mean of each metric over *rows*, each with its own n and NaN count.

    Every metric carries its own n because NaNs are per-metric: two metrics in
    the same table can legitimately be averaged over different subsets, and the
    only safe presentation is one that says so on every cell.
    """
    block: dict = {"n_items": len(rows)}
    for m in METRIC_NAMES:
        vals = [r.get("scores", {}).get(m) for r in rows]
        scored = [float(v) for v in vals if _is_number(v)]
        block[m] = {
            "mean": round(sum(scored) / len(scored), 4) if scored else None,
            "n_scored": len(scored),
            "n_nan": len(vals) - len(scored),
        }
    return block


def build_aggregates(rows: list[dict]) -> dict:
    """Build the overall and per-question-type aggregate blocks."""
    by_type = {}
    for qtype in QUESTION_TYPES:
        subset = [r for r in rows if r.get("question_type") == qtype]
        if subset:
            by_type[qtype] = _mean_block(subset)
    # Surface any question_type present in the data but absent from the
    # declared strata, rather than dropping those rows from the table silently.
    for qtype in sorted({r.get("question_type") for r in rows} - set(QUESTION_TYPES)):
        if qtype:
            by_type[qtype] = _mean_block([r for r in rows if r.get("question_type") == qtype])
    return {"overall": _mean_block(rows), "by_question_type": by_type}


# ── Printing ─────────────────────────────────────────────────────────────────

def _fmt(val) -> str:
    return f"{val:.4f}" if isinstance(val, float) else "  n/a "


def _print_metric_row(label: str, block: dict, n_col: int = 12) -> None:
    cells = []
    for m in METRIC_NAMES:
        d = block[m]
        nan_flag = f" ({d['n_nan']} NaN)" if d["n_nan"] else ""
        cells.append(f"{_fmt(d['mean'])} [n={d['n_scored']}]{nan_flag}")
    print(f"  {label:<{n_col}} " + "   ".join(f"{c:<24}" for c in cells))


def print_report(payload: dict) -> None:
    """Print the human-readable report for a results payload.

    Aggregates are printed ONLY for a complete run.  For a partial run the
    per-item detail is still shown but every mean is withheld, so a stopped run
    can never be screenshotted as a finished one.
    """
    status = payload["run_status"]
    cfg = payload["config"]
    rows = payload["results"]

    print("\n" + "=" * 78)
    print(f"RUN {payload['run_id']}   ({payload['timestamp']})")
    print("=" * 78)
    print(f"  agent model  : {cfg['agent_model']}  (provider: {cfg['agent_provider']})")
    print(f"  judge model  : {cfg['judge_model']}  (provider: {cfg['judge_provider']})")
    print(f"  embeddings   : {cfg['embedding_model']}")
    print(f"  k (top_k)    : {cfg['k']}")
    print(f"  prompt ver   : {cfg['prompt_version']}")
    print(f"  ragas        : {cfg['ragas_version']}  (seed {cfg['ragas_seed']})")
    print(f"  benchmark    : {cfg['benchmark_file']}  ({cfg['benchmark_n']} items)")
    print(f"  git commit   : {payload['git_commit']}"
          f"{'  [DIRTY WORKING TREE]' if payload['git_dirty'] else ''}")
    print(f"  status       : generated {status['n_generated']}/{status['n_requested']}, "
          f"scored {status['n_scored']}")
    if status["stop_reason"]:
        print(f"  stop reason  : {status['stop_reason']}")

    cov = payload["metric_coverage"]
    if cov:
        print("\n--- Metric coverage (NaN is NOT zero; it is an unscored item) ---")
        for m in METRIC_NAMES:
            d = cov[m]
            warn = "   << BELOW REPORTING THRESHOLD" if d["coverage"] < MIN_METRIC_COVERAGE else ""
            print(f"  {m:<18} scored {d['n_scored']:>3} / NaN {d['n_nan']:>3} "
                  f"= {d['coverage'] * 100:5.1f}% coverage{warn}")
    else:
        print("\n--- Metric coverage: nothing was scored (no generated items) ---")

    if payload["aggregates"] is None:
        print("\n" + "!" * 78)
        print("PARTIAL RUN — AGGREGATES WITHHELD")
        print(f"  reason: {payload['aggregates_withheld_reason']}")
        print("  These numbers are NOT a baseline. Resume, then re-report.")
        print("!" * 78)
        _print_resume_hint(payload)
        return

    agg = payload["aggregates"]
    header = "  " + f"{'':<12} " + "   ".join(f"{m:<24}" for m in METRIC_NAMES)
    print("\n--- Overall ---")
    print(header)
    _print_metric_row("all", agg["overall"])

    print("\n--- By question type ---")
    print(header)
    for qtype, block in agg["by_question_type"].items():
        label = qtype
        _print_metric_row(label, block)
    thin = [
        (q, b["n_items"]) for q, b in agg["by_question_type"].items()
        if b["n_items"] <= THIN_STRATUM_N
    ]
    if thin:
        print("\n  Thin strata — read these rows as anecdote, not measurement:")
        for q, n in thin:
            note = "single item; not a finding" if n == 1 else "too few items to generalise"
            print(f"    {q:<12} n={n}  ({note})")

    diag = payload.get("judge_diagnostics") or {}
    failures = diag.get("executor_failures") or {}
    if failures:
        print(f"\n  {sum(failures.values())} judge job(s) failed inside the RAGAS "
              f"executor and were recorded as NaN: {failures}.")
        if "TimeoutError" in failures:
            print("  TimeoutError is a HARNESS artefact of judge throttling, not a "
                  "judge verdict.\n  Re-run --score-only with a higher "
                  "EVAL_RAGAS_TIMEOUT to recover those cells.")

    n_recursion = sum(1 for r in rows if r.get("recursion_limit_hit"))
    if n_recursion:
        print(f"\n  {n_recursion} item(s) hit the agent's recursion limit and returned no "
              f"answer.\n  These are agent-capability failures, not retrieval failures.")
    for m in METRIC_NAMES:
        if cov[m]["coverage"] < MIN_METRIC_COVERAGE:
            print(f"\n  WARNING: {m} scored on only {cov[m]['coverage'] * 100:.1f}% of items. "
                  f"Its mean is\n  reported for completeness but must NOT be quoted as a "
                  f"headline number.")


def _print_resume_hint(payload: dict) -> None:
    """Print the exact command that resumes this run from its cache."""
    cfg = payload["config"]
    # --judge-provider must be echoed alongside --judge-model.  The provider
    # defaults to google, so printing a Groq model id on its own produces a
    # command that resolves a Gemini provider for a Groq model and fails.
    judge_flags = (f"--judge-provider {cfg['judge_provider']} "
                   f"--judge-model {cfg['judge_model']}")
    print("\nTo resume (cached items are skipped; only missing items re-run):")
    print(f"    python -m eval.run_eval --benchmark {cfg['benchmark_file']} {judge_flags}")
    print("\nTo re-judge what is already cached without any new agent calls:")
    print(f"    python -m eval.run_eval --score-only "
          f"--benchmark {cfg['benchmark_file']} {judge_flags}")
    print(f"\nCache: {CACHE_FILE.relative_to(REPO_ROOT)} "
          f"({len(load_cache())} entries)")


# ── Judge construction ───────────────────────────────────────────────────────

def build_token_meter():
    """Return a callback handler that totals judge token usage over a pass.

    Measured token cost is what the free-tier budgeting in EVALUATION.md rests
    on, so this meters it rather than estimating.

    The handler MUST be a real BaseCallbackHandler subclass.  RAGAS drives its
    metrics through LangChain's ASYNC callback manager, which calls
    ahandle_event() on every handler; a duck-typed object that merely defines
    on_llm_end() satisfies no async contract — the coroutine is never awaited,
    every metric raises, and the entire scoring pass silently returns NaN.  That
    failure mode is invisible in the output, because NaN looks identical to a
    judge that legitimately could not score the item, which is exactly why it is
    spelled out here.

    Built inside a function so the langchain import stays lazy and `--dry-run`
    keeps working without langchain installed.
    """
    from langchain_core.callbacks import BaseCallbackHandler

    class _TokenMeter(BaseCallbackHandler):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0
            self.input_tokens = 0
            self.output_tokens = 0

        def on_chat_model_start(self, *args, **kwargs) -> None:
            self.calls += 1

        def on_llm_start(self, *args, **kwargs) -> None:
            self.calls += 1

        def on_llm_end(self, response, **kwargs) -> None:
            # Defensive about missing usage metadata: a provider that omits it
            # must cost us the measurement, never the run.
            for generations in getattr(response, "generations", []) or []:
                for generation in generations:
                    message = getattr(generation, "message", None)
                    meta = getattr(message, "usage_metadata", None) if message else None
                    if isinstance(meta, dict):
                        self.input_tokens += meta.get("input_tokens") or 0
                        self.output_tokens += meta.get("output_tokens") or 0

    return _TokenMeter()


class _ExecutorErrorCounter(logging.Handler):
    """Count the exceptions RAGAS's executor swallows into NaN scores.

    RAGAS catches per-job exceptions, logs them, and records NaN.  Without this
    counter a NaN caused by OUR throttling (an executor timeout) is
    indistinguishable in the results file from a NaN caused by the judge
    genuinely failing to score an item — and those two mean opposite things.
    Attaching a handler to the ragas.executor logger is the only place the
    distinction is still visible.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.counts: dict[str, int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "Exception raised in Job" not in message:
            return
        # Message shape: "Exception raised in Job[3]: TimeoutError()"
        kind = message.rsplit(":", 1)[-1].strip().split("(")[0].strip() or "Unknown"
        self.counts[kind] = self.counts.get(kind, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def build_judge(provider: str, model: str, api_key: str):
    """Build the RAGAS judge LLM wrapper for *provider*/*model*.

    A rate limiter is attached to the chat model itself rather than relying on
    RAGAS-level concurrency limits, because RAGAS fans metrics out across a
    worker pool and only a limiter on the model can cap the aggregate request
    rate that actually reaches the provider.

    bypass_n=True makes RAGAS issue N separate single-candidate requests instead
    of one N-candidate request — see "KNOWN MEASUREMENT ARTEFACTS" at the top.
    """
    from langchain_core.rate_limiters import InMemoryRateLimiter
    from ragas.llms import LangchainLLMWrapper

    limiter = InMemoryRateLimiter(
        requests_per_second=judge_rps(provider),
        check_every_n_seconds=0.5,
        max_bucket_size=1,
    )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        chat = ChatGoogleGenerativeAI(
            model=model, google_api_key=api_key, temperature=0, rate_limiter=limiter,
        )
    elif provider == "groq":
        # Groq exposes an OpenAI-compatible endpoint, so ChatOpenAI is used with
        # a base_url override.  This avoids adding langchain-groq as a
        # dependency purely for the cross-family judge spot check.
        from langchain_openai import ChatOpenAI

        chat = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            temperature=0,
            rate_limiter=limiter,
            # Groq's free tier is token-bound (8k TPM) and a faithfulness NLI
            # call carrying the full retrieved contexts can be several thousand
            # tokens on its own, so a 429 is expected even under the request
            # limiter.  Absorb them here rather than letting them surface as a
            # failed job and a NaN score.
            max_retries=8,
        )
    else:
        raise ValueError(
            f"Unsupported judge provider {provider!r}. Supported: google, groq."
        )

    return LangchainLLMWrapper(chat, bypass_n=JUDGE_BYPASS_N)


def resolve_judge(args) -> tuple[str, str, str]:
    """Resolve (provider, model, api_key) for the judge from flags then .env.

    Two independent judge configurations live in .env: the default Gemini judge
    (GEMINI_API_KEY + RAGAS_LLM_MODEL) and a cross-family judge
    (RAGAS_JUDGE_PROVIDER / RAGAS_JUDGE_MODEL / RAGAS_JUDGE_API_KEY).  Passing
    --judge-provider selects the latter.  Credentials are only ever read from
    the environment — never accepted on the command line, where they would land
    in shell history.

    The provider default is the literal "google", NOT $RAGAS_JUDGE_PROVIDER.
    Deriving it from the env var meant that `--judge-model <a-gemini-model>`
    alone silently produced a run labelled provider=groq: the results file then
    misreports which family judged it, which is precisely the provenance error
    this harness exists to make impossible.  Selecting a cross-family judge is
    therefore always an explicit --judge-provider.
    """
    provider = (args.judge_provider or "google").strip().lower()

    if provider == "google":
        model = args.judge_model or os.getenv("RAGAS_LLM_MODEL") or "gemini-2.5-flash-lite"
        key = os.getenv("GEMINI_API_KEY")
        key_var = "GEMINI_API_KEY"
    else:
        model = args.judge_model or os.getenv("RAGAS_JUDGE_MODEL")
        key = os.getenv("RAGAS_JUDGE_API_KEY")
        key_var = "RAGAS_JUDGE_API_KEY"

    if not model:
        raise EnvironmentError(
            f"No judge model resolved for provider {provider!r}. Set "
            f"{'RAGAS_LLM_MODEL' if provider == 'google' else 'RAGAS_JUDGE_MODEL'} "
            f"in .env or pass --judge-model."
        )
    if not key:
        raise EnvironmentError(
            f"{key_var} is not set. Add it to .env to run RAGAS scoring."
        )
    return provider, model, key


# ── Generation phase ─────────────────────────────────────────────────────────

def generate_outputs(benchmark, agent_model, prompt_version, cache, use_cache=True):
    """Run the agent over *benchmark*, checkpointing to the cache each item.

    Returns (records, stop_reason).  ``stop_reason`` is None on a clean pass and
    a human-readable string when the run stopped on a quota wall — in which case
    ``records`` holds every item that DID complete, all of them already
    persisted to the cache.
    """
    records: list[dict] = []
    stop_reason: str | None = None
    n_from_cache = 0

    for i, row in enumerate(benchmark, start=1):
        item_id = row["id"]
        key = _cache_key(item_id, agent_model, prompt_version)

        if use_cache and key in cache:
            records.append(cache[key])
            n_from_cache += 1
            logger.info("[%d/%d] %s — cached, skipping generation",
                        i, len(benchmark), item_id)
            continue

        logger.info("[%d/%d] %s — running agent: %s",
                    i, len(benchmark), item_id, row["question"][:70])
        try:
            answer, contexts, n_messages = run_agent_capture(row["question"])
        except Exception as exc:  # noqa: BLE001 — any agent failure must be classified
            if _is_quota_error(exc):
                # Do NOT record a stub: a quota refusal says nothing about the
                # agent's answer, and persisting it would poison the cache with
                # a permanent "failure" for an item that was never attempted.
                logger.error("Quota exhausted at item %d (%s): %s", i, item_id, exc)
                stop_reason = (
                    f"provider quota exhausted at item {i}/{len(benchmark)} "
                    f"({item_id}): {type(exc).__name__}"
                )
                break
            logger.exception("Agent failed on %s — recording error and continuing.", item_id)
            record = _record(row, agent_model, prompt_version,
                             answer=f"<agent error: {exc}>", contexts=[],
                             n_messages=0, error=str(exc))
            records.append(record)
            cache[key] = record
            save_cache(cache)
            continue

        record = _record(row, agent_model, prompt_version,
                         answer=answer, contexts=contexts, n_messages=n_messages)
        records.append(record)

        # Checkpoint after EVERY item — this is what makes a quota wall cost
        # only the item in flight rather than the whole run.
        cache[key] = record
        save_cache(cache)

        logger.info("  -> %d chars, %d contexts%s", len(answer), len(contexts),
                    "  [RECURSION LIMIT HIT]" if record["recursion_limit_hit"] else "")

        if i < len(benchmark):
            time.sleep(AGENT_SLEEP_SECONDS)

    if n_from_cache:
        logger.info("Reused %d cached agent output(s); generated %d new.",
                    n_from_cache, len(records) - n_from_cache)
    return records, stop_reason


def _record(row, agent_model, prompt_version, *, answer, contexts, n_messages, error=None):
    """Build one per-item result record.

    Every row carries its own full provenance — item id, stratum, model ids, k
    and prompt version — so a single row remains interpretable when it is read
    in isolation, pulled into a table, or compared against a row from a
    different run.
    """
    from agent.financial_agent import TOP_K

    return {
        "id": row["id"],
        "question_type": row.get("question_type", ""),
        "ticker": row.get("ticker", ""),
        "section": row.get("section", ""),
        "difficulty": row.get("difficulty", ""),
        "requires_table": row.get("requires_table", ""),
        "question": row["question"],
        "ground_truth": row["ground_truth"],
        "answer": answer,
        "contexts": contexts,
        "n_contexts": len(contexts),
        "n_agent_messages": n_messages,
        "recursion_limit_hit": RECURSION_LIMIT_MARKER in (answer or "").lower(),
        "agent_model": agent_model,
        "agent_provider": "google",
        "k": TOP_K,
        "prompt_version": prompt_version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error": error,
    }


# ── Scoring phase ────────────────────────────────────────────────────────────

def score_records(records, judge_provider, judge_model, judge_key, max_judge_calls=None):
    """Score *records* with RAGAS; attach a ``scores`` dict to each in place.

    Returns (n_scored, scores_error, diagnostics).  A quota failure inside RAGAS is caught so
    that the caller can still persist the generated outputs — losing the judge
    pass is recoverable via --score-only, losing the generations is not.

    ``max_judge_calls`` caps the work by truncating the scored subset up-front
    (4 judge calls per item with the three configured metrics), which is what
    keeps a tightly-throttled cross-family pass inside a small token budget.
    """
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy, context_recall
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.run_config import RunConfig
    from ingestion.embedder import build_embeddings

    # faithfulness x2 (statement extraction + NLI), answer_relevancy x3
    # (strictness=3, and JUDGE_BYPASS_N makes each sample a separate call),
    # context_recall x1.  Measured at ~6.4k judge tokens per item on this
    # benchmark, which is what the token-per-day budgeting in EVALUATION.md uses.
    JUDGE_CALLS_PER_ITEM = 6 if JUDGE_BYPASS_N else 4
    to_score = records
    if max_judge_calls is not None:
        budget = max(1, max_judge_calls // JUDGE_CALLS_PER_ITEM)
        if budget < len(records):
            logger.warning(
                "Judge-call cap %d allows only %d of %d items — scoring the first %d.",
                max_judge_calls, budget, len(records), budget,
            )
            to_score = records[:budget]

    for r in records:
        r["scores"] = {m: None for m in METRIC_NAMES}
        r["judge_model"] = judge_model
        r["judge_provider"] = judge_provider

    if not to_score:
        return 0, "no records to score", {}

    # RAGAS errors on an empty contexts list; substitute a single empty string so
    # the row still scores (faithfulness/context_recall correctly fall to 0).
    ds = Dataset.from_dict({
        "question": [r["question"] for r in to_score],
        "answer": [r["answer"] for r in to_score],
        "contexts": [r["contexts"] if r["contexts"] else [""] for r in to_score],
        "ground_truth": [r["ground_truth"] for r in to_score],
    })

    logger.info("RAGAS judge: %s via %s (%.2f rps cap, bypass_n=%s, timeout=%ds)",
                judge_model, judge_provider, judge_rps(judge_provider),
                JUDGE_BYPASS_N, RAGAS_TIMEOUT_SECONDS)
    judge = build_judge(judge_provider, judge_model, judge_key)
    embeddings = LangchainEmbeddingsWrapper(build_embeddings())

    # Watch the executor for jobs it turns into NaN, and meter judge token usage
    # so the per-item token cost of a run is a measured number rather than an
    # estimate — free-tier budgeting depends on it.
    err_counter = _ExecutorErrorCounter()
    executor_logger = logging.getLogger("ragas.executor")
    executor_logger.addHandler(err_counter)
    usage = build_token_meter()

    try:
        result = evaluate(
            ds,
            metrics=[faithfulness, answer_relevancy, context_recall],
            llm=judge,
            embeddings=embeddings,
            run_config=RunConfig(
                max_workers=RAGAS_MAX_WORKERS,
                seed=RAGAS_SEED,
                timeout=RAGAS_TIMEOUT_SECONDS,
            ),
            callbacks=[usage],
        )
    except Exception as exc:  # noqa: BLE001 — quota / rate-limit / network
        logger.exception("RAGAS evaluate() failed — generated outputs are safe in cache.")
        return 0, f"{type(exc).__name__}: {exc}", {}
    finally:
        executor_logger.removeHandler(err_counter)

    if err_counter.total:
        logger.warning(
            "RAGAS executor swallowed %d job failure(s) into NaN scores: %s. "
            "A TimeoutError here is a HARNESS artefact (throttling), NOT a judge "
            "verdict — do not read those NaNs as model behaviour.",
            err_counter.total, err_counter.counts,
        )
    logger.info("Judge token usage: %d in / %d out over %d call(s) (~%.0f tokens/item)",
                usage.input_tokens, usage.output_tokens, usage.calls,
                (usage.input_tokens + usage.output_tokens) / max(1, len(to_score)))
    score_diagnostics = {
        "executor_failures": dict(err_counter.counts),
        "judge_calls": usage.calls,
        "judge_input_tokens": usage.input_tokens,
        "judge_output_tokens": usage.output_tokens,
    }

    score_rows = result.to_pandas().to_dict(orient="records")
    for record, srow in zip(to_score, score_rows):
        record["scores"] = {
            m: (float(srow[m]) if _is_number(srow.get(m)) else None)
            for m in METRIC_NAMES
        }
        record["scored_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    n_scored = sum(
        1 for r in records
        if any(_is_number(v) for v in r["scores"].values())
    )
    return n_scored, None, score_diagnostics


# ── Main ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the financial agent against a benchmark and score it with RAGAS.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the smoke benchmark questions and exit — no agent or RAGAS calls. "
             "This is the CI path; it needs no API key.",
    )
    p.add_argument(
        "--smoke", action="store_true",
        help=f"Use the 5-item smoke benchmark ({SMOKE_BENCHMARK_FILE.name}) instead "
             f"of the full {BENCHMARK_FILE.name}.",
    )
    p.add_argument(
        "--benchmark", type=Path, default=None,
        help="Explicit benchmark CSV path (overrides --smoke).",
    )
    p.add_argument(
        "--score-only", action="store_true",
        help="Skip all generation; score whatever is already in the agent-output "
             "cache. Use to re-judge with a different judge model at zero "
             "generation cost.",
    )
    p.add_argument(
        "--generate-only", action="store_true",
        help="Run the agent and checkpoint outputs to the cache, then STOP without "
             "judging. The multi-day primitive: agent and judge quotas live in "
             "different buckets that reset independently, so generation can be "
             "accumulated across days and judged in one coherent pass at the end.",
    )
    p.add_argument(
        "--no-cache", action="store_true",
        help="Ignore cached agent outputs and regenerate every item (costs full "
             "agent quota).",
    )
    p.add_argument("--ids", default=None,
                   help="Comma-separated item ids to restrict the run to.")
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the first N benchmark items.")
    p.add_argument("--judge-provider", default=None, choices=["google", "groq"],
                   help="Judge provider. Default: RAGAS_JUDGE_PROVIDER, else google.")
    p.add_argument("--judge-model", default=None,
                   help="Judge model id. Default: RAGAS_LLM_MODEL (google) or "
                        "RAGAS_JUDGE_MODEL (other providers).")
    p.add_argument("--max-judge-calls", type=int, default=None,
                   help="Hard cap on judge LLM calls; the scored subset is "
                        "truncated to fit and the run stops cleanly at the cap.")
    p.add_argument("--label", default=None,
                   help="Run label used in the results filename "
                        "(default: 'baseline'). Written to eval/results/<label>-<commit>.json.")
    p.add_argument("--out", type=Path, default=None,
                   help="Explicit results file path (overrides --label).")
    return p


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ── Dry-run: short-circuit before importing any LLM library ──────────────
    if args.dry_run:
        path = args.benchmark or SMOKE_BENCHMARK_FILE
        benchmark = load_benchmark(path)
        logger.info("Loaded %d benchmark rows from %s", len(benchmark), path)
        print(f"[DRY RUN] {len(benchmark)} benchmark question(s) from {path.name}:")
        for i, row in enumerate(benchmark, start=1):
            print(f"\n  {i}. [{row['id']}  {row['question_type']}] Q: {row['question']}")
            print(f"     GT: {row['ground_truth']}")
        return 0

    # ── Resolve benchmark + configuration ────────────────────────────────────
    benchmark_path = args.benchmark or (SMOKE_BENCHMARK_FILE if args.smoke else BENCHMARK_FILE)
    benchmark = load_benchmark(benchmark_path)
    if args.ids:
        wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
        by_id = {r["id"]: r for r in benchmark}
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise ValueError(f"--ids not present in {benchmark_path.name}: {missing}")
        benchmark = [by_id[i] for i in wanted]
    if args.limit:
        benchmark = benchmark[: args.limit]
    if not benchmark:
        logger.error("No benchmark rows selected — nothing to evaluate.")
        return 1

    import ragas
    from agent.financial_agent import LLM_MODEL as AGENT_MODEL, TOP_K
    from ingestion.embedder import EMBEDDING_MODEL

    prompt_version = _prompt_version()
    judge_provider, judge_model, judge_key = resolve_judge(args)
    commit, dirty = _git_commit()
    label = args.label or ("smoke" if args.smoke else "baseline")

    logger.info("Benchmark %s (%d items) | agent %s | judge %s/%s | prompt %s",
                benchmark_path.name, len(benchmark), AGENT_MODEL,
                judge_provider, judge_model, prompt_version)

    # ── Generation ───────────────────────────────────────────────────────────
    cache = load_cache()
    if args.score_only:
        records = []
        missing = []
        for row in benchmark:
            key = _cache_key(row["id"], AGENT_MODEL, prompt_version)
            if key in cache:
                records.append(dict(cache[key]))
            else:
                missing.append(row["id"])
        if missing:
            logger.warning(
                "--score-only: %d of %d items have no cached agent output and will "
                "be omitted: %s", len(missing), len(benchmark),
                ", ".join(missing[:10]) + ("..." if len(missing) > 10 else ""),
            )
        if not records:
            logger.error(
                "--score-only found no cached agent outputs for agent=%s prompt=%s. "
                "Run without --score-only first.", AGENT_MODEL, prompt_version,
            )
            return 1
        stop_reason = None
    else:
        records, stop_reason = generate_outputs(
            benchmark, AGENT_MODEL, prompt_version, cache,
            use_cache=not args.no_cache,
        )

    # ── Scoring ──────────────────────────────────────────────────────────────
    scores_error = None
    n_scored = 0
    score_diag: dict = {}
    if args.generate_only:
        logger.info(
            "--generate-only: %d item(s) checkpointed to %s. Skipping the judge "
            "pass; nothing is scored and no aggregates will be produced.",
            len(records), CACHE_FILE.relative_to(REPO_ROOT),
        )
        for r in records:
            r.setdefault("scores", {m: None for m in METRIC_NAMES})
            r.setdefault("judge_model", judge_model)
            r.setdefault("judge_provider", judge_provider)
    elif records and stop_reason is None:
        n_scored, scores_error, score_diag = score_records(
            records, judge_provider, judge_model, judge_key,
            max_judge_calls=args.max_judge_calls,
        )
    elif stop_reason:
        logger.warning("Skipping the judge pass: generation stopped early "
                       "(%s). Nothing partial will be scored or averaged.", stop_reason)
        for r in records:
            r.setdefault("scores", {m: None for m in METRIC_NAMES})
            r.setdefault("judge_model", judge_model)
            r.setdefault("judge_provider", judge_provider)

    # ── Assemble payload ─────────────────────────────────────────────────────
    complete = (
        stop_reason is None
        and scores_error is None
        and len(records) == len(benchmark)
        and n_scored == len(benchmark)
    )
    withheld_reason = None
    if not complete:
        parts = []
        if stop_reason:
            parts.append(stop_reason)
        if scores_error:
            parts.append(f"judge pass failed: {scores_error}")
        if len(records) != len(benchmark):
            parts.append(f"generated {len(records)} of {len(benchmark)} requested items")
        if args.generate_only:
            parts.append("--generate-only: generation checkpointed, judge pass not run")
        elif n_scored != len(benchmark):
            parts.append(f"scored {n_scored} of {len(benchmark)} requested items")
        withheld_reason = "; ".join(parts)

    payload = {
        "schema_version": 2,
        "run_id": f"{label}-{commit}",
        "label": label,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "config": {
            "agent_model": AGENT_MODEL,
            "agent_provider": "google",
            "judge_model": judge_model,
            "judge_provider": judge_provider,
            "judge_temperature_configured": 0.0,
            "judge_temperature_note": (
                "RAGAS overrides temperature to 0.3 for metrics that request n>1 "
                "generations (answer_relevancy, strictness=3); see "
                "ragas.llms.base.BaseRagasLLM.get_temperature. answer_relevancy is "
                "therefore NOT deterministic even with temperature=0 configured."
            ),
            "judge_bypass_n": JUDGE_BYPASS_N,
            "judge_requests_per_second": judge_rps(judge_provider),
            "answer_relevancy_strictness": 3,
            "embedding_model": EMBEDDING_MODEL,
            "k": TOP_K,
            "prompt_version": prompt_version,
            "ragas_version": ragas.__version__,
            "ragas_seed": RAGAS_SEED,
            "ragas_max_workers": RAGAS_MAX_WORKERS,
            "agent_sleep_seconds": AGENT_SLEEP_SECONDS,
            "agent_recursion_limit": 20,
            "benchmark_file": str(benchmark_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "benchmark_n": len(benchmark),
            "metrics": list(METRIC_NAMES),
        },
        "run_status": {
            "complete": complete,
            "n_requested": len(benchmark),
            "n_generated": len(records),
            "n_scored": n_scored,
            "stopped_early": stop_reason is not None,
            "stop_reason": stop_reason,
            "scores_error": scores_error,
        },
        "judge_diagnostics": score_diag,
        "metric_coverage": metric_coverage(records) if records else {},
        "aggregates": build_aggregates(records) if complete else None,
        "aggregates_withheld_reason": withheld_reason,
        "results": records,
    }

    out_path = args.out or (RESULTS_DIR / f"{label}-{commit}.json")
    save_results(payload, out_path)
    print_report(payload)
    print(f"\nPer-item evidence: {out_path.relative_to(REPO_ROOT)}")

    if stop_reason:
        return EXIT_QUOTA_EXHAUSTED
    return 0


if __name__ == "__main__":
    sys.exit(main())
