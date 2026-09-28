# eval/ci_gate.py
#
# PURPOSE
# -------
# The quality gate: numbers from the evaluation instrument compared with the
# thresholds in eval/ci_gate.json, and a non-zero exit when any is missed.
# Two modes, one per workflow:
#
#   retrieval   every pull request (.github/workflows/ci.yml).  Scores the
#               gated retrieval configurations over the CI slice
#               (eval/ci_corpus.py) with the retriever-only runner — no LLM
#               call, no secret — and fails the build below the thresholds.
#   judged      manual trigger (.github/workflows/eval-judged.yml).  Applies
#               the judged thresholds to a results file eval/run_eval.py
#               wrote for the fixed ten-item smoke subset.  About $0.20 of
#               judge calls per run, which is why it is not on every PR.
#
# THRESHOLDS ARE MEASURED, NOT ASPIRATIONAL
# -----------------------------------------
# Every retrieval threshold names the committed results file it was set from
# and sits two benchmark items (2/71 = 0.028) below that file's value; the
# gate prints the committed value next to the one it measures, so a slice
# that stopped reproducing the full index would show as a gap before it
# showed as a failure.  The judged thresholds were calibrated from the
# per-item scores of the committed judged run over the same ten items
# (`python -m eval.ci_gate calibrate` prints both columns).  A threshold set
# above its own calibration value fails `--dry-run`, so the gate cannot be
# tightened past what was ever measured.
#
# The Markdown table goes to $GITHUB_STEP_SUMMARY when it is set, so the
# numbers land on the run's summary page; the per-item results files the
# gate writes are uploaded as workflow artefacts, never committed.

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.chunk_labels import LABELS_FILE, load_labels, scorable_groups  # noqa: E402
from eval.experiment import benchmark_version, config_hash  # noqa: E402
from eval.run_eval import (  # noqa: E402
    BENCHMARK_FILE, RESULTS_DIR, _display_path, _git_commit, build_aggregates as build_generation_aggregates,
    load_benchmark, save_results,
)
from eval.run_retrieval_eval import RANKED_K, SCHEMA_VERSION, build_aggregates, evaluate_items  # noqa: E402

logger = logging.getLogger(__name__)

GATE_FILE = Path(__file__).resolve().parent / "ci_gate.json"
CI_OUT_DIR = RESULTS_DIR / "ci"
JUDGED_METRICS = ("faithfulness", "answer_relevancy", "context_recall")
LOWER_IS_BETTER = ("terminal_failures",)

EXIT_PASS, EXIT_FAIL, EXIT_CONFIG = 0, 1, 2


@dataclass
class Check:
    scope: str
    metric: str
    value: float | None
    threshold: float
    committed: float | None = None
    higher_is_better: bool = True

    @property
    def passed(self) -> bool:
        if self.value is None:
            return False
        return self.value >= self.threshold if self.higher_is_better else self.value <= self.threshold


def load_gate(path: Path = GATE_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare(scope: str, values: dict, thresholds: dict, committed: dict | None = None) -> list[Check]:
    """One Check per threshold, in the gate file's order.  A metric the run did not produce fails."""
    committed = committed or {}
    return [
        Check(scope, metric, _num(values.get(metric)), float(minimum), _num(committed.get(metric)),
              higher_is_better=metric not in LOWER_IS_BETTER)
        for metric, minimum in thresholds.items()
    ]


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _fmt(v, count: bool = False) -> str:
    if v is None:
        return "—"
    return f"{int(v)}" if count else f"{v:.3f}"


def render(title: str, checks: list[Check], notes: list[str] = ()) -> str:
    """Markdown: one row per check, PASS/FAIL in the last column, a verdict line, then the notes."""
    lines = [f"### {title}", "", "| scope | metric | committed | this run | threshold | result |", "|---|---|---|---|---|---|"]
    for c in checks:
        sign = "≥" if c.higher_is_better else "≤"
        count = c.metric in LOWER_IS_BETTER   # counts print as integers, rates to three decimals
        lines.append(f"| {c.scope} | {c.metric} | {_fmt(c.committed, count)} | {_fmt(c.value, count)} | "
                     f"{sign} {_fmt(c.threshold, count)} | {'PASS' if c.passed else 'FAIL'} |")
    failed = [c for c in checks if not c.passed]
    lines += ["", f"**{'PASS' if not failed else 'FAIL'}** — {len(checks) - len(failed)} of {len(checks)} checks passed."]
    for n in notes:
        lines.append(f"- {n}")
    return "\n".join(lines) + "\n"


def emit_summary(text: str) -> None:
    print("\n" + text)
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


# ── Retrieval gate ──────────────────────────────────────────────────────────

def committed_overall(rel_path: str | None) -> dict | None:
    """`aggregates.overall` of a committed results file, or None when it is not named or missing."""
    if not rel_path:
        return None
    path = REPO_ROOT / rel_path
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return (payload.get("aggregates") or {}).get("overall")


def _default_retriever_factory(rc_dict: dict, k: int):
    from retrieval.retriever import RetrievalConfig, Retriever

    retriever = Retriever(RetrievalConfig(**rc_dict))
    retriever.retrieve("warm-up query about total net sales", k)   # load models before anything is timed

    def retrieve_fn(question: str, k: int, ticker: str | None) -> list[str]:
        return [c.chunk_id for c in retriever.retrieve(question, k, ticker)]

    return retrieve_fn


def index_problem(labels: dict) -> str | None:
    """Why the live index cannot be scored against these labels, or None when it can."""
    from eval.ci_corpus import CORPUS_FILE, index_chunk_count, read_header
    from ingestion.embedder import CHROMA_PERSIST_DIR, COLLECTION_NAME

    live = index_chunk_count(CHROMA_PERSIST_DIR, COLLECTION_NAME)
    if not live:
        return f"no index at {CHROMA_PERSIST_DIR}: run `python -m eval.ci_corpus index` (CI) or the ingestion pipeline"
    accepted = {labels.get("index_chunk_count"): "the labelled full index"}
    if CORPUS_FILE.exists():
        header = read_header(CORPUS_FILE)
        if header.get("index_chunk_count") != labels.get("index_chunk_count"):
            return (f"the CI slice was cut from a {header.get('index_chunk_count')}-chunk index but the labels were "
                    f"made on {labels.get('index_chunk_count')}: run `python -m eval.ci_corpus build`")
        accepted[header.get("n_chunks")] = "the CI slice"
    if live not in accepted:
        return (f"the index at {CHROMA_PERSIST_DIR} has {live} chunks, which is neither "
                + " nor ".join(f"{name} ({n})" for n, name in accepted.items()))
    return None


def run_retrieval_gate(gate: dict, out_dir: Path, retriever_factory=None, rows=None, labels=None):
    """Score every gated configuration; return (passed, markdown summary, checks)."""
    section = gate["retrieval"]
    rows = rows if rows is not None else load_benchmark(BENCHMARK_FILE)
    labels = labels if labels is not None else load_labels(LABELS_FILE)
    k = int(section.get("k", RANKED_K))
    factory = retriever_factory or _default_retriever_factory
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    commit, dirty = _git_commit()

    checks: list[Check] = []
    notes: list[str] = []
    for cfg in section["configs"]:
        from retrieval.retriever import RetrievalConfig
        rc = RetrievalConfig(**cfg["retrieval"])
        retrieve_fn = factory(cfg["retrieval"], k)
        items, skipped = evaluate_items(rows, labels, retrieve_fn, k, oracle_ticker=(rc.ticker_filter == "oracle"))
        aggregates = build_aggregates(items) if items else None
        config = {
            "result_kind": "retrieval", "schema_version": SCHEMA_VERSION, "gate": "ci",
            "retrieval": rc.as_dict(), "query_source": "benchmark_question",
            "benchmark_file": str(BENCHMARK_FILE.relative_to(REPO_ROOT)).replace("\\", "/"),
            "labels_file": str(LABELS_FILE.relative_to(REPO_ROOT)).replace("\\", "/"),
            "benchmark_version": benchmark_version(BENCHMARK_FILE, LABELS_FILE) if BENCHMARK_FILE.exists() and LABELS_FILE.exists() else None,
            "item_ids": sorted(r["id"] for r in rows),
            "thresholds": cfg["thresholds"], "committed": cfg.get("committed"),
        }
        config["config_hash"] = config_hash(config)
        payload = {
            "schema_version": SCHEMA_VERSION, "result_kind": "retrieval",
            "run_id": f"retrieval-ci-{cfg['label']}-{config['config_hash']}", "label": f"ci-{cfg['label']}",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_commit": commit, "git_dirty": dirty, "config": config,
            "run_status": {"complete": bool(items), "n_requested": len(rows), "n_scored": len(items),
                           "skipped_ids": skipped, "skipped_reason": "no chunk label" if skipped else None},
            "aggregates": aggregates, "results": items,
        }
        out_path = out_dir / f"retrieval-ci-{cfg['label']}.json"
        save_results(payload, out_path)
        overall = (aggregates or {}).get("overall") or {}
        checks += compare(cfg["label"], overall, cfg["thresholds"], committed_overall(cfg.get("committed")))
        lat = (aggregates or {}).get("latency_ms") or {}
        notes.append(f"`{cfg['label']}` = {rc.as_dict()}; {len(items)} items scored, "
                     f"p50 {lat.get('p50')} ms per query; evidence `{_display_path(out_path)}`")
    passed = all(c.passed for c in checks)
    return passed, render("Retrieval quality gate", checks, notes), checks


def validate_gate(gate: dict, rows=None, labels=None) -> list[str]:
    """Problems with the gate file itself: unknown items, missing calibration files, thresholds above calibration."""
    problems: list[str] = []
    rows = rows if rows is not None else load_benchmark(BENCHMARK_FILE)
    labels = labels if labels is not None else load_labels(LABELS_FILE)
    ids = {r["id"] for r in rows}
    unlabelled = [r["id"] for r in rows if not scorable_groups(labels, r["id"])]
    if unlabelled:
        problems.append(f"{len(unlabelled)} benchmark item(s) have no chunk label: {', '.join(unlabelled[:8])}")
    for cfg in gate["retrieval"]["configs"]:
        committed = committed_overall(cfg.get("committed"))
        if committed is None:
            problems.append(f"retrieval/{cfg['label']}: committed results file {cfg.get('committed')!r} not found")
            continue
        for metric, minimum in cfg["thresholds"].items():
            if committed.get(metric) is None:
                problems.append(f"retrieval/{cfg['label']}: committed file has no metric {metric!r}")
            elif float(minimum) > committed[metric]:
                problems.append(f"retrieval/{cfg['label']}: threshold {metric} {minimum} is above the committed value {committed[metric]}")
    judged = gate.get("judged") or {}
    unknown = sorted(set(judged.get("item_ids", [])) - ids)
    if unknown:
        problems.append(f"judged: item ids not in the benchmark: {', '.join(unknown)}")
    cal_path = REPO_ROOT / judged.get("calibrated_from", "")
    if not judged.get("calibrated_from") or not cal_path.exists():
        problems.append(f"judged: calibration file {judged.get('calibrated_from')!r} not found")
    else:
        values = calibration_values(gate)
        for metric, minimum in judged.get("thresholds", {}).items():
            v = values.get(metric)
            if v is None:
                problems.append(f"judged: calibration run has no value for {metric!r}")
            elif float(minimum) > v:
                problems.append(f"judged: threshold {metric} {minimum} is above the calibration value {v}")
        if values.get("terminal_failures", 0) > judged.get("max_terminal_failures", 0):
            problems.append("judged: the calibration run itself exceeds max_terminal_failures")
    return problems


# ── Judged gate ─────────────────────────────────────────────────────────────

def subset_payload(payload: dict, item_ids: list[str]) -> dict:
    """The same run restricted to *item_ids*, aggregates rebuilt — what the smoke subset would have scored."""
    wanted = set(item_ids)
    rows = [r for r in payload.get("results") or [] if r.get("id") in wanted]
    config = dict(payload.get("config") or {})
    config["benchmark_item_ids"] = sorted(r["id"] for r in rows)
    config["benchmark_n"] = len(rows)
    return {
        **{k: v for k, v in payload.items() if k not in ("results", "aggregates", "config", "run_status")},
        "config": config,
        "run_status": {**(payload.get("run_status") or {}), "complete": len(rows) == len(wanted),
                       "n_requested": len(wanted), "n_generated": len(rows), "n_scored": len(rows)},
        "aggregates": build_generation_aggregates(rows) if rows and len(rows) == len(wanted) else None,
        "results": rows,
    }


def judged_values(payload: dict) -> dict:
    """The numbers the judged thresholds apply to, from a complete results file."""
    overall = (payload.get("aggregates") or {}).get("overall") or {}
    values = {m: (overall.get(m) or {}).get("mean_failures_as_zero") for m in JUDGED_METRICS}
    values["figure_primary_rate"] = (overall.get("deterministic") or {}).get("figure_primary_rate")
    values["terminal_failures"] = sum(1 for r in payload.get("results") or [] if r.get("terminal_failure"))
    return values


def judged_mismatches(payload: dict, section: dict) -> list[str]:
    """Ways the run differs from what the thresholds were calibrated for.  Any one of them fails the gate."""
    cfg = payload.get("config") or {}
    expect = section.get("expect") or {}
    problems: list[str] = []
    if sorted(cfg.get("benchmark_item_ids") or []) != sorted(section["item_ids"]):
        problems.append(f"items differ from the gate's subset: got {cfg.get('benchmark_n')} item(s)")
    if expect.get("agent_model") and cfg.get("agent_model") != expect["agent_model"]:
        problems.append(f"agent_model is {cfg.get('agent_model')!r}, calibrated on {expect['agent_model']!r}")
    for key, val in (expect.get("retrieval") or {}).items():
        if (cfg.get("retrieval") or {}).get(key) != val:
            problems.append(f"retrieval.{key} is {(cfg.get('retrieval') or {}).get(key)!r}, calibrated on {val!r}")
    return problems


def run_judged_gate(gate: dict, payload: dict):
    """Apply the judged thresholds to *payload*; return (passed, markdown summary, checks)."""
    section = gate["judged"]
    notes: list[str] = []
    status = payload.get("run_status") or {}
    cfg = payload.get("config") or {}
    diag = payload.get("judge_diagnostics") or {}
    notes.append(f"agent `{cfg.get('agent_model')}`, judge `{cfg.get('judge_provider')}/{cfg.get('judge_model')}`, "
                 f"{diag.get('judge_calls', '?')} judge calls"
                 + (f", judge cost ${diag['judge_cost_usd']:.2f}" if isinstance(diag.get("judge_cost_usd"), (int, float)) else ""))
    expect_judge = (section.get("expect") or {}).get("judge_model")
    if expect_judge and cfg.get("judge_model") != expect_judge:
        notes.append(f"WARNING: thresholds were calibrated with judge `{expect_judge}`; scores from another judge are "
                     "not comparable to the third decimal (eval/EVALUATION.md finding 4)")
    if not status.get("complete"):
        notes.append(f"run is not complete: {payload.get('aggregates_withheld_reason') or status}")
        checks = compare("judged", {}, section["thresholds"])
        return False, render("Judged smoke gate", checks, notes), checks
    mismatches = judged_mismatches(payload, section)
    for m in mismatches:
        notes.append(f"configuration mismatch: {m}")
    values = judged_values(payload)
    checks = compare("judged", values, section["thresholds"])
    checks.append(Check("judged", "terminal_failures", float(values["terminal_failures"]),
                        float(section.get("max_terminal_failures", 0)), higher_is_better=False))
    passed = all(c.passed for c in checks) and not mismatches
    return passed, render("Judged smoke gate", checks, notes), checks


def calibration_values(gate: dict) -> dict:
    """What the committed judged run scored on the gate's subset — the numbers the thresholds sit under."""
    section = gate["judged"]
    with open(REPO_ROOT / section["calibrated_from"], encoding="utf-8") as f:
        payload = json.load(f)
    return judged_values(subset_payload(payload, section["item_ids"]))


# ── CLI ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare evaluation numbers with the thresholds in eval/ci_gate.json.")
    p.add_argument("--gate", type=Path, default=GATE_FILE)
    sub = p.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("retrieval", help="Score the gated retrieval configurations on the live index (no LLM).")
    r.add_argument("--out-dir", type=Path, default=CI_OUT_DIR)
    r.add_argument("--dry-run", action="store_true", help="Validate the gate file against the committed results; no index, no model.")
    j = sub.add_parser("judged", help="Apply the judged thresholds to a results file from eval.run_eval.")
    j.add_argument("--results", type=Path, required=True)
    sub.add_parser("judged-ids", help="Print the smoke subset as a comma-separated list for `run_eval --ids`.")
    sub.add_parser("calibrate", help="Print what the calibration run scored on the subset, next to the thresholds.")
    return p


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = build_parser().parse_args()
    gate = load_gate(args.gate)

    if args.mode == "judged-ids":
        print(",".join(gate["judged"]["item_ids"]))
        return EXIT_PASS

    if args.mode == "calibrate":
        values = calibration_values(gate)
        thresholds = gate["judged"]["thresholds"]
        print(f"calibration run: {gate['judged']['calibrated_from']} restricted to {len(gate['judged']['item_ids'])} items")
        for metric, v in values.items():
            limit = thresholds.get(metric, gate["judged"].get("max_terminal_failures") if metric == "terminal_failures" else None)
            count = metric in LOWER_IS_BETTER
            print(f"  {metric:<22} {_fmt(v, count):>8}   threshold {_fmt(limit, count)}")
        return EXIT_PASS

    if args.mode == "retrieval":
        problems = validate_gate(gate)
        if problems:
            for pr in problems:
                print(f"GATE FILE PROBLEM: {pr}")
            return EXIT_CONFIG
        if args.dry_run:
            for cfg in gate["retrieval"]["configs"]:
                committed = committed_overall(cfg.get("committed")) or {}
                print(f"[DRY RUN] {cfg['label']}: " + ", ".join(
                    f"{m} >= {t} (committed {committed.get(m)})" for m, t in cfg["thresholds"].items()))
            print(f"[DRY RUN] judged subset: {len(gate['judged']['item_ids'])} items; thresholds {gate['judged']['thresholds']}")
            return EXIT_PASS
        labels = load_labels(LABELS_FILE)
        problem = index_problem(labels)
        if problem:
            print(f"INDEX PROBLEM: {problem}")
            return EXIT_CONFIG
        passed, summary, _ = run_retrieval_gate(gate, args.out_dir, labels=labels)
        emit_summary(summary)
        return EXIT_PASS if passed else EXIT_FAIL

    # judged
    with open(args.results, encoding="utf-8") as f:
        payload = json.load(f)
    passed, summary, _ = run_judged_gate(gate, payload)
    emit_summary(summary)
    return EXIT_PASS if passed else EXIT_FAIL


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
