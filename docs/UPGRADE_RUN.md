# FRA upgrade run: journal, ledger and report

Prompt: `FRA upgrade prompts/FRA_UPGRADE_PROMPT.md` (outside this repo). This file is the run's journal (appended after every step), its spend ledger, and, at the end, its REPORT.

<!-- REPORT: written at the top of this file in Phase 6 -->

## CONFIG (final as given; the owner did not edit it)

```
SPEND_CAP_USD       = 1.00
JUDGED_RUNS_ALLOWED = 0
COMMIT_POLICY       = current-branch
PUSH                = false
```

## Start state

- Branch at start: `main` (not detached). START_SHA: `eda988efb5c5331bef5af9173a240ee7d683a3b3`.
- `git status --short` at start: empty (clean tree). No `.git/index.lock` present; no git process running.
- Interpreter: `C:\Users\1842s\anaconda3\envs\financial-agent\python.exe`, Python 3.11.15 (CI uses 3.11).
- The repo is the `financial-research-agent/` folder inside the owner's project folder; the parent holds the filings, `secrets.txt`, `hf-space/` and the prompt. Nothing outside this repo is touched.
- Commit trailer: the owner's standing instruction for this repo is no `Co-Authored-By` trailer, which overrides the harness's default attribution line. Commits here carry none.

## Spend ledger

Counted cost = actual cost (sum of `cost_usd` in the new results file; for the probe, the sum of its meters) x 1.25. The cap check uses the sum of the counted costs. Before a live command: expected = items x $0.0025 x 1.25; if ledger total + expected > SPEND_CAP_USD, the command is NOT RUN.

| # | time | command | items | expected (counted) | actual | counted (x1.25) | ledger total |
|---|------|---------|-------|--------------------|--------|-----------------|--------------|
| | | (no paid call yet) | | | | | $0.0000 |

## Journal

### 2026-10-04 22:07 Phase 0.1: baseline recorded

Gates at START_SHA (`eda988e`), run with the conda interpreter:

- `python -m pytest --tb=short --strict-markers -p no:cacheprovider`: 224 passed in 18.4 s.
- `flake8 . --max-line-length 120 --ignore E501,W503`: exit 0, no output.
- `python -m eval.run_eval --dry-run`: exit 0 (5 smoke items listed).
- `python -m eval.run_retrieval_eval --dry-run`: exit 0 (71 items, 71 labelled, index 4,783 chunks).
- `python -m eval.ci_gate retrieval --dry-run`: exit 0.
- `npm run lint` (web/): exit 0.
- `npm test` (web/): 3 passed in 1 file (Vitest 4.1.11).
- `npm run build` (web/): exit 0, Next.js 16.3.4 (Turbopack), routes `/` and `/_not-found` prerendered.

All gates are green at START_SHA. Baseline test count: **224 Python, 3 web**.

### 2026-10-04 22:12 Phase 0.2: reading done

Read: `agent/*.py`, `api/main.py`, `mcp_server/server.py`, `retrieval/facts.py`, `eval/run_eval.py`, `tests/test_meter.py`, `test_tool_wiring.py`, `test_mcp_contract.py`, `test_query_stream.py`, `test_query_meta.py`, `test_api_edges.py`, `conftest.py`, ROADMAP, DECISIONS, and EVALUATION "Cost and latency", "Output contract" and finding 11. Facts re-verified against the code (section 4 of the prompt): all hold. Details that matter later:

- `QueryMeter.run_inline = True` (callbacks run on the calling thread), but a ToolNode runs simultaneous calls on worker threads, so the appends still need a lock.
- `tests/test_query_stream.py` and `test_api_edges.py` call `api_main._sse_event_stream(request, question)` and the fake agents' `ainvoke(payload, config=None)`; changes to the API's internal helpers must keep those call shapes working.
- `eval/run_eval.py` regenerates `eval/results/LEADERBOARD.md` at the end of every run (`write_leaderboard`). That is a generated file, and Phase 5 regenerates it on purpose; between runs I will leave any leaderboard change uncommitted and commit it once, in Phase 5, so each run's `git_dirty` flag stays honest.
- `conftest.py` forces `VERIFY_MODE=off` for tests.
- The harness's cache key hashes the system prompt only (prompt trap, section 4.5); every experiment uses its own `--cache-file` and `--label`.

Decisions so far: none needed.

NEXT STEP: commit this journal (Phase 0.3), then Phase 1.2 (blind tool labels) BEFORE anything opens a results file's `tools_used` / `meter.tool_calls`; then Phase 1.1 (meter), 1.3, 1.4.

### 2026-10-04 22:16 Phase 0.3, 1.2 and 1.1 done (commits 9c1555a, 29dabc3, e559f2f)

- **0.3** Journal committed (`9c1555a`).
- **1.2 Blind labels** (`29dabc3`, committed on its own, before any results file's `tools_used` or `meter.tool_calls` was opened; I opened none). `eval/benchmark_tools.json`: 71 items with `first_tool_ok`, `allowed_tools`, `required_tools`, `tickers`, `fiscal_year` (plus an optional `fiscal_years_ok` for growth questions, which legitimately look up the base year), and a one-line rationale. Written from question, ground truth, `question_type`, `section`, prompt rules 5 to 8 and the tool docstrings; I did not use the `reference_contexts` or `notes` columns. Audit: three independent labellers (subagents handed only those same fields, one file, no repo access) labelled all 71 items from scratch. They matched my `first_tool_ok` on 65 / 64 / 61 of 71, `allowed_tools` on 61 / 56 / 62, `required_tools` on 71 / 71 / 71, `fiscal_year` on 70 / 71 / 71. Adjudication (widen where a defensible alternative exists): `qa_0001`, `qa_0048` gain `lookup_financial_fact` as an acceptable first call (the cover period is tagged); `qa_0003`, `qa_0016`, `qa_0023`, `qa_0028`, `qa_0040`, `qa_0051` gain it as allowed; `qa_0013`, `qa_0045`, `qa_0050` lose it from allowed (all three labellers found a fact lookup indefensible there). Kept as written: search as a first call on the three growth questions `qa_0021`, `qa_0041`, `qa_0053` (one labeller of three dissented; rule 7 is explicit). Six items require `compute_metric` (`qa_0005, 0007, 0021, 0034, 0041, 0053`), the ones whose question asks for a growth rate, change or difference (rule 8). The panel are language models reading the same rules, so the labels are blind but not independent (a limitation the docs will state). Sidecar sha256 at commit: `0DD9B822CFD3899CE638C7883351FDEB54B698AB2D5FC314ABADDF094C73CFE5`. A test pins that it covers exactly the 71 benchmark ids, names only tools that exist, and is not read by `benchmark_version`.
- **1.1 Meter** (`e559f2f`). `tool_calls` entries keep `name`, `ms`, `error` and gain `args`, `t0_ms`, `error_message`; appends are under a lock; `public_meta` unchanged. 8 new tests in `tests/test_meter.py` (6 before, 14 now) cover arguments, truncation, overlapping windows from two threads, 400 concurrent appends, error message, old keys, `public_meta` never carrying arguments, and one run through LangGraph's real ToolNode with a scripted fake model: three tool calls in one model step started 2 ms apart on worker threads, overlapping, and the call without `concept` was recorded as an error with `Field required` in its message.
- Gates after both commits: 236 passed, flake8 clean, three dry-runs pass. Test count: 224 to 236 (labels 4, meter 8).

NEXT STEP: Phase 1.3: write `eval/tool_metrics.py` and `tests/test_tool_metrics.py`; then 1.4 run it offline on `cost-v3` and `contract-v3` and reproduce section 5 of the prompt.

### 2026-10-04 22:30 Phase 1.3 and 1.4 done (commits a37de84, 258c5d2); tool_schema_version added

- **1.3 `eval/tool_metrics.py`** (`python -m eval.tool_metrics <results.json> [--ids ...] [--baseline <results.json>] [--out ...] [--no-facts]`), output in `eval/tool_metrics/` (verified: `python -m eval.leaderboard` still renders, to a scratch file, with the new directory present; `eval/results/` untouched). Metrics: `call_validity` (framework errors plus the argument-error marker), `first_tool_ok`, `tool_set_ok` (and `allowed_only_ok`, the label set widest in doubt), `batched` (window overlap with each end padded by 50 ms, because a 2 ms lookup can finish before its sibling's worker thread has started; per-item "calls greater than model calls minus 1" heuristic with `method: heuristic` on older files), `redundant_calls`, `arg_validity` (lookup only, files with arguments only; the concept check uses `FactStore.lookup`, no model), each as counts over denominators, overall and by `question_type`. The tolerant-tool marker (`[tool argument error] <tool>: ...`, helpers in `agent/observations.py`) is counted as a failed call. 29 tests in `tests/test_tool_metrics.py`, including the two committed files pinned to the baseline numbers.
- **1.4 Offline analysis.** Only three committed results files carry `meter.tool_calls`: `cost-v3`, `contract-v3`, `reindex-v3`. Section 5 reproduced from the committed files:
  - `cost-v3`: 71 items; 131 calls (`search_filings` 59, `lookup_financial_fact` 58, `compute_metric` 8, `list_available_companies` 6); 7 failed, all `lookup_financial_fact`, on 6 items (`qa_0005, 0008, 0018, 0021, 0041, 0053`); calls per item `{1: 46, 2: 8, 3: 7, 4: 6, 5: 3, 9: 1}`; the eight items with more calls than tool steps (steps = `llm_calls` - 1) are exactly `qa_0007, 0012, 0034, 0043, 0053, 0060, 0062, 0069`; 45 figure-applicable items, 33 of them called `lookup_financial_fact`; one terminal failure, `qa_0062` (recursion limit).
  - `contract-v3`: over the 70 answered items, latency p50 3,689.5 ms, p95 8,971.7 ms, mean 4,452.8 ms **with linear-interpolated percentiles** (these round to the section 5 figures 3,690 / 8,972 / 4,453). The harness's own nearest-rank percentiles give p50 3,684.2 ms and p95 9,077.0 ms over the same 70, and over all 71 items p50 3,694.8 ms / p95 9,077.0 ms (the 3.7 s / 9.1 s the docs quote). Mean cost $0.002067 per query over 71 items, $0.1467 total. Every latency block in the tool-metrics files carries both percentile conventions and both with and without the terminal-failure item.
- **Baseline tool-quality numbers (cost-v3 and contract-v3 are identical in tool use):** `call_validity` 124 of 131; `lookup_financial_fact` 51 of 58; `first_tool_ok` 67 of 71; `tool_set_ok` 67 of 71. The four items that miss are `qa_0008`, `qa_0065`, `qa_0068`, `qa_0071`, all because the first call was `list_available_companies`. Two of them (`qa_0068`, `qa_0071`) do not name a company in the question ("What were Google Cloud revenues?", "What were AWS's net sales?"), where a ticker-list call is defensible. I did **not** revise the labels after seeing this (rule R2); the docs will say 67 of 71 as labelled and that two of the four misses are arguable. `reindex-v3` (the judged strict run on the rebuilt index): 129 calls, 122 valid, `tool_set_ok` 66 of 71, latency p50 3,231 ms: the same configuration's latency p50 differs by 12% between two committed runs, which is larger than the 10% latency gate of Phase 3.4, so that gate will be read against this noise.
- **tool_schema_version.** `agent/financial_agent.py` now has a single `TOOLS` list and `tool_schema_version()` (sha256 over each tool's OpenAI-style name, description and argument schema; 12 hex). `eval/run_eval.py` records it in every results file's config; it is hashed with the config (a tool change gives a different results file) and is not part of the cache key. Current value `sha256:3eeb27dc718d`; `prompt_version` is still `sha256:99d36aed6b9c`, the one in `cost-v3`. 4 tests (`tests/test_tool_schema_version.py`).
- Test count after this: 269 (224 + 4 labels + 8 meter + 29 metrics + 4 schema version).

NEXT STEP: commit `tool_schema_version`; then Phase 2.1: fix ITEMS_A (the six error items plus the first six figure-applicable items in benchmark order that did not error), list them here, check the spend ledger (SPEND_CAP_USD 1.00), and run the unchanged shipped configuration on ITEMS_A once as a same-day "before" (12 items, expected counted cost 12 x $0.0025 x 1.25 = $0.0375) so any candidate is compared with today's model, not only the 09-28 files.

### 2026-10-04 22:40 Phase 2.1: ITEMS_A fixed

ITEMS_A (never changes), 12 items: the six items whose `cost-v3` calls were rejected, `qa_0005, qa_0008, qa_0018, qa_0021, qa_0041, qa_0053`, plus the first six figure-applicable items in benchmark order with no failed call, `qa_0002, qa_0004, qa_0006, qa_0007, qa_0009, qa_0010` (figure-applicable includes the address of `qa_0002` and the headcount of `qa_0004`, because the figure check v4 counts any non-year figure in the ground truth).

`--ids qa_0002,qa_0004,qa_0005,qa_0006,qa_0007,qa_0008,qa_0009,qa_0010,qa_0018,qa_0021,qa_0041,qa_0053`

Live-run rules I apply to every paid command: shipped configuration (`VERIFY_MODE=strict LLM_MODEL=gemini-3.1-flash-lite RETRIEVAL_RERANK=true RETRIEVAL_FETCH_K=50 RETRIEVAL_TICKER_FILTER=inferred`), `--generate-only`, its own `--cache-file eval/cache/<label>.json`, a distinct `--label`, a clean tree at the start (so `git_dirty` is false), and **LangSmith tracing forced off** (`LANGSMITH_TRACING=false`, `LANGCHAIN_TRACING_V2=false`): the owner's `.env` turns LangSmith on with a key, and a trace upload is a call to a non-Gemini API, which this run may not make. `trace_id` is still recorded (it is the root run id the meter takes) but will not open as a LangSmith trace. After each run: restore `eval/results/LEADERBOARD.md` (the harness regenerates it; Phase 5 regenerates it once on purpose), run `eval.tool_metrics` on the new file, commit the results and metrics.

Plan for this phase: (0) run the UNCHANGED code on ITEMS_A once, label `a-before`, so any candidate is compared with today's model and not only with the 09-28 files (12 items, expected counted cost 12 x $0.0025 x 1.25 = $0.0375; ledger total before: $0.0000; cap $1.00); (a) wording; (b) schema; (c) tolerant tool, stopping at the first that passes the section 2.4 rule.

NEXT STEP: run `a-before` (see the ledger row), then write the candidate (a).
