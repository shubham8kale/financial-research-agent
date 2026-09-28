# eval/recompute_deterministic.py
#
# PURPOSE
# -------
# Re-derive the judge-free metrics on an existing results file — the figure
# check and the agent-level retrieval hit — from the answers and observations
# it already stores, and rebuild its aggregates.  Judge scores are never
# touched.
#
# WHY IT EXISTS
# -------------
# The judge-free metrics are pure functions of stored data, so when their
# definition changes (a figure-extraction rule, a relabelled chunk) every
# results file can be brought up to the new definition without spending a
# single API call.  Doing that through a committed script, which records what
# it did inside the file, is what keeps the numbers in the docs traceable to
# the rule that produced them.
#
#   python -m eval.recompute_deterministic eval/results/baseline-v3-*.json

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.chunk_labels import LABELS_FILE, load_labels  # noqa: E402
from eval.experiment import benchmark_version  # noqa: E402
from eval.figure_match import FIGURE_MATCH_VERSION  # noqa: E402
from eval.run_eval import (  # noqa: E402
    BENCHMARK_FILE, RESULTS_DIR, attach_deterministic_metrics, build_aggregates, save_results,
)


def recompute(path: Path, labels: dict | None) -> dict:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("result_kind", "generation") != "generation":
        raise ValueError(f"{path.name} is not a generation results file")
    records = payload["results"]
    # The labels belong to one index.  A run made on another (its config records a
    # different benchmark_version) keeps the agent_* it was scored with; only the
    # figure and grounding metrics, which depend on stored text alone, are redone.
    current = benchmark_version(BENCHMARK_FILE, LABELS_FILE) if labels and LABELS_FILE.exists() else None
    same_index = bool(labels) and payload.get("config", {}).get("benchmark_version") == current
    attach_deterministic_metrics(records, labels if same_index else None, keep_agent_retrieval=not same_index)
    if payload.get("aggregates") is not None:
        payload["aggregates"] = build_aggregates(records)
    payload.setdefault("recomputed", []).append({
        "what": ("deterministic metrics (figure_*, grounding, agent_*) and aggregates" if same_index
                 else "figure_* and grounding only; agent_* kept (run predates the current labels)"),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "figure_match_version": FIGURE_MATCH_VERSION,
        "labels_file": str(LABELS_FILE.relative_to(REPO_ROOT)).replace("\\", "/") if same_index else None,
    })
    payload["config"]["figure_match_version"] = FIGURE_MATCH_VERSION
    save_results(payload, path)
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="Recompute judge-free metrics on generation results files.")
    p.add_argument("files", nargs="+", type=Path)
    args = p.parse_args()
    labels = load_labels(LABELS_FILE) if LABELS_FILE.exists() else None
    for path in args.files:
        payload = recompute(path, labels)
        agg = payload.get("aggregates")
        det = (agg or {}).get("overall", {}).get("deterministic") if agg else None
        print(f"{path.name}: {det if det else 'per-record metrics updated (no aggregates on this file)'}")
    from eval.leaderboard import write_leaderboard
    print("leaderboard:", write_leaderboard(RESULTS_DIR).relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
