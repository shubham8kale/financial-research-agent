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
| 1 | 22:25 | `run_eval --generate-only --label a-before --cache-file eval/cache/a-before.json --ids <ITEMS_A>` (shipped configuration, unchanged code at `bf271f0`) | 12 | $0.0375 | $0.0247 | $0.0309 | $0.0309 |
| 2 | 22:50 | `run_eval --generate-only --label a-wording --cache-file eval/cache/a-wording.json --ids <ITEMS_A>` (candidate (a), git `8e3d248`) | 12 | $0.0375 | $0.0231 | $0.0289 | $0.0598 |
| 3 | 22:45 | `run_eval --generate-only --label b-rule-v1 --cache-file eval/cache/b-rule-v1.json --ids <ITEMS_B>` with `AGENT_BATCH_RULE=on` (git `3d1f616`) | 17 | $0.0531 | $0.0425 | $0.0531 | $0.1129 |
| 4 | 22:50 | `run_eval --generate-only --label b-control --cache-file eval/cache/b-control.json --ids <ITEMS_B>` with `AGENT_BATCH_RULE=off` (fix kept, git `911f8c5`) | 17 | $0.0531 | $0.0421 | $0.0526 | $0.1656 |
| 5 | 22:57 | `run_eval --generate-only --label b-rule-v2 --cache-file eval/cache/b-rule-v2.json --ids <ITEMS_B>` with `AGENT_BATCH_RULE=on`, rule wording v2 (git `7438a47`) | 17 | $0.0531 | $0.0379 | $0.0474 | $0.2130 |
| | | (no other paid call yet) | | | | | |

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

### 2026-10-04 22:33 Phase 2: same-day baseline on ITEMS_A (`a-before`), ledger row 1

Ran the UNCHANGED code (git `bf271f0`, clean tree, `prompt_version sha256:99d36aed6b9c`, `tool_schema_version sha256:3eeb27dc718d`) on ITEMS_A: `eval/results/a-before-dfa6f50193ab.json`, 12 items, actual cost $0.0247 (counted $0.0309), ledger total **$0.0309** of $1.00. Tool metrics: `eval/tool_metrics/a-before-dfa6f50193ab.json`.

- **Today, unchanged: 5 of 20 `lookup_financial_fact` calls were rejected (15 of 20 valid)**, every one for a missing `concept` (`concept_present` 15 of 20); the committed `cost-v3` and `contract-v3` had 7 on the same 12 items. So the same-day baseline is 5, not 7, and any candidate has to beat 5 (the smaller, harder baseline) to count. The other arg checks were clean: ticker 20 of 20, fiscal year exact 20 of 20, concept resolves to rows 15 of 15.
- Tool use otherwise: 28 calls (lookup 20, search 3, compute 5); `first_tool_ok` 12 of 12; `tool_set_ok` 12 of 12; verification 12 of 12 verified; `figure_primary` 11 of 11 applicable; 0 terminal failures; model calls 3.0 per query (agent) and cost $0.00206 per query.
- Batching, measured with start offsets for the first time: 24 model steps made the 28 calls; 4 steps batched 8 calls (28.6% of calls) on 3 of 12 items.
- **Observation about the cause, from the stored error messages of `cost-v3` (no argument was recorded then):** in 5 of the 6 failing items the omission happens inside a batch: the model emits two or three `lookup_financial_fact` calls in one step (fiscal 2025 and fiscal 2024) and the FIRST one (on `qa_0053`, the first two) arrives with only `ticker` and `fiscal_year`. Batched emission and the rejected call are therefore linked, which matters for Workstream B: a rule that asks for more batching could raise the rejection rate, and its gate includes "invalid lookup calls below baseline".
- Side findings: (1) this run made Hugging Face Hub metadata GETs for the locally cached reranker at start-up (public, unauthenticated, no spend; later runs set `HF_HUB_OFFLINE=1` so they talk to Gemini only); (2) the harness's `--generate-only` log line and resume hint print the default cache path (`eval\cachegent_outputs.json`) although the run wrote to the `--cache-file` it was given; I confirmed `agent_outputs.json` is untouched (mtime 2026-09-27) and will fix the message in the next harness commit; (3) tracing was off as intended (no LangSmith call).

NEXT STEP: write candidate (a), wording: tool description first sentence and `_SYSTEM_PROMPT` rule 7 say `concept` is REQUIRED on every call and one call is needed per concept and year, with an example call; mirror in `mcp_server/server.py`; update `tests/test_mcp_contract.py` deliberately; then run label `a-wording` on ITEMS_A (expected counted cost $0.0375; ledger $0.0309 + $0.0375 = $0.0684).

### 2026-10-04 22:55 Phase 2 decision: candidate (a), the wording, passes; (b) and (c) not needed

`a-wording` (`eval/results/a-wording-5512347a3b12.json`, git `8e3d248`, clean tree, `prompt_version sha256:e96ec6393c90`, `tool_schema_version sha256:88e7a8918c7f`), ITEMS_A, 12 items, actual $0.0231 (counted $0.0289), ledger total **$0.0598** of $1.00. Tool metrics: `eval/tool_metrics/a-wording-5512347a3b12.json`.

| ITEMS_A, 12 items | committed `contract-v3` / `cost-v3` | `a-before` (today, unchanged) | `a-wording` |
|---|---|---|---|
| `lookup_financial_fact` calls rejected | 7 | **5 of 20** | **0 of 15** |
| tool calls | 28 | 28 | 24 |
| agent model calls per query (mean) | n/a | 3.00 | 2.58 |
| `figure_primary` (11 applicable items) | 11 of 11 | 11 of 11 | 11 of 11 |
| verification | 12 verified | 12 verified | 12 verified |
| `first_tool_ok` / `tool_set_ok` | n/a | 12 of 12 / 12 of 12 | 11 of 12 / 11 of 12 |
| cost per query | n/a | $0.00206 | $0.00193 |

- **Rule 2.4 (keep only if invalid lookup calls are strictly fewer on ITEMS_A and no control item changes its `figure_primary` result or its verification status): met.** Invalid calls 0 < 5 (today) < 7 (committed). All six controls (`qa_0002, 0004, 0006, 0007, 0009, 0010`) keep `figure_primary` True and `verified`; the six error items keep both too. So (a) is kept and unconditional (it repairs a defect). Candidates (b) schema and (c) tolerant tool were not needed, so they were not built and their code does not exist; the argument-error marker helpers in `agent/observations.py` and the metric that counts them stay (the metric is still the right instrument if a tool ever returns the marker), and the docs will say (c) was not taken.
- The one tool-selection miss in `a-wording` is `qa_0008` calling `list_available_companies` first; the committed `cost-v3` and `contract-v3` runs also did, and `a-before` did not, so this is run-to-run variation on that item, not an effect of the wording (the labels were not revised).
- **What this does and does not show.** n is 12 items and 15 to 20 calls. ITEMS_A was chosen BECAUSE six of its items failed in the committed run, so it over-represents failures (the committed full-benchmark rate is 7 of 58 lookups, 12%, and `a-before` reproduced 5 of 20 on these items: a regression-to-the-mean effect is visible already). 0 of 15 against 5 of 20 is Fisher exact p about 0.06 (one-sided about 0.05): suggestive, not conclusive. The full 71-item confirmation run in Phase 3.4 re-measures call validity over every lookup (baseline 7 of 58 lookups rejected, 12%), and the docs will quote the ITEMS_A figures as a subset result with this caveat, not as a headline.
- Also seen while comparing: the model omits `concept` inside a batched step (see the previous entry). With the wording in, the three-lookup trajectories of `qa_0005`, `qa_0021`, `qa_0041` and `qa_0053` still batch (`batched` 10 of 24 calls) and none were rejected.

NEXT STEP: commit these results; run the gates and commit the Phase 4.2 API wiring (`api/main.py`, `tests/test_query_threads.py`, 38 tests, written while the live run was in flight); then Phase 3.1 (concurrency test for `search_filings` and `compare_companies` against the real index, no spend).

### 2026-10-04 23:10 Phase 4 backend (4.1, 4.2, 4.5) and Phase 3.1 done (commits 1a89704, 014c04e, 6f930cb, 97a5b1c)

- **4.1 `agent/memory.py`** (`1a89704`): `ThreadMemory` as specified: at most `THREAD_MEMORY_MAX_TURNS` (6) turns per thread, stored question cut to 500 characters and answer to 1,200, idle TTL `THREAD_MEMORY_TTL_SECONDS` (1800), `THREAD_MEMORY_MAX_THREADS` (500) with LRU eviction, one lock, an injectable clock, `THREAD_MEMORY=on|off`. It stores an answer only when verification said `verified` or `skipped` (never `refused`, `unverified`, a terminal failure or an empty answer). The history preface (constant `HISTORY_PREFACE`) says the earlier turns are for reference only, to reuse no figure from them and to call the tools again for every figure stated. 17 tests.
- **4.2 API** (`014c04e`): optional `thread_id` (`^[A-Za-z0-9_-]{8,64}$`, otherwise 422). `/query` and `/query/stream` share `_thread_context`, `_payload` and `_finish_thread`: with earlier turns the agent gets ONE human message (history block, then `Current question:` and the question); the verification step gets the current question alone; a request without a thread id (or with memory off) sends exactly `{"messages": [("human", question)]}`; `meta` gains `thread_id` and `thread_turns` only when a thread was in force; the id rides in the LangSmith run `metadata` only and is not logged. No `thread_id` or content ever reaches a log line (tested with `caplog`). 38 tests in `tests/test_query_threads.py`: follow-up carries the prior turn on both endpoints, no thread id gives today's payload exactly (asserted), invalid ids 422 (7 shapes) and the boundary ids accepted, `/query` and `/query/stream` parity over a 3-turn conversation, `THREAD_MEMORY=off` ignores the id (object and environment), two threads never see each other, a refusal and an unverified draft and a terminal failure are never stored, the contract sees the current question alone, the history is bounded to 6 turns, the MCP path remembers identically.
- **3.1 Concurrency safety** (`6f930cb`), measured on the real index with the shipped retrieval configuration (reranker over 50, inferred ticker filter): **steady state is safe**: 48 concurrent `search_filings` calls (12 workers, 3 rounds) were byte-identical to the sequential results, and 24 concurrent `compare_companies` calls (2 rounds, 3 retrievals each) likewise, with no exception. **The first use of a cold process was not safe**: with 8 threads racing, **6 of 8 calls failed** with `ValueError: Could not connect to tenant default_tenant` because `_get_vectorstore()` is a lazy singleton and every thread built its own Chroma client (`builds {'vectorstore': 8}`); the same unguarded pattern existed for the process-wide retriever, its reranker and BM25 index, the cross-encoder model load and the fact store. The deployed Space builds the vectorstore lazily on the first search, and a batched first step is exactly what Workstream B encourages, so this was a real hazard, not a theoretical one. Fix, the narrowest one: a double-checked lock around each lazy build, taken only while the resource is unbuilt; a warm call costs 39 ns and never queues; steady-state searches are not serialised, so batching still saves wall time. After the fix: 12 racing threads built the vectorstore once and the reranker once, 12 of 12 results identical to sequential. Tests: five race tests with fakes (they fail on the unfixed code: verified by restoring the old files, 6 of 6 failed, then restored the fix), plus two real-index tests (concurrent equals sequential from a cold start, for both tools; skipped with a stated reason where the index or the cached models are absent; about 36 s locally). Honest limit: with the reranker on, a thread pool gave only about 1.4x wall-clock over sequential searches on this 12-logical-core machine (12 workers, 24 queries 12.9 s against 0.78 s per sequential query); on the 2-vCPU Space the parallel speedup of searches will be smaller. What batching reliably saves is model round trips.
- **Side fix** (`97a5b1c`): the harness's `--generate-only` log line and resume hint named the default cache file (see the 22:33 entry); now they name the file used and the resume commands carry `--cache-file`. 1 test.
- Gates after each commit: green. Python tests: 224 at start, **334 now** (+110: labels 4, meter 8, metrics 29, schema version 4, memory 17, API threads 38 plus 2 deliberate contract/wiring assertions, concurrency 8, harness hint 1).

NEXT STEP: Phase 3.2: `AGENT_BATCH_RULE=on|off` (default off for now; the default is decided by the measured gate in 3.4). The rule text is appended to the system prompt only when on, and `_prompt_version()` must hash the composed prompt; mirror in `agent/mcp_agent.py`; tests; then 3.3 on ITEMS_B (17 items, expected counted cost 17 x $0.0025 x 1.25 = $0.0531; ledger $0.0598, so $0.1129).

### 2026-10-04 22:50 Phase 3.3 (first measurement): `b-rule-v1`, the Phase 2 fix plus the batching rule on, ITEMS_B

ITEMS_B (17 items, never changes): `qa_0005, 0007, 0012, 0021, 0034, 0041, 0044, 0053` (the eight `compute_metric` items), `qa_0060` to `qa_0063` (comparative), `qa_0067` to `qa_0071` (temporal). `eval/results/b-rule-v1-8a8b2d430b78.json`, git `3d1f616`, clean tree, `prompt_version sha256:07b9ce75c773` (rule on), `agent_batch_rule on`, `tool_schema_version sha256:88e7a8918c7f`; actual $0.0425, counted $0.0531, ledger total **$0.1129**. Tool metrics with the comparison: `eval/tool_metrics/b-rule-v1-8a8b2d430b78-subset.json` (`--ids <ITEMS_B> --baseline contract-v3`).

| ITEMS_B, 17 items | committed `contract-v3` (before the fix) | `b-rule-v1` (fix + rule on) |
|---|---|---|
| tool calls (calls per item) | 59 | 47 |
| calls that failed | 5 of 59 (all `lookup_financial_fact`) | **0 of 47** |
| calls issued in a step with 2 or more calls | not measurable (no start offsets); 7 of 17 items had more calls than tool steps | **23 of 47 (48.9%)**; 10 of 17 items had a batched step; 10 of 17 had more calls than steps |
| agent model calls per query (mean) | 3.82 | **3.00** |
| latency p50 / mean, harness nearest-rank, excluding the terminal failure | 4,586 ms (n=16) / 5,283 ms | 3,199 ms (n=17) / 4,172 ms |
| latency including the terminal failure (n=17) p50 / mean | 4,586 ms / 6,014 ms | 3,199 ms / 4,172 ms |
| recursion-limit failures | 1 (`qa_0062`) | **0** (`qa_0062` answered) |
| `figure_primary` (16 applicable) | 16 of 16 | 16 of 16 |
| verification | 16 verified, 1 terminal failure | 17 verified, 0 refused |
| cost per query | $0.00302 | $0.00250 |
| `first_tool_ok` / `tool_set_ok` | 15 of 17 / 15 of 17 | 15 of 17 / 15 of 17 |

By stratum (anecdotal, n = 4, 5 and 8): comparative p50 4,003 ms (3 answered) to 5,939 ms and mean 5,658 ms to 5,237 ms; temporal p50 3,245 ms to 2,593 ms; numerical p50 5,084 ms to 3,199 ms.

**Read this with care.** (1) This compares a run made today with a run made on 09-28, and it changes two things at once (candidate (a) and the rule), so by itself it attributes nothing to the rule; the same items are being re-run with the rule OFF (`b-control`) to separate them. (2) The latency numbers were taken with my own CPU work (tests, a web build) running in the same minutes, which is noise of unknown size on top of the 12% run-to-run difference already measured between two committed runs of the same configuration. (3) `qa_0062` answering is one item; it has hit the recursion limit on every earlier run of this configuration, so it is a notable observation, not a statistic.

NEXT STEP: run `b-control` (the same 17 items, rule OFF, fix kept) on a quiet machine (expected counted cost $0.0531; ledger $0.1129 + $0.0531 = $0.1660); then decide whether to iterate the rule's wording (at most twice) or go to the full run.

### 2026-10-04 23:30 Phase 3.3: the control (`b-control`, rule OFF) says the rule adds almost nothing beyond the Phase 2 wording; the multi-turn probe is built

`b-control` (`eval/results/b-control-e8f826600800.json`, git `911f8c5`, clean tree, `prompt_version sha256:e96ec6393c90`, `agent_batch_rule off`): the same 17 ITEMS_B items with the Phase 2 fix kept and the rule OFF; actual $0.0421, counted $0.0526, ledger total **$0.1656**. Run on a quiet machine. Comparisons: `eval/tool_metrics/b-control-e8f826600800-subset.json` (against `contract-v3`) and `eval/tool_metrics/b-rule-v1-vs-b-control-subset.json` (rule on against rule off).

| ITEMS_B, 17 items | `contract-v3` (09-28) | `b-control` (fix, rule OFF) | `b-rule-v1` (fix, rule ON) |
|---|---|---|---|
| calls rejected | 5 of 59 | 0 of 47 | 0 of 47 |
| calls issued in a batched step | n/a (7 of 17 items had more calls than steps) | 21 of 47 (44.7%), 9 of 17 items | 23 of 47 (48.9%), 10 of 17 items |
| agent model calls per query | 3.82 | 3.06 | 3.00 |
| latency p50 / mean (nearest-rank, terminal failure excluded) | 4,586 / 5,283 ms (n=16) | 3,205 / 4,133 ms | 3,199 / 4,172 ms |
| recursion-limit failures | 1 (`qa_0062`) | 0 | 0 |
| `figure_primary` / verified | 16 of 16 / 16 | 16 of 16 / 17 | 16 of 16 / 17 |
| cost per query | $0.00302 | $0.00248 | $0.00250 |
| `tool_set_ok` | 15 of 17 | 14 of 17 | 15 of 17 |

- **The rule's own effect is not distinguishable from noise.** Step structure is identical in 14 of 17 items with the rule on and off. It differs on three: `qa_0062` (3 search steps instead of 4, p50 7,458 to 5,939 ms), `qa_0063` ([1, 2] instead of [1, 1, 1] call steps) and `qa_0034` (the rule-on run used the calculator, the control did not, which is why `tool_set_ok` reads 14 against 15). On everything else, agent model calls (3.06 against 3.00), batched share (44.7% against 48.9%) and latency (3,205 against 3,199 ms) the two runs agree within what one stochastic re-run moves.
- **What changed the trajectories is the Phase 2 wording, not the rule.** Against `contract-v3` the control already shows 0 rejected calls, agent model calls 3.82 to 3.06, `qa_0062` answering, and 44.7% of calls issued in batched steps. The Phase 2 text tells the model to "make one call per figure and year ... repeat the concept", which is itself an instruction to batch the two-year lookups. This is the reason the control mattered: the first run alone, with the fix and the rule both on, would have credited the rule with all of it.
- **Where batching is still not happening:** `qa_0061` (headcount of the five companies) and `qa_0062` (incorporation across five companies) issue 3 to 4 SERIAL `search_filings` calls (one step each) in both runs; `qa_0063`, `qa_0068`, `qa_0071` call `list_available_companies` as a separate first step. The multi-ticker tool `compare_companies` takes a comma-separated ticker list and was never used on these items. The rule's sentence "prefer one lookup_financial_fact or compare_companies call per company over repeated searches" is ambiguous (compare_companies is already one call for several companies), which is likely why it did not move `qa_0061`/`qa_0062`. This is the one targeted wording change worth a measured iteration (iteration 1 of the at most two allowed): say that for untagged facts about several companies the model should make ONE `compare_companies` call listing every ticker, and that tagged figures get one `lookup_financial_fact` per company in the same step.
- Caveat for every wording iteration: ITEMS_B is both where the rule is tuned and where it is evaluated (R2). The full 71-item run in Phase 3.4 is the regression check on the other 54 items.

**Multi-turn probe built (Phase 4.3, no spend yet)**: `eval/multi_turn_probe.json` (8 conversations, 19 questions, 11 follow-up turns), `eval/run_multi_turn_probe.py`, 16 tests. Every ground-truth figure was read from `data/facts.sqlite` (fact ids cited) and a test re-checks all of them; one follow-up question was reworded so no two questions in the file are identical. Plan: 30 requests (19 with memory, 11 follow-ups in isolation; the first turns are the same request in both modes so the isolation run reuses them), expected $0.075 actual, $0.0938 counted.

NEXT STEP: rule wording iteration 1 (commit, then run `b-rule-v2` on ITEMS_B with the rule on; expected counted cost $0.0531, ledger $0.1656 + $0.0531 = $0.2187).

### 2026-10-04 23:50 Phase 3.3: rule wording v2 (iteration 1 of 2) moves exactly the items it was written for

`b-rule-v2` (`eval/results/b-rule-v2-2b81f7712548.json`, git `7438a47`, clean tree, `prompt_version sha256:95cd45597bbc`, `agent_batch_rule on`): ITEMS_B, 17 items; actual $0.0379, counted $0.0474, ledger total **$0.2130**. Quiet machine. Comparisons: `eval/tool_metrics/b-rule-v2-vs-b-control-subset.json` (against the rule-off control) and `eval/tool_metrics/b-rule-v2-2b81f7712548-subset.json` (against `contract-v3`).

| ITEMS_B, 17 items | `contract-v3` | `b-control` (fix, rule OFF) | `b-rule-v2` (fix, rule v2 ON) |
|---|---|---|---|
| tool calls | 59 | 47 | **39** |
| model steps that made the calls (t0 windows) | n/a | 35 | **26** |
| calls issued in a batched step | n/a | 21 of 47 (44.7%) | **23 of 39 (59.0%)** |
| agent model calls per query | 3.82 | 3.06 | **2.53** |
| latency p50 / mean (nearest-rank, terminal failure excluded) | 4,586 / 5,283 ms | 3,205 / 4,133 ms | **2,933 / 3,970 ms** |
| cost per query | $0.00302 | $0.00248 | **$0.00223** |
| calls rejected | 5 of 59 | 0 of 47 | 0 of 39 |
| `first_tool_ok` / `tool_set_ok` | 15 of 17 / 15 of 17 | 15 of 17 / 14 of 17 | 17 of 17 / 16 of 17 |
| `figure_primary` / verified / terminal failures | 16 of 16 / 16 / 1 | 16 of 16 / 17 / 0 | 16 of 16 / 17 / 0 |

- **What moved, item by item (same 17 items, rule off to rule v2):** `qa_0062` (incorporation across five companies) four serial searches to ONE `compare_companies` call listing all five tickers, answer still correct (Apple California, Microsoft Washington; p50 7,458 to 5,961 ms); `qa_0061` (headcount of five) `list_available_companies` plus three serial searches to `list_available_companies` plus ONE `compare_companies` call; `qa_0063` three steps to one step of two parallel lookups; `qa_0068` and `qa_0071` no longer call `list_available_companies` first, one step instead of two. The other 12 items have the same step structure as the control, except `qa_0034` (the control and v2 used no calculator, v1 did: model variation). `qa_0063`, `qa_0068`, `qa_0071` answers are still the ground-truth figures.
- **Why this is not the same finding as v1:** v1 changed 3 items by one step each and moved nothing in the aggregate; v2 changed the five items whose wording (`compare_companies` once, with a list) addressed them, and agent model calls fell 3.06 to 2.53 against the control, with the answers unchanged on every figure item.
- **Caveats.** (1) One run per configuration, 17 items, 5 of which moved; the run-to-run variation of one configuration is about 12% on p50 (two committed runs of the shipped configuration) so the latency difference 3,205 to 2,933 ms (8.5%) is within it and is NOT a finding; the model-call and step counts are structural and are the evidence. (2) The wording was written after looking at which of these same items did not batch, so ITEMS_B is the set it was tuned on (R2): the other 54 benchmark items are the regression check in the full run. (3) `qa_0062` has no figure in its ground truth, so the figure check cannot score it; its answer was read by hand against the ground truth.

Decision: one iteration is enough (the second allowed iteration is not needed); the full 71-item confirmation run uses rule v2 ON with the Phase 2 fix, label `upgrade-v1`, and its default-on gate is evaluated against `contract-v3` AND against the rule's own controlled evidence above (the vs-`contract-v3` gate in section 3.4 cannot separate the rule from the wording fix, so the rule ships on only if it also beats the rule-off control, which v2 does and v1 did not).

NEXT STEP: commit these results, then `upgrade-v1`, the full 71-item run (expected counted cost 71 x $0.0025 x 1.25 = $0.2219, ledger $0.2130 + $0.2219 = $0.4349), on a QUIET machine (no tests or builds during the run: the latency gate reads from it).
