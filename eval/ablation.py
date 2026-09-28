# eval/ablation.py
#
# PURPOSE
# -------
# Render every retrieval-only results file under eval/results/ as one
# Markdown matrix: one row per retrieval configuration, columns for the
# ranking metrics, the table-dependent stratum, and latency.  This is the
# table eval/EVALUATION.md reports and the README cites, generated from the
# same files the leaderboard reads so the two cannot disagree.
#
#   python -m eval.ablation            # print the matrix
#   python -m eval.ablation --sort hit@5

import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"

COLUMNS = (
    ("hit@5", "hit@5"), ("recall@5", "recall@5"), ("mrr", "MRR"), ("ndcg@5", "nDCG@5"),
    ("recall@10", "recall@10"), ("recall@25", "recall@25"),
)


def load_retrieval_runs(results_dir: Path = RESULTS_DIR) -> list[dict]:
    runs = []
    for path in sorted(results_dir.glob("retrieval-*.json")):
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("result_kind") == "retrieval" and payload.get("aggregates"):
            payload["_file"] = path.name
            runs.append(payload)
    return runs


def describe(rc: dict) -> str:
    """One short phrase for a RetrievalConfig dict."""
    parts = [rc.get("mode", "?")]
    if rc.get("mode") == "hybrid":
        extras = []
        if rc.get("rrf_k", 60) != 60:
            extras.append(f"rrf_k={rc['rrf_k']}")
        if rc.get("sparse_weight", 1.0) != 1.0 or rc.get("dense_weight", 1.0) != 1.0:
            extras.append(f"w={rc.get('dense_weight', 1.0)}/{rc.get('sparse_weight', 1.0)}")
        parts += extras
    if rc.get("rerank"):
        parts.append(f"rerank fetch={rc.get('fetch_k')}")
    elif rc.get("mode") != "dense" and rc.get("fetch_k", 25) != 25:
        parts.append(f"fetch={rc.get('fetch_k')}")
    tf = rc.get("ticker_filter")
    if tf and tf not in ("none", False):
        parts.append(f"ticker={tf}")
    return ", ".join(parts)


def _f(v) -> str:
    return "—" if v is None else f"{v:.3f}"


def render(runs: list[dict], sort_key: str | None = None) -> str:
    rows = []
    for p in runs:
        rc = p["config"].get("retrieval") or {}
        ov = p["aggregates"]["overall"]
        table = (p["aggregates"].get("by_requires_table") or {}).get("True") or {}
        lat = p["aggregates"].get("latency_ms") or {}
        rows.append({
            "label": p.get("label", p["_file"]),
            "config": describe(rc),
            **{k: ov.get(k) for k, _ in COLUMNS},
            "table_hit@5": table.get("hit@5"),
            "p50": lat.get("p50"),
            "file": p["_file"],
            "hash": p["config"].get("config_hash", ""),
        })
    if sort_key:
        rows.sort(key=lambda r: -(r.get(sort_key) or 0))
    header = "| configuration | " + " | ".join(name for _, name in COLUMNS) + " | table hit@5 | p50 ms | file |"
    sep = "|---|" + "---|" * (len(COLUMNS) + 3)
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"| {r['config']} | " + " | ".join(_f(r[k]) for k, _ in COLUMNS)
            + f" | {_f(r['table_hit@5'])} | {r['p50'] if r['p50'] is not None else '—'} | [{r['label']}]({r['file']}) |"
        )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Print the retrieval ablation matrix from eval/results/.")
    p.add_argument("--sort", default=None, help="Metric key to sort by, descending (e.g. hit@5).")
    args = p.parse_args()
    runs = load_retrieval_runs()
    if not runs:
        print("No retrieval results under", RESULTS_DIR)
        return 1
    print(render(runs, args.sort))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
