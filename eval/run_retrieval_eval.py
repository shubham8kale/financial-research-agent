# eval/run_retrieval_eval.py
#
# PURPOSE
# -------
# Score the RETRIEVER on its own — no agent, no judge, no LLM call of any kind.
# Each benchmark question is sent verbatim to the retriever, the top-k chunk
# ids come back, and eval/retrieval_metrics.py compares them against the
# labelled chunks in eval/benchmark_chunks.json.  A full pass over the
# benchmark takes about a minute on CPU and costs nothing, which is what makes
# it usable as a per-configuration instrument: every retrieval switch upgrade 2
# adds is measured here first, and only the winner is spent on with the judge.
#
# WHAT THIS DOES AND DOES NOT MEASURE
# -----------------------------------
# The query is the benchmark question as written.  Inside the agent, the query
# is whatever the model chose to type, and eval/EVALUATION.md finding 3 showed
# that choice moves retrieved passages on 7 of 8 items.  So this file measures
# the retriever with query wording held fixed; the agent-level hit rate that
# eval/run_eval.py records on every generation run measures the retriever plus
# the agent's query composition.  When they disagree, the query is the problem.
#
# Latency is recorded per query (wall clock around the retrieval call, after a
# warm-up query so the embedding model load is not counted) so that upgrade 2's
# ablation table can show what each technique costs as well as what it gains.

import argparse
import json
import logging
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.chunk_labels import LABELS_FILE, load_labels, scorable_groups  # noqa: E402
from eval.experiment import benchmark_version, config_hash, find_existing_result  # noqa: E402
from eval.retrieval_metrics import DEFAULT_KS, METRIC_KEYS, aggregate, item_metrics  # noqa: E402
from eval.run_eval import (  # noqa: E402
    BENCHMARK_FILE, QUESTION_TYPES, RESULTS_DIR, SMOKE_BENCHMARK_FILE, THIN_STRATUM_N,
    _git_commit, load_benchmark, save_results,
)

logger = logging.getLogger(__name__)

RESULT_KIND = "retrieval"
SCHEMA_VERSION = 3
FETCH_K = max(DEFAULT_KS)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def _breakdown(items: list[dict], key: str, order: tuple[str, ...] | None = None) -> dict:
    values = list(order or ())
    for it in items:
        v = str(it.get(key, ""))
        if v and v not in values:
            values.append(v)
    out = {}
    for v in values:
        subset = [it for it in items if str(it.get(key, "")) == v]
        if subset:
            out[v] = aggregate(subset)
    return out


def evaluate_items(rows: list[dict], labels: dict, retrieve_fn, k: int, ticker_filter: bool) -> tuple[list[dict], list[str]]:
    """Run *retrieve_fn(question, k, ticker)* for every labelled row; return (items, skipped ids)."""
    items: list[dict] = []
    skipped: list[str] = []
    for row in rows:
        groups = scorable_groups(labels, row["id"])
        if not groups:
            skipped.append(row["id"])
            continue
        ticker = (row.get("ticker") or "").strip().upper() or None
        t0 = time.perf_counter()
        ranked = retrieve_fn(row["question"], k, ticker if ticker_filter else None)
        latency_ms = (time.perf_counter() - t0) * 1000
        item = {
            "id": row["id"],
            "question_type": row.get("question_type", ""),
            "ticker": ticker or "",
            "difficulty": row.get("difficulty", ""),
            "requires_table": row.get("requires_table", ""),
            "question": row["question"],
            "relevant_groups": groups,
            "ranked_chunk_ids": ranked,
            "latency_ms": round(latency_ms, 1),
            **item_metrics(ranked, groups),
        }
        items.append(item)
    return items, skipped


def build_aggregates(items: list[dict]) -> dict:
    latencies = [it["latency_ms"] for it in items]
    return {
        "overall": aggregate(items),
        "by_question_type": _breakdown(items, "question_type", QUESTION_TYPES),
        "by_requires_table": _breakdown(items, "requires_table", ("True", "False")),
        "by_difficulty": _breakdown(items, "difficulty", ("easy", "medium", "hard")),
        "latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "mean": round(statistics.fmean(latencies), 1) if latencies else None,
        },
    }


def print_report(payload: dict) -> None:
    cfg = payload["config"]
    agg = payload["aggregates"]
    keys = ("hit@5", "recall@5", "mrr", "ndcg@5", "recall@10", "recall@25")
    print("\n" + "=" * 78)
    print(f"RETRIEVAL RUN {payload['run_id']}   ({payload['timestamp']})")
    print("=" * 78)
    print(f"  retrieval    : {json.dumps(cfg['retrieval'])}")
    print(f"  embeddings   : {cfg['embedding_model']}")
    print(f"  benchmark    : {cfg['benchmark_file']} @ {cfg['benchmark_version']}  "
          f"({payload['run_status']['n_scored']} scored, {len(payload['run_status']['skipped_ids'])} unlabelled)")
    print(f"  config hash  : {cfg['config_hash']}   git {payload['git_commit']}"
          f"{'  [DIRTY]' if payload['git_dirty'] else ''}")
    lat = agg["latency_ms"]
    print(f"  latency      : p50 {lat['p50']} ms, p95 {lat['p95']} ms per query")

    def row(label: str, block: dict) -> None:
        cells = "  ".join(f"{(block[k] if block[k] is not None else float('nan')):>9.4f}" for k in keys)
        print(f"  {label:<16}{cells}   n={block['n_items']}")

    print("\n  " + " " * 16 + "  ".join(f"{k:>9}" for k in keys))
    row("overall", agg["overall"])
    print("  by question type")
    for q, b in agg["by_question_type"].items():
        row(f"  {q}", b)
    print("  by requires_table")
    for q, b in agg["by_requires_table"].items():
        row(f"  table={q}", b)
    thin = [(q, b["n_items"]) for q, b in agg["by_question_type"].items() if b["n_items"] <= THIN_STRATUM_N]
    if thin:
        print("\n  Thin strata (anecdote, not measurement): "
              + ", ".join(f"{q} n={n}" for q, n in thin))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Score the retriever against labelled chunks. No LLM calls.")
    p.add_argument("--benchmark", type=Path, default=None, help="Benchmark CSV (default: full benchmark).")
    p.add_argument("--smoke", action="store_true", help=f"Use {SMOKE_BENCHMARK_FILE.name}.")
    p.add_argument("--labels", type=Path, default=LABELS_FILE, help="Chunk labels JSON from eval.chunk_labels.")
    p.add_argument("--k", type=int, default=FETCH_K, help=f"Chunks fetched per query (default {FETCH_K}).")
    p.add_argument("--ticker-filter", action="store_true",
                   help="Apply the benchmark row's ticker as a metadata filter (an oracle filter: "
                        "the agent does not know the ticker unless it infers it).")
    p.add_argument("--label", default="dense", help="Run label used in the results filename.")
    p.add_argument("--out", type=Path, default=None, help="Explicit results path.")
    p.add_argument("--force", action="store_true", help="Re-run even if this exact configuration already has a complete results file.")
    p.add_argument("--dry-run", action="store_true", help="Load the benchmark and labels, report coverage, make no retrieval calls.")
    return p


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    benchmark_path = args.benchmark or (SMOKE_BENCHMARK_FILE if args.smoke else BENCHMARK_FILE)
    rows = load_benchmark(benchmark_path)
    labels = load_labels(args.labels)
    n_labelled = sum(1 for r in rows if scorable_groups(labels, r["id"]))
    if args.dry_run:
        print(f"[DRY RUN] {len(rows)} items in {benchmark_path.name}; {n_labelled} have chunk labels "
              f"in {args.labels.name} (index had {labels.get('index_chunk_count')} chunks).")
        return 0 if n_labelled else 1

    from ingestion.embedder import EMBEDDING_MODEL
    from retrieval.retriever import RetrievalConfig, retrieve

    rc = RetrievalConfig(mode="dense", k=args.k, ticker_filter=args.ticker_filter)
    bench_version = benchmark_version(benchmark_path, args.labels)
    config = {
        "result_kind": RESULT_KIND,
        "schema_version": SCHEMA_VERSION,
        "retrieval": rc.as_dict(),
        "embedding_model": EMBEDDING_MODEL,
        "query_source": "benchmark_question",
        "benchmark_file": str(benchmark_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "labels_file": str(args.labels.relative_to(REPO_ROOT)).replace("\\", "/"),
        "benchmark_version": bench_version,
        "item_ids": sorted(r["id"] for r in rows),
        "metrics": list(METRIC_KEYS),
    }
    cfg_hash = config_hash(config)
    config["config_hash"] = cfg_hash

    existing = find_existing_result(RESULTS_DIR, cfg_hash)
    if existing and not args.force:
        with open(existing, encoding="utf-8") as f:
            payload = json.load(f)
        print(f"This configuration ({cfg_hash}) already has a complete run: {existing.relative_to(REPO_ROOT)}")
        print_report(payload)
        return 0

    from agent.financial_agent import _get_vectorstore
    vs = _get_vectorstore()
    vs.similarity_search("warm-up query", k=1)  # load the embedding model before timing anything

    def retrieve_fn(question: str, k: int, ticker: str | None) -> list[str]:
        return [c.chunk_id for c in retrieve(question, k, ticker=ticker, vectorstore=vs)]

    logger.info("Retrieval eval: %d items, %d labelled, config %s", len(rows), n_labelled, json.dumps(rc.as_dict()))
    items, skipped = evaluate_items(rows, labels, retrieve_fn, args.k, args.ticker_filter)
    commit, dirty = _git_commit()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "result_kind": RESULT_KIND,
        "run_id": f"retrieval-{args.label}-{cfg_hash}",
        "label": args.label,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "config": config,
        "run_status": {
            "complete": bool(items),
            "n_requested": len(rows),
            "n_scored": len(items),
            "skipped_ids": skipped,
            "skipped_reason": "no chunk label (unlocatable reference passage)" if skipped else None,
        },
        "aggregates": build_aggregates(items) if items else None,
        "results": items,
    }
    out_path = args.out or (RESULTS_DIR / f"retrieval-{args.label}-{cfg_hash}.json")
    save_results(payload, out_path)
    print_report(payload)
    print(f"\nPer-item evidence: {out_path.relative_to(REPO_ROOT)}")

    from eval.leaderboard import write_leaderboard
    print(f"Leaderboard: {write_leaderboard(RESULTS_DIR).relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
