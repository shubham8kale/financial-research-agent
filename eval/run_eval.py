# eval/run_eval.py
#
# PURPOSE
# -------
# End-to-end RAGAS evaluation harness for the direct ReAct agent in
# agent/financial_agent.py.  Runs a fixed benchmark of questions through the
# agent, captures the generated answer and the retrieved context passages
# (from ToolMessage observations), then scores the run with three RAGAS
# metrics: faithfulness, answer_relevancy, and context_recall.
#
# WHY THE DIRECT AGENT (NOT THE MCP CLIENT)?
# -------------------------------------------
# The MCP path adds a network hop (HTTP to the MCP server) which introduces
# flakiness and rate-limit interactions that are unrelated to answer quality.
# For eval we want to measure the agent + retriever + LLM, not transport.
#
# WHY GEMINI 2.5 FLASH-LITE AS THE RAGAS JUDGE?
# ---------------------------------------------
# The project standardises on gemini-2.5-flash-lite for every LLM call
# (query engine, agent, RAGAS judge).  Using the same model for eval keeps
# cost/quota behaviour predictable and means the judge and the generator
# have comparable knowledge; it also avoids shipping a second provider just
# for eval.  We still explicitly pass temperature=0 so scoring is stable.
#
# WHY HuggingFace MiniLM EMBEDDINGS AS THE RAGAS EMBEDDINGS?
# ----------------------------------------------------------
# answer_relevancy needs an embedding model to compare the generated answer
# against synthetic counter-questions.  Reusing the same all-MiniLM-L6-v2
# model that indexed our corpus keeps the eval pipeline self-contained
# (no extra embedding API key needed) and means relevance is measured in
# the same vector space the retriever operates in.
#
# RATE LIMITING
# -------------
# Gemini free-tier has per-minute request limits that are easy to exhaust
# when an agent run fires several tool-calling round trips back-to-back.
# A 5-second sleep between agent invocations keeps us well under the quota
# and is short enough that a 5-row benchmark still finishes in under a minute.

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Make the repo root importable whether the script is run as
# `python -m eval.run_eval` or `python eval/run_eval.py`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ── Paths & constants ────────────────────────────────────────────────────────

EVAL_DIR = Path(__file__).resolve().parent
BENCHMARK_FILE = EVAL_DIR / "benchmark.csv"
RESULTS_FILE = EVAL_DIR / "results.json"

# Pause between successive agent invocations.  Each invocation typically
# makes 2–3 Gemini calls (tool-calling round trips), so spacing the top-level
# calls by 5 s keeps the sustained RPM well below the free-tier ceiling.
AGENT_SLEEP_SECONDS = 5

# Model used for the RAGAS judge is resolved inside main() AFTER load_dotenv()
# has run — see the RAGAS_LLM_MODEL comment there.  Reading os.getenv() at
# module import time would happen before .env is loaded and would silently
# fall back to the default even when RAGAS_LLM_MODEL is set in the .env file.

METRIC_NAMES = ("faithfulness", "answer_relevancy", "context_recall")


# ── Benchmark loading ────────────────────────────────────────────────────────

def load_benchmark(path: Path) -> list[dict]:
    """Load benchmark.csv into a list of {question, ground_truth} dicts."""
    if not path.exists():
        raise FileNotFoundError(
            f"Benchmark file not found: {path}. "
            f"Create it with columns: question, ground_truth"
        )
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    required = {"question", "ground_truth"}
    if rows and not required.issubset(rows[0].keys()):
        raise ValueError(
            f"benchmark.csv must have columns {sorted(required)}; "
            f"found {sorted(rows[0].keys())}"
        )
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


def run_agent_capture(question: str) -> tuple[str, list[str]]:
    """Invoke the direct agent and return (final_answer, retrieved_contexts).

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
        # AIMessage; fall back to str() to keep downstream code simple.
        final_answer = str(final_answer)
    contexts = _extract_contexts(messages)
    return final_answer, contexts


# ── Persistence ──────────────────────────────────────────────────────────────

def save_results(payload: dict, path: Path) -> None:
    """Write the results JSON, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    logger.info("Saved results → %s", path)


# ── Printing ─────────────────────────────────────────────────────────────────

def _print_scores(per_question: list[dict], score_rows: list[dict] | None) -> None:
    """Print per-question metric scores and an overall average table."""
    print("\n=== Per-question RAGAS scores ===")
    if not score_rows:
        print("  (no scores — evaluate() did not complete)")
        return

    aggregates: dict[str, list[float]] = {m: [] for m in METRIC_NAMES}
    for i, row in enumerate(score_rows, start=1):
        q_preview = (row.get("question") or per_question[i - 1]["question"])[:70]
        print(f"\n[{i}] {q_preview}")
        for m in METRIC_NAMES:
            val = row.get(m)
            if isinstance(val, (int, float)) and val == val:  # not NaN
                print(f"    {m:<18}: {val:.4f}")
                aggregates[m].append(float(val))
            else:
                print(f"    {m:<18}: n/a")

    print("\n=== Overall averages ===")
    for m in METRIC_NAMES:
        vals = aggregates[m]
        if vals:
            print(f"  {m:<18}: {sum(vals) / len(vals):.4f}  (n={len(vals)})")
        else:
            print(f"  {m:<18}: n/a")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()

    # Resolve RAGAS_LLM_MODEL *after* load_dotenv() so values in the project's
    # .env file are actually seen.  Deliberately read from its own env var
    # (RAGAS_LLM_MODEL) rather than the agent's LLM_MODEL so the judge can be
    # pointed at a different Gemini model — and therefore a different per-minute
    # quota bucket — from the agent.  This matters because an eval run fires
    # both the agent AND the judge back-to-back; sharing one model's RPM budget
    # causes 429s mid-scoring.  Default falls back to flash-lite so the
    # pipeline still works with no extra configuration.
    ragas_llm_model = os.getenv("RAGAS_LLM_MODEL", "gemini-2.5-flash-lite")
    parser = argparse.ArgumentParser(
        description="Run the financial agent against benchmark.csv and score it with RAGAS."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the benchmark questions and exit — no agent or RAGAS calls.",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help=(
            "Skip the agent loop; load previously saved agent outputs from "
            "eval/results.json and only run RAGAS scoring. Useful for retrying "
            "RAGAS after a quota exhaustion without re-burning agent quota."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ── Dry-run: short-circuit before importing any LLM libraries ────────────
    if args.dry_run:
        benchmark = load_benchmark(BENCHMARK_FILE)
        logger.info("Loaded %d benchmark rows from %s", len(benchmark), BENCHMARK_FILE)
        print(f"[DRY RUN] {len(benchmark)} benchmark question(s):")
        for i, row in enumerate(benchmark, start=1):
            print(f"\n  {i}. Q: {row['question']}")
            print(f"     GT: {row['ground_truth']}")
        return

    # ── 1. Obtain per_question — either by running the agent or by loading ──
    #     a prior run's results.json (when --score-only is set).
    per_question: list[dict]
    stopped_early: bool
    if args.score_only:
        if not RESULTS_FILE.exists():
            raise FileNotFoundError(
                f"--score-only requires a prior results file at {RESULTS_FILE}. "
                f"Run without --score-only first to generate agent outputs."
            )
        with open(RESULTS_FILE, encoding="utf-8") as f:
            prior = json.load(f)
        per_question = prior.get("results") or []
        if not per_question:
            logger.error(
                "results.json has no 'results' payload to score — nothing to do."
            )
            return
        stopped_early = bool(prior.get("stopped_early", False))
        logger.info(
            "[--score-only] Loaded %d prior agent outputs from %s",
            len(per_question), RESULTS_FILE,
        )
    else:
        benchmark = load_benchmark(BENCHMARK_FILE)
        logger.info("Loaded %d benchmark rows from %s", len(benchmark), BENCHMARK_FILE)

        if not benchmark:
            logger.error("benchmark.csv has a header but no rows — nothing to evaluate.")
            return

        per_question = []
        stopped_early = False
        for i, row in enumerate(benchmark, start=1):
            question = row["question"]
            ground_truth = row["ground_truth"]
            logger.info("[%d/%d] Running agent: %s", i, len(benchmark), question)
            try:
                answer, contexts = run_agent_capture(question)
            except Exception as exc:  # noqa: BLE001 — we want to catch anything the agent raises
                logger.exception("Agent failed on question %d — stopping early.", i)
                per_question.append({
                    "question": question,
                    "ground_truth": ground_truth,
                    "answer": f"<agent error: {exc}>",
                    "contexts": [],
                    "error": str(exc),
                })
                stopped_early = True
                break

            logger.info(
                "  → answer: %d chars, %d contexts captured", len(answer), len(contexts)
            )
            per_question.append({
                "question": question,
                "ground_truth": ground_truth,
                "answer": answer,
                "contexts": contexts,
            })
            if i < len(benchmark):
                time.sleep(AGENT_SLEEP_SECONDS)

        # Persist agent outputs BEFORE RAGAS runs, so that if RAGAS later
        # fails (quota, rate-limit, network) the agent work is not lost —
        # the user can rerun with --score-only to retry scoring against the
        # same outputs.
        early_timestamp = datetime.now().isoformat(timespec="seconds")
        save_results({
            "timestamp": early_timestamp,
            "ragas_llm_model": ragas_llm_model,
            "metrics": list(METRIC_NAMES),
            "stopped_early": stopped_early,
            "results": per_question,
            "scores": None,
            "scores_error": None,
        }, RESULTS_FILE)

    timestamp = datetime.now().isoformat(timespec="seconds")

    # ── 2. Build the RAGAS dataset ───────────────────────────────────────────
    # RAGAS 0.2.x still accepts a HuggingFace `datasets.Dataset` with these
    # four column names (question, answer, contexts, ground_truth).
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy, context_recall
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from langchain_google_genai import ChatGoogleGenerativeAI
    from ingestion.embedder import build_embeddings

    # RAGAS will error on an empty contexts list; substitute a single empty
    # string so the row still scores (faithfulness/context_recall will fall
    # to 0, which is the correct signal).
    ds = Dataset.from_dict({
        "question": [r["question"] for r in per_question],
        "answer": [r["answer"] for r in per_question],
        "contexts": [r["contexts"] if r["contexts"] else [""] for r in per_question],
        "ground_truth": [r["ground_truth"] for r in per_question],
    })

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. Add it to .env to run RAGAS scoring."
        )

    logger.info("RAGAS judge LLM: %s (from RAGAS_LLM_MODEL)", ragas_llm_model)
    ragas_llm = LangchainLLMWrapper(
        ChatGoogleGenerativeAI(
            model=ragas_llm_model,
            google_api_key=api_key,
            temperature=0,
        )
    )
    ragas_embeddings = LangchainEmbeddingsWrapper(build_embeddings())
    metrics = [faithfulness, answer_relevancy, context_recall]

    # ── 3. Run RAGAS, catching quota/network errors so partial results land ──
    score_rows: list[dict] | None = None
    scores_error: str | None = None
    try:
        logger.info("Running RAGAS evaluate() with metrics: %s", [m.name for m in metrics])
        ragas_result = evaluate(
            ds,
            metrics=metrics,
            llm=ragas_llm,
            embeddings=ragas_embeddings,
        )
        score_rows = ragas_result.to_pandas().to_dict(orient="records")
    except Exception as exc:  # noqa: BLE001 — quota / rate-limit / network
        logger.exception("RAGAS evaluate() failed — saving partial results.")
        scores_error = f"{type(exc).__name__}: {exc}"

    # ── 4. Print and persist ─────────────────────────────────────────────────
    _print_scores(per_question, score_rows)

    payload = {
        "timestamp": timestamp,
        "ragas_llm_model": ragas_llm_model,
        "metrics": list(METRIC_NAMES),
        "stopped_early": stopped_early,
        "results": per_question,
        "scores": score_rows,
        "scores_error": scores_error,
    }
    save_results(payload, RESULTS_FILE)


if __name__ == "__main__":
    main()
