# eval/leaderboard.py
#
# PURPOSE
# -------
# Regenerate eval/results/LEADERBOARD.md from every results file under
# eval/results/.  Each run is one row, keyed by its config hash, so the table
# is the complete record of what has been measured — and, because the
# generation and retrieval runners both call write_leaderboard() as their last
# step, it cannot drift from the files it summarises.
#
# Three tables, kept apart on purpose:
#   * generation runs, schema 3  — per-chunk contexts, judge + deterministic metrics
#   * retrieval runs             — retriever alone, no LLM
#   * legacy generation runs     — schema 2, contexts scored as observation blobs.
#     Listed for the record, never mixed into the schema-3 table: their
#     context_recall means something different (see eval/EVALUATION.md).
# Incomplete runs are listed last with their reason, so a partial run is
# visible but can never sit in a comparison table.

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
LEADERBOARD_FILE = RESULTS_DIR / "LEADERBOARD.md"


def load_results(results_dir: Path) -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and "run_status" in payload:
            out.append((path, payload))
    return out


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _date(payload: dict) -> str:
    return (payload.get("timestamp") or "")[:10]


def _kind(payload: dict) -> str:
    if payload.get("result_kind") == "retrieval":
        return "retrieval"
    if int(payload.get("schema_version") or 0) >= 3:
        return "generation"
    return "legacy"


def _complete(payload: dict) -> bool:
    return bool((payload.get("run_status") or {}).get("complete")) and payload.get("aggregates") is not None


def _gen_row(path: Path, p: dict) -> str:
    cfg = p["config"]
    ov = p["aggregates"]["overall"]
    det = ov.get("deterministic") or {}

    def m(name):
        return _fmt((ov.get(name) or {}).get("mean_failures_as_zero"))

    return "| " + " | ".join([
        p.get("run_id", path.stem), _date(p), cfg.get("agent_model", "—"),
        f"{cfg.get('judge_provider', '')}/{cfg.get('judge_model', '')}", cfg.get("context_format", "chunk"),
        str(ov.get("n_items", "—")),
        m("faithfulness"), m("answer_relevancy"), m("context_recall"),
        _fmt(det.get("figure_exact_rate")), _fmt(det.get("agent_hit_rate")),
        f"`{cfg.get('config_hash', '—')}`", f"[{path.name}]({path.name})",
    ]) + " |"


def _legacy_row(path: Path, p: dict) -> str:
    cfg = p["config"]
    ov = p["aggregates"]["overall"]

    def m(name):
        return _fmt((ov.get(name) or {}).get("mean_failures_as_zero"))

    return "| " + " | ".join([
        p.get("run_id", path.stem), _date(p), cfg.get("agent_model", "—"),
        f"{cfg.get('judge_provider', '')}/{cfg.get('judge_model', '')}", str(ov.get("n_items", "—")),
        m("faithfulness"), m("answer_relevancy"), m("context_recall"), f"[{path.name}]({path.name})",
    ]) + " |"


def _ret_row(path: Path, p: dict) -> str:
    cfg = p["config"]
    rc = cfg.get("retrieval") or {}
    ov = p["aggregates"]["overall"]
    lat = (p["aggregates"].get("latency_ms") or {})
    return "| " + " | ".join([
        p.get("run_id", path.stem), _date(p), rc.get("mode", "—"), str(rc.get("k", "—")),
        _fmt(rc.get("ticker_filter")), str(ov.get("n_items", "—")),
        _fmt(ov.get("hit@5")), _fmt(ov.get("recall@5")), _fmt(ov.get("mrr")), _fmt(ov.get("ndcg@5")),
        _fmt(ov.get("recall@25")), ("—" if lat.get("p50") is None else f"{lat['p50']:.1f}"),
        f"`{cfg.get('config_hash', '—')}`", f"[{path.name}]({path.name})",
    ]) + " |"


def render(results: list[tuple[Path, dict]]) -> str:
    gen = [(p, d) for p, d in results if _kind(d) == "generation" and _complete(d)]
    ret = [(p, d) for p, d in results if _kind(d) == "retrieval" and _complete(d)]
    legacy = [(p, d) for p, d in results if _kind(d) == "legacy" and _complete(d)]
    partial = [(p, d) for p, d in results if not _complete(d)]

    lines = [
        "# Leaderboard",
        "",
        f"Regenerated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python -m eval.leaderboard` "
        f"from every results file in this directory. Do not edit by hand.",
        "",
        "Rows are comparable only within a table and only at the same benchmark version. "
        "Judge-scored means count a terminal failure (empty answer, recursion limit) as 0.",
        "",
        "## Generation runs (schema 3: per-chunk contexts)",
        "",
        "| run | date | agent | judge | contexts | n | faithfulness | answer_rel | context_recall | figure_exact | agent_hit | config | file |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_gen_row(p, d) for p, d in sorted(gen, key=lambda x: _date(x[1]), reverse=True)] or ["| _none yet_ | | | | | | | | | | | | |"]
    lines += [
        "",
        "`figure_exact`: share of items whose answer contains every ground-truth figure (deterministic, no judge). "
        "`agent_hit`: share of labelled items where any relevant chunk appeared in the agent's tool observations.",
        "",
        "## Retrieval runs (retriever alone, no LLM)",
        "",
        "| run | date | mode | k | ticker_filter | n | hit@5 | recall@5 | mrr | ndcg@5 | recall@25 | p50 ms | config | file |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_ret_row(p, d) for p, d in sorted(ret, key=lambda x: _date(x[1]), reverse=True)] or ["| _none yet_ | | | | | | | | | | | | | |"]
    lines += [
        "",
        "## Legacy generation runs (schema 2: contexts scored as observation blobs)",
        "",
        "Kept for the record. `context_recall` here scored each tool observation as one blob of k passages, "
        "so it is not comparable with the schema-3 table above.",
        "",
        "| run | date | agent | judge | n | faithfulness | answer_rel | context_recall | file |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_legacy_row(p, d) for p, d in sorted(legacy, key=lambda x: _date(x[1]), reverse=True)] or ["| _none_ | | | | | | | | |"]
    if partial:
        lines += ["", "## Incomplete runs (no aggregates)", ""]
        for p, d in partial:
            reason = d.get("aggregates_withheld_reason") or (d.get("run_status") or {}).get("stop_reason") or "incomplete"
            lines.append(f"- `{p.name}` — {reason}")
    lines.append("")
    return "\n".join(lines)


def write_leaderboard(results_dir: Path = RESULTS_DIR, out: Path | None = None) -> Path:
    out = out or (results_dir / LEADERBOARD_FILE.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(load_results(results_dir)))
    return out


if __name__ == "__main__":
    print(write_leaderboard())
    sys.exit(0)
