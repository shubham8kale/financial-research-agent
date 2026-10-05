# eval/tool_metrics.py
#
# PURPOSE
# -------
# Tool-call quality, measured from what a results file already stores: the
# meter's record of every tool call (agent/meter.py) and the labels in
# eval/benchmark_tools.json.  No model call, no network, no new dependency:
#
#   python -m eval.tool_metrics eval/results/<run>.json [--ids qa_0060,qa_0061] [--baseline <run>.json]
#
# and the output lands in eval/tool_metrics/, not in eval/results/ (the
# leaderboard and the CI gate read that directory and know nothing of this).
#
# WHAT IS MEASURED
# ----------------
# Every metric is a count over a stated denominator, overall and by
# question_type, never a bare rate.
#
#   call_validity     calls that did not fail, over all calls, split by tool.  A
#                     call fails when the framework rejected it (meter `error`)
#                     or when a tolerant tool answered it with the argument-error
#                     marker (agent/observations.py): the marker is a returned
#                     string, so the meter alone would score such a call as fine.
#   first_tool_ok     the first call's tool is in the item's `first_tool_ok`.  The
#                     first call is the earliest start (`t0_ms`) when the file has
#                     it; on an older file it is the first RECORDED call, which is
#                     completion order, and the output says so.
#   tool_set_ok       the distinct tools are all in `allowed_tools` and cover
#                     `required_tools`.  allowed_only_ok drops the second half so a
#                     reader can see the label set that is widest in doubt.  Both
#                     measure conformity to the system prompt's tool rules, not
#                     whether the answer was right (figure_primary does that).
#   batched           calls issued in a model step that made two or more.  With
#                     `t0_ms`, calls are one step when their [start, start + ms]
#                     windows overlap (each end padded by OVERLAP_SLACK_MS, because a
#                     2 ms lookup can finish before its sibling's worker thread has
#                     started).  Without it: "tool calls > model calls - 1", per
#                     item, marked method=heuristic.
#   redundant_calls   the same tool with identical arguments again (files with
#                     arguments only).
#   arg_validity      lookup_financial_fact only, files with arguments only: the
#                     ticker is one of the item's, the fiscal year is the labelled
#                     one when the question names one, and the concept resolves to
#                     at least one row through retrieval/facts.py.
#
# The labels were written blind (before any results file was opened) and are
# not part of benchmark_version; this module records their sha256 instead.

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.observations import argument_error_tool  # noqa: E402
from eval.experiment import file_sha256  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
LABELS_FILE = EVAL_DIR / "benchmark_tools.json"
OUT_DIR = EVAL_DIR / "tool_metrics"
TOOL_METRICS_VERSION = "1"
FACT_TOOL = "lookup_financial_fact"
# Padding on a call's end when deciding whether the next call started in the same model step.  A step's calls start
# within a few milliseconds of each other (worker-thread start-up); the next step starts after a model call, hundreds
# of milliseconds later at the least.  50 ms sits well inside that gap.
OVERLAP_SLACK_MS = 50.0


# ── small helpers ──────────────────────────────────────────────────────────────

def ratio(n: int, of: int) -> dict:
    """A count with its denominator: {"n": 124, "of": 131, "rate": 0.9466}."""
    return {"n": n, "of": of, "rate": round(n / of, 4) if of else None}


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile exactly as eval/run_eval.py computes it, so a figure here equals the harness's."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def percentile_linear(values: list[float], pct: float) -> float | None:
    """Percentile by linear interpolation between the closest ranks (the numpy default), rounded to 0.1 ms.

    The harness's own percentile is nearest-rank (above), the figure the docs quote for p50 and p95.  The
    interpolated one is what the upgrade prompt's baseline (contract-v3 over the 70 answered items: p50 3,690 ms,
    p95 8,972 ms) was computed with, so the latency blocks carry both and say which is which.
    """
    if not values:
        return None
    ordered = sorted(values)
    pos = pct / 100 * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo), 1)


def load_labels(path: Path = LABELS_FILE) -> tuple[dict, str]:
    """(items by benchmark id, sha256 of the file read as LF)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["items"], file_sha256(path)


def calls_of(record: dict) -> list[dict] | None:
    """The meter's tool calls for one stored record, or None when the record has no meter record of them."""
    calls = (record.get("meter") or {}).get("tool_calls")
    return list(calls) if isinstance(calls, list) else None


def has_tool_calls(payload: dict) -> bool:
    """True when at least one record of a results file carries meter.tool_calls (older files may not)."""
    return any(calls_of(r) is not None for r in payload.get("results") or [])


def agent_llm_calls(record: dict) -> int | None:
    """Model calls the agent made, without the output contract's structuring calls (one per verification attempt)."""
    total = record.get("llm_calls")
    if total is None:
        total = (record.get("meter") or {}).get("llm_calls")
    if total is None:
        return None
    verification = record.get("verification") or {}
    if verification.get("status") not in (None, "skipped"):
        total -= int(verification.get("attempts") or 0)
    return int(total)


# ── steps and batching ─────────────────────────────────────────────────────────

def step_groups(calls: list[dict], slack_ms: float = OVERLAP_SLACK_MS) -> list[list[dict]] | None:
    """Calls grouped into model steps by overlapping windows; None when any call lacks a start offset (older files)."""
    if not calls or any("t0_ms" not in c for c in calls):
        return None
    ordered = sorted(calls, key=lambda c: c["t0_ms"])
    groups = [[ordered[0]]]
    end = ordered[0]["t0_ms"] + ordered[0].get("ms", 0.0)
    for c in ordered[1:]:
        if c["t0_ms"] <= end + slack_ms:
            groups[-1].append(c)
            end = max(end, c["t0_ms"] + c.get("ms", 0.0))
        else:
            groups.append([c])
            end = c["t0_ms"] + c.get("ms", 0.0)
    return groups


def batching_of(record: dict, calls: list[dict]) -> dict:
    """How one item's calls were issued: by window overlap when starts were recorded, else the per-item heuristic."""
    n = len(calls)
    groups = step_groups(calls)
    if groups is not None:
        batched_groups = [g for g in groups if len(g) > 1]
        return {"method": "t0", "n_calls": n, "steps": len(groups), "batched_steps": len(batched_groups),
                "calls_in_batched_steps": sum(len(g) for g in batched_groups), "batched": bool(batched_groups),
                "step_sizes": [len(g) for g in groups]}
    llm = agent_llm_calls(record)
    steps = None if llm is None else max(llm - 1, 0)       # the last model call is the answer, not a tool step
    return {"method": "heuristic", "n_calls": n, "steps": steps, "batched_steps": None,
            "calls_in_batched_steps": None, "batched": bool(steps is not None and n > steps),
            "calls_beyond_one_per_step": None if steps is None else max(n - steps, 0)}


# ── per-call and per-item ──────────────────────────────────────────────────────

def _marker_failures(record: dict) -> Counter:
    """Observations a tolerant tool answered with the argument-error marker, by the tool named in the marker."""
    out: Counter = Counter()
    for obs in record.get("observations") or []:
        tool = argument_error_tool(obs) if isinstance(obs, str) else None
        if tool:
            out[tool] += 1
    return out


def _canonical_args(args: dict) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def first_call(calls: list[dict]) -> tuple[dict | None, str]:
    """(the first call, how it was found): earliest start when every call has one, else first recorded (completion order)."""
    if not calls:
        return None, "none"
    if all("t0_ms" in c for c in calls):
        return min(calls, key=lambda c: c["t0_ms"]), "t0"
    return calls[0], "completion"


def _year_of(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}", value.strip()):
        return int(value.strip())
    return None


def _years_ok(label: dict) -> set[int]:
    years = set(label.get("fiscal_years_ok") or [])
    if label.get("fiscal_year") is not None:
        years.add(label["fiscal_year"])
    return years


def lookup_arg_checks(calls: list[dict], label: dict, store=None) -> list[dict]:
    """One arg_validity row per lookup_financial_fact call that carries arguments.

    *store* is a retrieval.facts.FactStore (or anything with ``lookup``); None
    skips the concept check.  The check is the repository's own resolver, no model.
    """
    rows = []
    tickers = {t.upper() for t in label.get("tickers") or []}
    years = _years_ok(label)
    for c in calls:
        if c.get("name") != FACT_TOOL or "args" not in c:
            continue
        args = c["args"] or {}
        concept = str(args.get("concept") or "").strip()
        ticker = str(args.get("ticker") or "").strip().upper()
        arg_year = args.get("fiscal_year")
        row = {"ticker_ok": ticker in tickers, "concept_present": bool(concept), "concept_resolved": None,
               "concept_returns_rows": None, "fiscal_year": None}
        if years:
            y = _year_of(arg_year)
            row["fiscal_year"] = "omitted" if arg_year is None else ("exact" if y in years else "wrong")
        if store is not None and concept and ticker:
            try:
                rows_found, info = store.lookup(ticker, concept, _year_of(arg_year), args.get("segment") or None)
                row["concept_returns_rows"] = len(rows_found) >= 1
                row["concept_resolved"] = bool(info.get("concepts"))
            except Exception:  # noqa: BLE001 — an unknown ticker or a malformed argument is an unresolved concept, not a crash
                row["concept_returns_rows"] = row["concept_resolved"] = False
        rows.append(row)
    return rows


def item_metrics(record: dict, label: dict | None, store=None) -> dict | None:
    """Every per-item quantity the aggregates are built from; None when the record has no tool-call record."""
    calls = calls_of(record)
    if calls is None:
        return None
    markers = _marker_failures(record)
    by_tool: dict[str, dict] = {}
    for c in calls:
        slot = by_tool.setdefault(c["name"], {"n": 0, "failed": 0})
        slot["n"] += 1
        slot["failed"] += 1 if c.get("error") else 0
    for tool, n in markers.items():            # a tolerant tool's marker is a returned string, so the meter scored it fine
        slot = by_tool.setdefault(tool, {"n": 0, "failed": 0})
        slot["failed"] = min(slot["failed"] + n, slot["n"])
    tools = {t: s["n"] for t, s in by_tool.items()}
    first, first_how = first_call(calls)
    has_args = any("args" in c for c in calls)
    seen: Counter = Counter()
    for c in calls:
        if "args" in c:
            seen[(c["name"], _canonical_args(c["args"]))] += 1
    verification = record.get("verification") or {}
    figure = record.get("figure") or {}
    llm = agent_llm_calls(record)
    steps_by_model_calls = None if llm is None else max(llm - 1, 0)      # the last model call is the answer, not a tool step
    out = {
        "id": record.get("id"), "question_type": record.get("question_type"), "n_calls": len(calls),
        "tools": tools, "by_tool": by_tool, "failed_calls": sum(s["failed"] for s in by_tool.values()),
        "marker_failures": dict(markers),
        "first_tool": first["name"] if first else None, "first_tool_method": first_how,
        "batching": batching_of(record, calls),
        "steps_by_model_calls": steps_by_model_calls,
        "more_calls_than_steps": steps_by_model_calls is not None and len(calls) > steps_by_model_calls,
        "redundant_calls": (sum(n - 1 for n in seen.values()) if has_args else None),
        "n_calls_with_args": sum(1 for c in calls if "args" in c),
        "arg_checks": lookup_arg_checks(calls, label, store) if (label and has_args) else [],
        "llm_calls": record.get("llm_calls"), "agent_llm_calls": agent_llm_calls(record),
        "latency_ms": record.get("latency_ms"), "cost_usd": record.get("cost_usd"),
        "terminal_failure": record.get("terminal_failure"), "recursion_limit_hit": bool(record.get("recursion_limit_hit")),
        "verification_status": verification.get("status"),
        "figure_applicable": bool(figure.get("applicable")), "figure_primary": figure.get("figure_primary"),
        "first_tool_ok": None, "tool_set_ok": None, "allowed_only_ok": None, "disallowed": None, "missing_required": None,
    }
    if label:
        allowed, required = set(label["allowed_tools"]), set(label["required_tools"])
        used = set(tools)
        out["disallowed"] = sorted(used - allowed)
        out["missing_required"] = sorted(required - used)
        out["allowed_only_ok"] = not out["disallowed"]
        out["tool_set_ok"] = out["allowed_only_ok"] and not out["missing_required"]
        if first:
            out["first_tool_ok"] = first["name"] in set(label["first_tool_ok"])
    return out


# ── aggregation ────────────────────────────────────────────────────────────────

def latency_block(items: list[dict], exclude_terminal: bool) -> dict:
    """Latency over metered items.

    The harness keeps a terminal-failure item in its latency figures (so does the documentation); with
    exclude_terminal it is left out, which is how the upgrade's baseline quotes it.  p50_ms and p95_ms are the
    harness's nearest-rank figures, the *_linear ones are interpolated.
    """
    rows = [i for i in items if i.get("latency_ms") is not None and not (exclude_terminal and i.get("terminal_failure"))]
    lat = [float(i["latency_ms"]) for i in rows]
    return {"n": len(lat), "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95),
            "mean_ms": round(sum(lat) / len(lat), 1) if lat else None,
            "p50_ms_linear": percentile_linear(lat, 50), "p95_ms_linear": percentile_linear(lat, 95)}


def summarize(items: list[dict]) -> dict:
    """The aggregate block for a set of per-item records (overall, or one question_type)."""
    n_calls = sum(i["n_calls"] for i in items)
    failed = sum(i["failed_calls"] for i in items)
    tool_n: Counter = Counter()
    tool_failed: Counter = Counter()
    for i in items:
        for t, s in i["by_tool"].items():
            tool_n[t] += s["n"]
            tool_failed[t] += s["failed"]
    with_first = [i for i in items if i["first_tool_ok"] is not None]
    labelled = [i for i in items if i["tool_set_ok"] is not None]
    batch_t0 = [i for i in items if i["batching"]["method"] == "t0"]
    batch_heur = [i for i in items if i["batching"]["method"] == "heuristic"]
    batching: dict = {"method": "t0" if batch_t0 and not batch_heur else ("heuristic" if batch_heur and not batch_t0 else "mixed")}
    if batch_t0:
        batching["steps"] = sum(i["batching"]["steps"] for i in batch_t0)
        batching["batched_steps"] = sum(i["batching"]["batched_steps"] for i in batch_t0)
        batching["calls_in_batched_steps"] = ratio(sum(i["batching"]["calls_in_batched_steps"] for i in batch_t0),
                                                   sum(i["n_calls"] for i in batch_t0))
        batching["items_with_a_batched_step"] = ratio(sum(1 for i in batch_t0 if i["batching"]["batched"]), len(batch_t0))
    if batch_heur:
        batching["heuristic_items_with_more_calls_than_steps"] = ratio(sum(1 for i in batch_heur if i["batching"]["batched"]),
                                                                       len(batch_heur))
        batching["heuristic_item_ids"] = [i["id"] for i in batch_heur if i["batching"]["batched"]]
    with_args = [i for i in items if i["redundant_calls"] is not None]
    checks = [row for i in items for row in i["arg_checks"]]
    arg_validity = None
    if checks:
        applicable = [r for r in checks if r["fiscal_year"] is not None]
        concept_rows = [r for r in checks if r["concept_returns_rows"] is not None]
        arg_validity = {
            "lookup_calls": len(checks),
            "ticker_ok": ratio(sum(r["ticker_ok"] for r in checks), len(checks)),
            "concept_present": ratio(sum(r["concept_present"] for r in checks), len(checks)),
            "concept_resolved": ratio(sum(1 for r in concept_rows if r["concept_resolved"]), len(concept_rows)),
            "concept_returns_rows": ratio(sum(1 for r in concept_rows if r["concept_returns_rows"]), len(concept_rows)),
            "fiscal_year_exact": ratio(sum(1 for r in applicable if r["fiscal_year"] == "exact"), len(applicable)),
            "fiscal_year_omitted": ratio(sum(1 for r in applicable if r["fiscal_year"] == "omitted"), len(applicable)),
            "fiscal_year_wrong": ratio(sum(1 for r in applicable if r["fiscal_year"] == "wrong"), len(applicable)),
        }
    return {
        "n_items": len(items),
        "tool_calls": n_calls,
        "calls_per_item": dict(sorted(Counter(i["n_calls"] for i in items).items())),
        "tools": dict(sorted(tool_n.items())),
        "call_validity": {"overall": ratio(n_calls - failed, n_calls),
                          "by_tool": {t: ratio(tool_n[t] - tool_failed[t], tool_n[t]) for t in sorted(tool_n)},
                          "failed_by_tool": {t: tool_failed[t] for t in sorted(tool_failed) if tool_failed[t]},
                          "items_with_a_failed_call": ratio(sum(1 for i in items if i["failed_calls"]), len(items))},
        "first_tool_ok": ratio(sum(1 for i in with_first if i["first_tool_ok"]), len(with_first)),
        "tool_set_ok": ratio(sum(1 for i in labelled if i["tool_set_ok"]), len(labelled)),
        "allowed_only_ok": ratio(sum(1 for i in labelled if i["allowed_only_ok"]), len(labelled)),
        "batched": batching,
        "more_calls_than_steps": {**ratio(sum(1 for i in items if i["more_calls_than_steps"]), len(items)),
                                  "ids": [i["id"] for i in items if i["more_calls_than_steps"]]},
        "redundant_calls": ({"items_measured": len(with_args),
                             "redundant_calls": sum(i["redundant_calls"] for i in with_args),
                             "items_with_one": sum(1 for i in with_args if i["redundant_calls"])} if with_args else None),
        "arg_validity": arg_validity,
        "model_calls": {"agent_mean": _mean([i["agent_llm_calls"] for i in items]),
                        "total_mean": _mean([i["llm_calls"] for i in items])},
        "terminal_failures": {"n": sum(1 for i in items if i["terminal_failure"]),
                              "recursion_limit": sum(1 for i in items if i["recursion_limit_hit"])},
        "latency_excluding_terminal_failures": latency_block(items, True),
        "latency_including_terminal_failures": latency_block(items, False),
        "cost_usd": {"mean": _mean([i["cost_usd"] for i in items], 6),
                     "total": round(sum(i["cost_usd"] for i in items if i["cost_usd"] is not None), 4)},
        "figure_primary": ratio(sum(1 for i in items if i["figure_applicable"] and i["figure_primary"]),
                                sum(1 for i in items if i["figure_applicable"])),
        "verification": dict(sorted(Counter(i["verification_status"] for i in items if i["verification_status"]).items())),
    }


def _mean(values: list, digits: int = 2) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), digits) if vals else None


def compute(payload: dict, labels: dict, ids: list[str] | None = None, store=None) -> dict:
    """The full metric block for one results payload: overall, by question_type, and one row per item."""
    wanted = set(ids) if ids else None
    items, skipped = [], []
    for record in payload.get("results") or []:
        if wanted is not None and record.get("id") not in wanted:
            continue
        m = item_metrics(record, labels.get(record.get("id")), store)
        (items if m is not None else skipped).append(m if m is not None else record.get("id"))
    by_type: dict[str, list[dict]] = {}
    for i in items:
        by_type.setdefault(i["question_type"] or "", []).append(i)
    return {
        "overall": summarize(items) if items else None,
        "by_question_type": {q: summarize(v) for q, v in sorted(by_type.items())},
        "items": items,
        "ids_without_a_tool_call_record": skipped,
        "unlabelled_ids": sorted(i["id"] for i in items if i["tool_set_ok"] is None),
    }


# ── comparison with a baseline run ────────────────────────────────────────────

def _stratum(items: list[dict]) -> dict:
    return {"n": len(items), "agent_llm_calls_mean": _mean([i["agent_llm_calls"] for i in items]),
            "latency_excluding_terminal_failures": latency_block(items, True),
            "latency_including_terminal_failures": latency_block(items, False),
            "tool_calls": sum(i["n_calls"] for i in items),
            "items_with_more_calls_than_steps": sum(1 for i in items if i["more_calls_than_steps"])}


def compare(current: dict, baseline: dict) -> dict:
    """Side-by-side figures for two metric blocks computed over the same ids (the keys the upgrade's gates read)."""
    def side(block):
        items = block["items"]
        s = summarize(items)
        by_type = {}
        for i in items:
            by_type.setdefault(i["question_type"] or "", []).append(i)
        return {
            "n_items": s["n_items"], "tool_calls": s["tool_calls"], "calls_per_item": s["calls_per_item"],
            "call_validity": s["call_validity"]["overall"], "failed_by_tool": s["call_validity"]["failed_by_tool"],
            "first_tool_ok": s["first_tool_ok"], "tool_set_ok": s["tool_set_ok"],
            "batched": s["batched"], "terminal_failures": s["terminal_failures"], "figure_primary": s["figure_primary"],
            "verification": s["verification"], "cost_usd": s["cost_usd"], "model_calls": s["model_calls"],
            "latency_excluding_terminal_failures": s["latency_excluding_terminal_failures"],
            "latency_including_terminal_failures": s["latency_including_terminal_failures"],
            "items_with_more_calls_than_steps": s["more_calls_than_steps"],
            "by_question_type": {q: _stratum(v) for q, v in sorted(by_type.items())},
        }
    return {"current": side(current), "baseline": side(baseline)}


# ── command line ─────────────────────────────────────────────────────────────────

def _line(label: str, r: dict | None) -> str:
    if not r:
        return f"  {label:<34} n/a"
    pct = "" if r.get("rate") is None else f"  ({r['rate'] * 100:.1f}%)"
    return f"  {label:<34} {r['n']} of {r['of']}{pct}"


def format_report(out: dict) -> str:
    ov = out["metrics"]["overall"]
    if not ov:
        return "no items with a tool-call record"
    lines = [f"tool metrics v{out['tool_metrics_version']}  source {out['source']['file']}  "
             f"({out['source']['n_items_selected']} items selected, {ov['n_items']} with tool calls)",
             f"  labels sha256 {out['labels']['sha256'][:16]}  ({out['labels']['file']})", ""]
    lines.append(f"  tool calls: {ov['tool_calls']}  by tool {ov['tools']}  per item {ov['calls_per_item']}")
    cv = ov["call_validity"]
    lines.append(_line("call_validity", cv["overall"]))
    for t, r in cv["by_tool"].items():
        lines.append(_line(f"  {t}", r))
    lines.append(_line("first_tool_ok", ov["first_tool_ok"]))
    lines.append(_line("tool_set_ok", ov["tool_set_ok"]))
    lines.append(_line("allowed_only_ok", ov["allowed_only_ok"]))
    b = ov["batched"]
    lines.append(f"  batched (method {b['method']}): " + json.dumps({k: v for k, v in b.items() if k != "method"}))
    if ov["redundant_calls"]:
        lines.append(f"  redundant_calls: {ov['redundant_calls']}")
    if ov["arg_validity"]:
        lines.append("  arg_validity (lookup_financial_fact): " + json.dumps(ov["arg_validity"]))
    lines.append(f"  latency (without terminal failures) {ov['latency_excluding_terminal_failures']}")
    lines.append(f"  latency (with terminal failures)    {ov['latency_including_terminal_failures']}")
    lines.append(f"  model calls {ov['model_calls']}  cost {ov['cost_usd']}  terminal failures {ov['terminal_failures']}")
    lines.append(f"  figure_primary {ov['figure_primary']}  verification {ov['verification']}")
    return "\n".join(lines)


def _load_store():
    """The XBRL fact store for the concept check, or None when data/facts.sqlite is absent (the check is then skipped)."""
    try:
        from retrieval.facts import FactStore
        return FactStore()
    except Exception:  # noqa: BLE001 — optional input; the output records that the check did not run
        return None


def build_output(results_path: Path, payload: dict, labels_path: Path, ids: list[str] | None, store,
                 baseline_path: Path | None = None, baseline_payload: dict | None = None) -> dict:
    labels, labels_sha = load_labels(labels_path)
    metrics = compute(payload, labels, ids, store)
    cfg = payload.get("config") or {}
    out = {
        "tool_metrics_version": TOOL_METRICS_VERSION,
        "source": {"file": _rel(results_path), "sha256": file_sha256(results_path), "run_id": payload.get("run_id"),
                   "label": payload.get("label"), "git_commit": payload.get("git_commit"),
                   "git_dirty": payload.get("git_dirty"), "config_hash": cfg.get("config_hash"),
                   "prompt_version": cfg.get("prompt_version"), "tool_schema_version": cfg.get("tool_schema_version"),
                   "agent_model": cfg.get("agent_model"), "benchmark_version": cfg.get("benchmark_version"),
                   "n_items_selected": len([r for r in payload.get("results") or [] if not ids or r.get("id") in set(ids)]),
                   "ids": ids},
        "labels": {"file": _rel(labels_path), "sha256": labels_sha},
        "method": {"overlap_slack_ms": OVERLAP_SLACK_MS, "concept_check": "ran" if store is not None else "skipped (no fact store)",
                   "first_call": "earliest t0_ms when every call has one, else the first recorded call (completion order)"},
        "metrics": metrics,
    }
    if baseline_payload is not None and baseline_path is not None:
        base = compute(baseline_payload, labels, ids, store)
        out["baseline"] = {"file": _rel(baseline_path), "sha256": file_sha256(baseline_path),
                           "run_id": baseline_payload.get("run_id")}
        out["comparison"] = compare(metrics, base)
    return out


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Tool-call quality metrics from a results file (no model call).")
    p.add_argument("results", type=Path, help="eval/results/<run>.json")
    p.add_argument("--ids", default=None, help="comma-separated item ids to restrict to")
    p.add_argument("--baseline", type=Path, default=None, help="a second results file to compare against, over the same ids")
    p.add_argument("--labels", type=Path, default=LABELS_FILE)
    p.add_argument("--out", type=Path, default=None, help=f"output file (default {OUT_DIR.name}/<results stem>[-subset].json)")
    p.add_argument("--no-facts", action="store_true", help="skip the concept check (no data/facts.sqlite needed)")
    args = p.parse_args(argv)

    with open(args.results, encoding="utf-8") as f:
        payload = json.load(f)
    if not has_tool_calls(payload):
        print(f"{args.results.name}: no meter.tool_calls in any record; skipped.")
        return 0
    baseline = None
    if args.baseline:
        with open(args.baseline, encoding="utf-8") as f:
            baseline = json.load(f)
    ids = [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None
    store = None if args.no_facts else _load_store()
    out = build_output(args.results, payload, args.labels, ids, store, args.baseline, baseline)

    path = args.out or (OUT_DIR / f"{args.results.stem}{'-subset' if ids else ''}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    print(format_report(out))
    print(f"\nwritten: {_rel(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
