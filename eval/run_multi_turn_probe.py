# eval/run_multi_turn_probe.py
#
# PURPOSE
# -------
# Does per-thread conversation memory (agent/memory.py) answer the follow-ups
# a stateless API cannot?  This runs eval/multi_turn_probe.json, eight
# conversations of two or three turns whose follow-ups leave out something the
# previous turn supplied, through the REAL API code path (api/main.py's /query,
# in process, with the real direct agent and the output contract):
#
#   (a) with memory     each conversation is one thread: every turn carries the
#                       conversation's thread id, so turn n sees turns 1..n-1.
#   (b) in isolation    every follow-up asked alone, with no thread id: what the
#                       stateless system does today.  A conversation's first turn
#                       is the same request in both modes, so (b) reuses (a)'s
#                       answer for it instead of paying for it twice.
#
# Each answer is scored with the repository's judge-free figure check
# (eval/figure_match.py, the check behind figure_primary): does the answer
# contain the ground truth's primary figure?  A refused answer scores as not
# found.  The headline is "x of N follow-up turns answered correctly with
# memory, y of N without", and N is 11: the probe shows the mechanism works, it
# does not estimate a rate.
#
#   python -m eval.run_multi_turn_probe --dry-run
#   python -m eval.run_multi_turn_probe --label memory-v1
#
# Results go to eval/probes/multiturn-<label>-<config hash>.json; checkpoints to
# eval/cache/multiturn-<label>.json (git-ignored), so a stopped run resumes
# without re-buying turns.  Tracing is the caller's business: run with
# LANGSMITH_TRACING=false when no trace upload is wanted.

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EVAL_DIR = Path(__file__).resolve().parent
PROBE_FILE = EVAL_DIR / "multi_turn_probe.json"
PROBES_DIR = EVAL_DIR / "probes"
CACHE_DIR = EVAL_DIR / "cache"
SCHEMA_VERSION = 1
EXIT_QUOTA_EXHAUSTED = 2

# What a spend estimate for one query uses (the committed full run averaged $0.0021 with verification) and the safety
# margin the repo's meter gets, because it cannot see retried or interrupted requests (docs/UPGRADE_RUN.md, ledger).
EXPECTED_USD_PER_QUERY = 0.0025
SAFETY_MARGIN = 1.25
SLEEP_SECONDS = 3.0

# Words in a server-side log or exception that mean the provider refused for money or key reasons: stop at once.
_QUOTA_MARKERS = ("resource_exhausted", "resourceexhausted", "429", "quota", "rate limit", "ratelimit",
                  "too many requests", "billing", "api key not valid", "permission_denied", "permission denied")


def load_probe(path: Path = PROBE_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def query_plan(probe: dict, ids: list[str] | None = None) -> list[dict]:
    """Every request the run makes, in order: {conversation, turn, mode, question, thread}.

    Memory mode asks every turn of a conversation on one thread; isolation mode asks only the follow-ups (the first
    turn is the same request and is reused).
    """
    wanted = set(ids) if ids else None
    plan = []
    for conv in probe["conversations"]:
        if wanted is not None and conv["id"] not in wanted:
            continue
        for i, turn in enumerate(conv["turns"]):
            plan.append({"conversation": conv["id"], "turn": i, "mode": "memory", "question": turn["question"],
                         "thread": conv["id"], "depends_on_history": turn["depends_on_history"]})
    for conv in probe["conversations"]:
        if wanted is not None and conv["id"] not in wanted:
            continue
        for i, turn in enumerate(conv["turns"]):
            if turn["depends_on_history"]:
                plan.append({"conversation": conv["id"], "turn": i, "mode": "isolated", "question": turn["question"],
                             "thread": None, "depends_on_history": True})
    return plan


def expected_cost(n_queries: int) -> dict:
    return {"queries": n_queries, "expected_usd": round(n_queries * EXPECTED_USD_PER_QUERY, 4),
            "counted_usd": round(n_queries * EXPECTED_USD_PER_QUERY * SAFETY_MARGIN, 4)}


def thread_id_for(label: str, conversation: str) -> str:
    """A thread id the API accepts (^[A-Za-z0-9_-]{8,64}$) that is unique per run label and conversation."""
    raw = f"probe-{label}-{conversation}"
    return "".join(c if (c.isalnum() or c in "_-") else "_" for c in raw)[:64]


# ── scoring ────────────────────────────────────────────────────────────────────

def score_turn(ground_truth: str, answer: str | None, verification_status: str | None) -> dict:
    """The judge-free figure check on one answer; a refused or missing answer asserts no figure."""
    from eval.figure_match import figure_match

    if not answer or verification_status == "refused":
        return {"correct": False, "applicable": True, "figure_primary": False, "figure_exact": False,
                "refused": verification_status == "refused", "missing": None}
    fig = figure_match(ground_truth, answer)
    return {"correct": bool(fig.get("figure_primary")), "applicable": bool(fig.get("applicable")),
            "figure_primary": bool(fig.get("figure_primary")), "figure_exact": bool(fig.get("figure_exact")),
            "refused": False, "missing": fig.get("missing")}


class QuotaWatch(logging.Handler):
    """Notices a provider refusal for money or key reasons in what the API logs server-side (it never says so to a client)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.hit: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage().lower()
        if record.exc_info and record.exc_info[1] is not None:
            text += f" {type(record.exc_info[1]).__name__} {record.exc_info[1]}".lower()
        for marker in _QUOTA_MARKERS:
            if marker in text:
                self.hit = marker
                return


# ── the run ────────────────────────────────────────────────────────────────────

def run_probe(post, probe: dict, label: str, memory=None, ids: list[str] | None = None, cache: dict | None = None,
              save_cache=None, sleep=time.sleep, cap_counted_usd: float | None = None, quota_watch: QuotaWatch | None = None) -> dict:
    """Run the plan; return {"turns": [...], "stop_reason": str | None}.

    *post(question, thread_id)* sends one request through the API and returns (status code, response dict).  *cache*
    maps "mode|conversation|turn" to a finished turn record; a cached turn is not asked again, and in memory mode its
    answer is put back into *memory* (when it passed verification) so the next turn of that thread still sees it.
    """
    cache = {} if cache is None else cache
    turns_by_conv = {c["id"]: c["turns"] for c in probe["conversations"]}
    records: list[dict] = []
    spent = 0.0
    stop_reason = None
    for step in query_plan(probe, ids):
        key = f"{step['mode']}|{step['conversation']}|{step['turn']}"
        truth = turns_by_conv[step["conversation"]][step["turn"]]["ground_truth"]
        thread = thread_id_for(label, step["conversation"]) if step["thread"] else None
        if key in cache:
            rec = cache[key]
            if step["mode"] == "memory" and memory is not None:
                memory.remember(thread, rec["question"], rec.get("answer") or "", rec.get("verification_status"))
            records.append(rec)
            continue
        if cap_counted_usd is not None and spent * SAFETY_MARGIN + EXPECTED_USD_PER_QUERY * SAFETY_MARGIN > cap_counted_usd:
            stop_reason = f"the spend cap of ${cap_counted_usd:.2f} (counted) would be exceeded at {key}"
            break
        t0 = time.perf_counter()
        status, body = post(step["question"], thread)
        wall = round((time.perf_counter() - t0) * 1000, 1)
        body = body if isinstance(body, dict) else {}
        verification = body.get("verification") or {}
        meta = body.get("meta") or {}
        answer = body.get("answer") if status == 200 else None
        rec = {
            "key": key, "conversation": step["conversation"], "turn": step["turn"], "mode": step["mode"],
            "question": step["question"], "ground_truth": truth, "depends_on_history": step["depends_on_history"],
            "thread_id": thread, "http_status": status, "answer": answer,
            "verification_status": verification.get("status"), "wall_ms": wall,
            "meta": {k: meta.get(k) for k in ("latency_ms", "llm_calls", "input_tokens", "output_tokens", "cost_usd",
                                              "tools", "thread_id", "thread_turns", "backend")},
            **{"score": score_turn(truth, answer, verification.get("status"))},
        }
        records.append(rec)
        cache[key] = rec
        if save_cache:
            save_cache(cache)
        spent += float(meta.get("cost_usd") or 0.0)
        if quota_watch is not None and quota_watch.hit:
            stop_reason = f"provider refusal for money or key reasons seen in the server log ({quota_watch.hit!r}) at {key}"
            break
        sleep(SLEEP_SECONDS)
    return {"turns": records, "stop_reason": stop_reason}


def summarize(probe: dict, records: list[dict], ids: list[str] | None = None) -> dict:
    """The counts the probe reports: follow-up turns correct with memory and in isolation, N stated, plus cost."""
    wanted = set(ids) if ids else None
    convs = [c for c in probe["conversations"] if wanted is None or c["id"] in wanted]
    follow_ups = sum(1 for c in convs for t in c["turns"] if t["depends_on_history"])
    firsts = sum(1 for c in convs for t in c["turns"] if not t["depends_on_history"])
    by = {(r["mode"], r["conversation"], r["turn"]): r for r in records}

    def correct(mode, depends):
        n = sum(1 for r in records if r["mode"] == mode and r["depends_on_history"] == depends and r["score"]["correct"])
        return n

    iso_first = sum(1 for r in records if r["mode"] == "memory" and not r["depends_on_history"] and r["score"]["correct"])
    per_conv = []
    for c in convs:
        row = {"id": c["id"], "kind": c["kind"], "turns": []}
        for i, t in enumerate(c["turns"]):
            mem, iso = by.get(("memory", c["id"], i)), by.get(("isolated", c["id"], i))
            row["turns"].append({"turn": i, "depends_on_history": t["depends_on_history"],
                                 "with_memory": None if mem is None else mem["score"]["correct"],
                                 "in_isolation": (None if not t["depends_on_history"] else
                                                  (None if iso is None else iso["score"]["correct"]))})
        per_conv.append(row)
    costs = [r["meta"].get("cost_usd") or 0.0 for r in records]
    lat = [r["meta"].get("latency_ms") for r in records if r["meta"].get("latency_ms") is not None]
    return {
        "n_conversations": len(convs), "n_follow_up_turns": follow_ups, "n_first_turns": firsts,
        "follow_ups_correct_with_memory": {"n": correct("memory", True), "of": follow_ups},
        "follow_ups_correct_in_isolation": {"n": correct("isolated", True), "of": follow_ups},
        "first_turns_correct": {"n": iso_first, "of": firsts},
        "n_requests": len(records),
        "http_errors": sum(1 for r in records if r["http_status"] != 200),
        "refused": sum(1 for r in records if r["verification_status"] == "refused"),
        "verification": dict(sorted({s: sum(1 for r in records if r["verification_status"] == s)
                                     for s in {r["verification_status"] for r in records if r["verification_status"]}}.items())),
        "cost_usd_actual": round(sum(costs), 6), "cost_usd_counted": round(sum(costs) * SAFETY_MARGIN, 6),
        "latency_ms_mean": round(sum(lat) / len(lat), 1) if lat else None,
        "per_conversation": per_conv,
        "note": (f"N is small: {follow_ups} follow-up turns in {len(convs)} conversations. This shows the memory mechanism "
                 "works on these conversations; it is not an estimate of a rate."),
    }


# ── wiring: the real API in process ────────────────────────────────────────────

def build_client():
    """A TestClient over the real api.main app: real direct agent, real contract, a fresh in-process memory."""
    from fastapi.testclient import TestClient

    from agent import financial_agent
    from agent.memory import ThreadMemory
    from api import main as api_main

    api_main.app.state.mcp_agent = None
    api_main.app.state.direct_agent = financial_agent.build_agent_executor()
    memory = ThreadMemory.from_env()
    memory.enabled = True                      # the probe is about memory; an inherited THREAD_MEMORY=off must not hide it
    api_main.app.state.thread_memory = memory
    client = TestClient(api_main.app)

    def post(question: str, thread_id: str | None):
        body = {"question": question}
        if thread_id:
            body["thread_id"] = thread_id
        resp = client.post("/query", json=body)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {}
    return post, memory


def _git_commit() -> tuple[str, bool]:
    import subprocess

    def run(*args):
        return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    try:
        return run("git", "rev-parse", "--short", "HEAD"), bool(run("git", "status", "--porcelain"))
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return "nogit", False


def build_config(probe_path: Path, ids: list[str] | None) -> dict:
    from agent import financial_agent
    from agent.contract import CONTRACT_VERSION, verify_mode
    from agent.memory import DEFAULT_MAX_TURNS, HISTORY_PREFACE
    from eval.experiment import canonical_json, config_hash, file_sha256  # noqa: F401
    from eval.run_eval import _prompt_version, _tool_schema_version
    from retrieval.retriever import RetrievalConfig

    cfg = {
        "result_kind": "multiturn_probe", "schema_version": SCHEMA_VERSION,
        "probe_file": probe_path.name, "probe_sha256": file_sha256(probe_path), "conversation_ids": ids or "all",
        "agent_model": financial_agent.LLM_MODEL, "agent_provider": "google",
        "prompt_version": _prompt_version(), "tool_schema_version": _tool_schema_version(),
        "agent_batch_rule": "on" if financial_agent.batch_rule_enabled() else "off",
        "verify_mode": verify_mode(), "contract_version": CONTRACT_VERSION,
        "retrieval": RetrievalConfig.from_env().as_dict(),
        "memory": {"max_turns": int(os.getenv("THREAD_MEMORY_MAX_TURNS") or DEFAULT_MAX_TURNS),
                   "history_preface_sha": config_hash({"preface": HISTORY_PREFACE}, 8)},
        "scoring": "eval/figure_match.py::figure_match, figure_primary",
    }
    cfg["config_hash"] = config_hash({k: v for k, v in cfg.items()})
    return cfg


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the multi-turn memory probe through the real API code path.")
    p.add_argument("--label", default="memory-probe", help="run label (results file name, thread ids)")
    p.add_argument("--probe", type=Path, default=PROBE_FILE)
    p.add_argument("--ids", default=None, help="comma-separated conversation ids")
    p.add_argument("--dry-run", action="store_true", help="print the plan and the expected cost; make no model call")
    p.add_argument("--cap-usd", type=float, default=0.15, help="stop when the counted spend (actual x 1.25) would pass this")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--force", action="store_true", help="run even if a complete results file for this configuration exists")
    args = p.parse_args(argv)

    probe = load_probe(args.probe)
    ids = [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None
    plan = query_plan(probe, ids)
    cost = expected_cost(len(plan))
    follow = sum(1 for s in plan if s["mode"] == "isolated")
    print(f"{len(plan)} requests ({len(plan) - follow} with memory, {follow} follow-ups in isolation); "
          f"expected ${cost['expected_usd']} actual, ${cost['counted_usd']} counted (x{SAFETY_MARGIN}).")
    if args.dry_run:
        for s in plan:
            print(f"  [{s['mode']:<8}] {s['conversation']} turn {s['turn']}  thread={s['thread'] and thread_id_for(args.label, s['conversation'])}  {s['question']}")
        return 0
    if cost["counted_usd"] > args.cap_usd:
        print(f"NOT RUN: expected counted ${cost['counted_usd']} exceeds --cap-usd {args.cap_usd}.")
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = build_config(args.probe, ids)
    out_path = args.out or (PROBES_DIR / f"multiturn-{args.label}-{cfg['config_hash']}.json")
    if out_path.exists() and not args.force:
        print(f"This configuration already has a results file: {out_path.name}. Pass --force to run it again.")
        return 0

    post, memory = build_client()
    watch = QuotaWatch()
    logging.getLogger().addHandler(watch)
    cache_path = CACHE_DIR / f"multiturn-{args.label}.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    def save_cache(c):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(c, indent=2, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
        os.replace(tmp, cache_path)

    result = run_probe(post, probe, args.label, memory=memory, ids=ids, cache=cache, save_cache=save_cache,
                       cap_counted_usd=args.cap_usd, quota_watch=watch)
    summary = summarize(probe, result["turns"], ids)
    commit, dirty = _git_commit()
    payload = {
        "schema_version": SCHEMA_VERSION, "run_id": f"multiturn-{args.label}-{commit}", "label": args.label,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_commit": commit, "git_dirty": dirty,
        "config": cfg, "complete": result["stop_reason"] is None and len(result["turns"]) == len(query_plan(probe, ids)),
        "stop_reason": result["stop_reason"], "summary": summary, "turns": result["turns"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    s = summary
    print(f"\nwith memory: {s['follow_ups_correct_with_memory']['n']} of {s['follow_ups_correct_with_memory']['of']} follow-up turns correct")
    print(f"in isolation: {s['follow_ups_correct_in_isolation']['n']} of {s['follow_ups_correct_in_isolation']['of']} follow-up turns correct")
    print(f"first turns: {s['first_turns_correct']['n']} of {s['first_turns_correct']['of']}   refused {s['refused']}   http errors {s['http_errors']}")
    print(f"cost: ${s['cost_usd_actual']} actual, ${s['cost_usd_counted']} counted   N is small ({s['n_follow_up_turns']} follow-ups)")
    print(f"written: {out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}")
    if result["stop_reason"]:
        print(f"STOPPED: {result['stop_reason']}")
        return EXIT_QUOTA_EXHAUSTED if "money or key" in result["stop_reason"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
