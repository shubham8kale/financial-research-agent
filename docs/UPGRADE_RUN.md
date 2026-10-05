# FRA upgrade run: journal, ledger and report

Prompt: `FRA upgrade prompts/FRA_UPGRADE_PROMPT.md` (outside this repo). This file is the run's journal (appended after every step), its spend ledger, and, at the end, its REPORT.

## REPORT

**Run:** 2026-10-04, unattended, on branch `main` (checked out at the start; no branch created, none switched; the two other local branches `review-fixes` and `upgrade-1-eval-v2` date from 09-28 and are not mine). **START_SHA `eda988efb5c5331bef5af9173a240ee7d683a3b3`.** Nothing was pushed (PUSH = false). 34 commits sit on top of START_SHA before this report's own commit: `git log --oneline eda988e..HEAD`. Final gates, all green: pytest 358 passed, flake8 clean, the three dry-runs, web lint, 16 Vitest tests, `npm run build`. The working tree is clean.

### What shipped, and each switch's default

| change | shipped as | default |
|---|---|---|
| **A. the dropped `concept`** | wording in the tool's first sentence, its parameter text, rule 7 and the MCP description; contract test pins it | on, unconditional (it repairs a defect) |
| A. tool-call evaluation | the meter records each call's arguments, start offset and error (thread-safe); `eval/tool_metrics.py`; 71 blind labels in `eval/benchmark_tools.json` (a sidecar, not in `benchmark_version`); a `tool_schema_version` fingerprint and `agent_batch_rule` in every results file's config | always |
| **B. a race on first use** | every lazily built singleton (vectorstore, retriever, reranker, sparse index, cross-encoder, fact store) built once under a double-checked lock; 6 of 8 racing threads had failed | on, unconditional |
| B. batching rule | `AGENT_BATCH_RULE=on\|off` appends rule 9 to the system prompt (direct and MCP agent) | **off** |
| **C. thread memory** | `agent/memory.py`; optional `thread_id` on `/query` and `/query/stream`; `meta.thread_id` and `meta.thread_turns` | `THREAD_MEMORY=on` (acts only on a request with a `thread_id`), `THREAD_MEMORY_MAX_TURNS=6`, `THREAD_MEMORY_TTL_SECONDS=1800`, `THREAD_MEMORY_MAX_THREADS=500` |
| C. web | one thread id per page load in React state, a "New chat" control, the line "Follow-up questions are remembered for this session. Memory is not saved and clears when the server restarts." | on |
| C. probe | `eval/multi_turn_probe.json` (8 conversations, 11 follow-ups, ground truth by fact id) and `eval/run_multi_turn_probe.py` | n/a |
| harness | the `--generate-only` log line and resume hint name the cache file the run used | n/a |
| docs | `eval/EVALUATION.md` ("Tool-call quality", findings 23 to 25, limitations 20 to 26), README, ROADMAP (three Done entries), `docs/DECISIONS.md` (eight entries), `.env.example` (five variables), leaderboard regenerated, this file | n/a |

An unknown value of `AGENT_BATCH_RULE` or `THREAD_MEMORY` stops the server at startup (as `VERIFY_MODE` already does) instead of silently choosing.

### What did not ship, and why

* **The batching rule as the default.** Every gate the plan set held against `contract-v3`, and one answer was wrong: `qa_0008` (Apple's reportable segments) is answered wrongly **4 of 4** times with the rule on (ten parallel lookups over product categories, taken for segments) and correctly in all 6 runs without it. No judge-free metric sees it (the ground truth has no figure and every figure in the answer was retrieved); reading the 30 changed answers found it. The rule stays behind the switch, off (finding 24).
* **Candidates (b), an argument schema, and (c), a tolerant tool.** Not needed: the wording passed its gate. The marker helpers and the metric that counts them stay in the code. A default `concept` was ruled out in advance.
* **Any latency claim.** Work that did not change got 15% faster between two runs on the same index, as much as the whole p50 difference.
* **A judged run** (JUDGED_RUNS_ALLOWED = 0); **persistent or cross-session memory**; **deployment of any kind**; **a push**.

### Before and after, 71 items, generation only, `gemini-3.1-flash-lite`

`contract-v3` is the plan's baseline and is on the FIRST 67,521-chunk index; `reindex-v3` is the same prompt and tools on the rebuilt index and is the like-for-like baseline. "Shipped" is the new default (wording fix on, rule off), run `upgrade-control`, rebuilt index.

| | `contract-v3` (first index) | `reindex-v3` (rebuilt, no change) | **shipped** |
|---|---|---|---|
| tool calls / rejected | 131 / 7 | 129 / 7 | **119 / 0** |
| agent model calls per query | 2.68 | 2.65 | **2.49** |
| verified / refused / terminal failures | 70 / 0 / 1 | 70 / 0 / 1 | **71 / 0 / 0** |
| `figure_primary` | 45 of 45 | 46 of 46 | 46 of 46 |
| calls issued in a batched step | n/a | n/a | 23 of 119 (19.3%) |
| `first_tool_ok` / `tool_set_ok` (labels not revised; baselines' first call is completion order) | 67 / 67 of 71 | 67 / 66 of 71 | 65 / 64 of 71 |
| cost per query | $0.002067 | $0.002031 | $0.001987 |
| answers that became wrong (hand read of every changed answer vs `contract-v3`) | n/a | not read | **0** of 35 changed |

Not shipped, for the record (`upgrade-v1`, rule on): 121 calls, 0 rejected, 2.37 model calls, 70 verified, 1 terminal failure, 28.9% of calls batched, **1 wrong answer in 30 changed**. **Memory** (`eval/probes/multiturn-memory-v1-3b4d5b73e61d.json`): **11 of 11 follow-up turns correct with memory, 3 of 11 without; N = 11** in 8 conversations (the 3 are lucky defaults), all 30 requests verified. Latency is deliberately absent: see the do-not-quote list.

### Spend

Counted ledger total **$0.6518** against SPEND_CAP_USD $1.00 (under the $0.75 aim); the repo meter's own total is $0.5214 (counted = x1.25). Nine ledger rows (eleven paid runs: one row is three single-item repeats), all `--generate-only` or the probe, none a judge pass. Against the roughly $5 of credit: about $4.35 left by this ledger's counting; the meter cannot see retried or interrupted requests, so check Google billing before the next paid run. Tracing was forced off for every run so no non-Gemini API was called (the first live run made public Hugging Face Hub metadata fetches for the cached reranker, no spend; later runs set `HF_HUB_OFFLINE=1`).

### Files changed (66 files, +83,736 / -89; 43 added, 23 modified)

Code: `agent/` (`financial_agent.py`, `mcp_agent.py`, `meter.py`, `observations.py`, new `memory.py`), `api/main.py`, `mcp_server/server.py`, `retrieval/` (`facts.py`, `rerank.py`, `retriever.py`), `eval/` (`run_eval.py`; new `tool_metrics.py`, `run_multi_turn_probe.py`, `benchmark_tools.json`, `multi_turn_probe.json`), `web/` (`app/page.tsx`, `lib/api.ts`, new `lib/thread.ts`, two new test files, `README.md`). Tests: 11 new files and 6 extended. Evidence, all new files: 10 results files under `eval/results/` (plus the regenerated `LEADERBOARD.md`, the only existing file there that changed), 13 under `eval/tool_metrics/`, 1 under `eval/probes/`. Docs: `eval/EVALUATION.md`, `README.md`, `ROADMAP.md`, `docs/DECISIONS.md`, `.env.example`, this file. Untouched: `eval/benchmark.csv`, `benchmark_chunks.json`, `chunk_labels_overrides.json`, every other results file, `data/`, `hf-space/`, `requirements.txt`, `.github/workflows/`, `.env`.

### Tests

Python **224 to 358** (collected; three skip without the index or the fact table); web **3 to 16**.

### Known risks

* **Wording changed rule 7 and the tool description**, so everything generated is a new distribution; the manual judged workflow (`eval-judged.yml`, never clicked) was calibrated on the old prompt and has not been re-run (about $0.20 a click).
* **The plan's baseline is on another index.** Part of the movement against `contract-v3` is the index rebuild; `reindex-v3` is quoted beside it everywhere and the conclusions use it.
* **Memory is in process and per worker.** A restart or a second replica or uvicorn worker forgets it (the Space runs one worker); a long thread adds a history block of up to about 10,000 characters to each follow-up. The UI note says follow-ups are remembered, which is false against the currently deployed backend until the Space is synced.
* **The labels are blind but not independent** (author plus three language models); two of the four baseline first-tool misses are arguable.
* **`qa_0024`** loops on nine searches in some runs (terminal in `reindex-v3` and the rule-on run); not addressed.
* **`.env.example`** had five variables appended without reading the file (the `tail` I printed to check showed four placeholder lines of its existing block); `.env` was never opened.
* The lock change is on the retrieval hot path; a warm call costs 39 ns and the new race tests fail on the old code.

### Needs the owner

1. **Review the commits** (`git log --oneline eda988e..HEAD`) and push them yourself. **Order matters: sync the Space first, then push**, because the web change tells users their follow-ups are remembered and the deployed backend does not do that until it is synced; a push to `main` also runs CI and probably auto-deploys the Vercel frontend. The Space sync and the Vercel deploy are manual and were not touched.
2. Decide whether to **click the manual judged workflow** once (the prompt wording changed; JUDGED_RUNS_ALLOWED was 0).
3. **Review the 71 labels** in `eval/benchmark_tools.json` (and the four first-tool misses of `contract-v3`).
4. Decide about **`AGENT_BATCH_RULE`**: it saves model calls (2.49 to 2.37 per query) and breaks `qa_0008`. Setting it on is a one-line env change.
5. Merge the appended block of **`.env.example`** where you want it.
6. **Check Google billing** (ledger: $0.52 of meter cost, $0.65 counted).
7. Know that the plan's `contract-v3` baseline is on the first index; the quoted comparisons lean on `reindex-v3` for that reason.

### Reproduce each result

```bash
# gates
python -m pytest --tb=short --strict-markers -p no:cacheprovider
flake8 . --max-line-length 120 --ignore E501,W503
python -m eval.run_eval --dry-run && python -m eval.run_retrieval_eval --dry-run && python -m eval.ci_gate retrieval --dry-run
# tool-call metrics from the committed results (no model, no network)
python -m eval.tool_metrics eval/results/upgrade-control-7c50eed7da71.json --baseline eval/results/reindex-v3-5b1deb95bdcc.json
python -m eval.tool_metrics eval/results/contract-v3-76b8f532c332.json
# a generation run (the shipped configuration; set LANGSMITH_TRACING=false to keep traces off)
VERIFY_MODE=strict LLM_MODEL=gemini-3.1-flash-lite RETRIEVAL_RERANK=true RETRIEVAL_FETCH_K=50 RETRIEVAL_TICKER_FILTER=inferred \
  AGENT_BATCH_RULE=off python -m eval.run_eval --generate-only --label <label> --cache-file eval/cache/<file>.json [--ids ...]
# the memory probe (about $0.05)
python -m eval.run_multi_turn_probe --dry-run
python -m eval.run_multi_turn_probe --label memory-v1
```

Each results file records the commit it ran at (`git_commit`, `git_dirty`), `prompt_version`, `tool_schema_version` and `agent_batch_rule`; the experiments before the final wording ran at older commits. The per-experiment commands and ids are in the journal below and in `eval/EVALUATION.md` ("Reproducing this").

### Undo the whole run

For a run that has not been pushed, from the repo: `git reset --hard eda988efb5c5331bef5af9173a240ee7d683a3b3`. After a push: `git revert --no-commit eda988efb5c5331bef5af9173a240ee7d683a3b3..HEAD` and commit. Either is yours to run; the run never ran a reset.



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
| 6 | 23:01 | `run_eval --generate-only --label upgrade-v1 --cache-file eval/cache/upgrade-v1.json` with `AGENT_BATCH_RULE=on`, rule v2 (git `cc21568`), all 71 items | 71 | $0.2219 | $0.1465 | $0.1831 | $0.3961 |
| 7 | 23:13 | `run_eval --generate-only --label upgrade-control --cache-file eval/cache/upgrade-control.json` with `AGENT_BATCH_RULE=off` (the shipped configuration: fix on, rule off; git `a143cfd`), all 71 items | 71 | $0.2219 | $0.1411 | $0.1764 | $0.5725 |
| 8 | 23:55 | three runs of `run_eval --generate-only --label q8-rule-on-{1,2,3} --ids qa_0008 --force` with `AGENT_BATCH_RULE=on` (git `7fd642d`, `546e6bd`, `5a79ed2`) | 3 | $0.0094 | $0.0120 | $0.0150 | $0.5875 |
| 9 | 23:27 | `python -m eval.run_multi_turn_probe --label memory-v1` (30 requests through the real `/query` path; git `342149a`) | 30 | $0.0938 | $0.0514 | $0.0643 | $0.6518 |
| | | (no other paid call; the run is finished) | | | | | |

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

### 2026-10-04 23:25 Phase 3.4: the full 71-item run with the fix and rule v2 ON (`upgrade-v1`); the formal gates pass; a manual answer review finds one regression the gates cannot see

`upgrade-v1` (`eval/results/upgrade-v1-0c01c6017b31.json`, git `cc21568`, clean tree, `prompt_version sha256:95cd45597bbc`, `agent_batch_rule on`, `tool_schema_version sha256:88e7a8918c7f`): all 71 items; actual $0.1465, counted $0.1831, ledger total **$0.3961**. Quiet machine. Tool metrics with the `contract-v3` comparison: `eval/tool_metrics/upgrade-v1-0c01c6017b31.json`.

| 71 items | `contract-v3` (09-28, the shipped configuration then) | `upgrade-v1` (fix + rule v2 ON) |
|---|---|---|
| tool calls | 131 | 121 |
| calls rejected | 7 of 131 (all `lookup_financial_fact`, 6 items) | **0 of 121** (lookups 0 of 60) |
| calls issued in a batched step | not measurable (8 items had more calls than tool steps) | 35 of 121 (28.9%); 12 of 71 items had a batched step; 13 had more calls than steps |
| agent model calls per query | 2.68 | **2.37** |
| `figure_primary` | 45 of 45 | 46 of 46 (the 45 plus `qa_0066`, whose ground truth was corrected on 09-28, after the contract-v3 snapshot); 45 of 45 on the original 45 |
| verified / refused | 70 / 0 | 70 / 0 |
| terminal failures | 1 (`qa_0062`) | 1 (`qa_0024`, a nine-search loop on a list question; it also hit the limit in `reindex-v3`) |
| `first_tool_ok` / `tool_set_ok` | 67 of 71 / 67 of 71 | 70 of 71 / 69 of 71 |
| latency p50 / p95 / mean, nearest-rank, terminal failure excluded (n=70) | 3,684 / 9,077 / 4,453 ms | 2,831 / 5,572 / 3,273 ms |
| cost per query / whole benchmark | $0.002067 / $0.1467 | $0.002063 / $0.1465 |

**The section 3.4 gates, evaluated literally against `contract-v3`: all hold** (`figure_primary` not below; verified 70 not below and refusals 0; terminal failures 1 not above 1; p50 latency 23% better, not worse; cost per query equal; invalid lookup calls 0 below 7). **I do not read that as a licence to turn the rule on, for two reasons found by looking, not by the gates.**

1. **A regression the gates cannot see: `qa_0008`.** "Which of Apple's reportable segments saw a decrease in net sales in fiscal 2025?" (ground truth: Greater China) was answered correctly in all five earlier runs that included it (`cost-v3`, `contract-v3`, `reindex-v3`, my `a-before` and `a-wording`, rule off) and WRONG in `upgrade-v1`: the rule made the model fan out TEN parallel `lookup_financial_fact` calls over Apple's product categories (iPhone, Mac, iPad, Wearables, Services, two years each), treat them as the "reportable segments" (they are geographic: Americas, Europe, Greater China, Japan, Rest of Asia Pacific) and answer "Wearables, Home and Accessories", verified. The figure check does not apply (the ground truth has no figure) and the contract verifies figures against the observations, which the model did retrieve, so nothing in `figure_primary`, verification or the tool metrics reflects it. The mechanism is the one a batching rule should be expected to have: "make one lookup per company or year in the same step" invites a fan-out where a narrative search was the right tool. I read every answer that changed between `contract-v3` and `upgrade-v1` (30 items) against its ground truth: `qa_0008` is the only one that became wrong; `qa_0014`, `qa_0035`, `qa_0047` are paraphrase-level changes that also differ between the committed runs, and the other 26 are the same answer in new words.
2. **The latency gain is mostly not the change.** 52 items used exactly the same tools in both runs (45 of them a single call); their p50 fell from 3,338 to 2,722 ms (median per-item ratio 0.84) with nothing about what they did changed, and the median `search_filings` call fell from 859 to 706 ms. About 16% of the latency difference is the day, not the code. Only structural counts (model calls, steps, batched calls) are evidence about the change; the latency difference is NOT quoted.

**Decision.** `AGENT_BATCH_RULE` ships with the default **off**. Evidence for the switch, all kept: the rule v2 does what it was written to do (ITEMS_B against the rule-off control: agent model calls 3.06 to 2.53, 47 to 39 calls, `qa_0062` four serial searches to one `compare_companies` call, answers unchanged on the figure items) and the full run shows 0 rejected calls, 2.68 to 2.37 model calls per query and no figure or verification regression; against that it produced at least one wrong, verified answer on the full benchmark. R3 says a change that does not clearly help is switched off and written up; R4 says the default of a switch that could regress answers is decided by a measured gate, and this gate (answer correctness read by hand) is not met. The wording fix of Phase 2 is unconditional (it repairs a defect) and is on.

**What is still missing for the default I am shipping.** Nothing has run the SHIPPED configuration (fix on, rule off) over all 71 items: `upgrade-v1` had the rule on. So the second full run is the same configuration with the rule OFF (the optional "second full run": ledger $0.3961 is under the $0.60 limit for it; counted cost expected $0.2219). It gives the full-benchmark numbers for what ships, a read of the answers for any regression of the wording fix, and, set against `upgrade-v1`, the rule's effect at full scale. I chose it over a pure repeat of `upgrade-v1` as the noise sample because noise is already measured twice (12% p50 between two committed runs, the 16% same-tools drift above).

NEXT STEP: commit these results; run `upgrade-v1-off` (label `upgrade-control`): all 71 items, `AGENT_BATCH_RULE=off`, quiet machine; then the multi-turn probe.

### 2026-10-04 23:35 CORRECTION to two earlier entries (22:33 and 22:55): the rejected calls are not a batching phenomenon

The 22:33 entry said that in 5 of the 6 failing `cost-v3` items the omission "happens inside a batch", and the 22:55 entry repeated that "the model omits `concept` inside a batched step". That was an inference from the order of the stored error messages, and it was wrong. Measured with start offsets (`a-before`, the unchanged code, 5 rejected calls): 3 were single-call steps (`qa_0005`, `qa_0018`, `qa_0041`) and 2 were inside one 2-call step (both in `qa_0053`). For the committed `cost-v3` run, 5 of the 6 failing items had no more calls than model steps (`qa_0005`, `qa_0008`, `qa_0018`, `qa_0021`, `qa_0041`), so they were not batched; only `qa_0053` was. The omission occurs on single calls too. Consequences: (1) the sentence in the rule's code comment and in `tests/test_batch_rule.py` that says the rejected lookups "happened inside batched steps" is corrected in the next commit (the rule still repeats the required-arguments reminder, now on the weaker ground that a batch must not make the omission more likely); (2) the commit message of `3d1f616` carries the same wrong sentence and is left as it is (not rewritten); (3) the Phase 3 reading "a batching rule could raise the rejection rate" stays a hypothesis that was tested and not borne out (0 of 47, 0 of 39 and 0 of 121 calls rejected with the rule on or off after the Phase 2 wording).

### 2026-10-04 23:45 Phase 3.4, second full run: the SHIPPED configuration (fix on, rule off), `upgrade-control`

`upgrade-control` (`eval/results/upgrade-control-7c50eed7da71.json`, git `a143cfd`, clean tree, `prompt_version sha256:e96ec6393c90`, `agent_batch_rule off`): all 71 items, quiet machine; actual $0.1411, counted $0.1764, ledger total **$0.5725** (under the $0.60 limit the prompt sets for a second full run, which it was started under: $0.3961 at the time). Tool metrics: `eval/tool_metrics/upgrade-control-7c50eed7da71.json` (against `contract-v3`) and `eval/tool_metrics/upgrade-v1-vs-upgrade-control.json` (rule on against rule off).

| 71 items | `contract-v3` | **`upgrade-control` (what ships: fix on, rule off)** | `upgrade-v1` (fix + rule v2 on) |
|---|---|---|---|
| tool calls / rejected | 131 / 7 | **119 / 0** | 121 / 0 |
| calls issued in a batched step | n/a | 23 of 119 (19.3%), 10 items | 35 of 121 (28.9%), 12 items |
| agent model calls per query | 2.68 | **2.49** | 2.37 |
| `figure_primary` | 45 of 45 | **46 of 46** (45 of 45 on the original 45) | 46 of 46 |
| verified / refused / terminal failures | 70 / 0 / 1 (`qa_0062`) | **71 / 0 / 0** | 70 / 0 / 1 (`qa_0024`) |
| `first_tool_ok` / `tool_set_ok` (labels as written, not revised) | 67 of 71 / 67 of 71 | 65 of 71 / 64 of 71 | 70 of 71 / 69 of 71 |
| latency p50 / p95 (nearest-rank) | 3,684 / 9,077 ms (70 answered) | 2,839 / 6,940 ms (71) | 2,831 / 5,572 ms (70) |
| cost per query | $0.002067 | $0.001987 | $0.002063 |
| answers that became wrong (hand read of every changed answer) | n/a | **0** (35 changed, 36 identical to `contract-v3`) | **1** (`qa_0008`, of 30 changed) |

- **Every answer that changed was read against its ground truth.** In the shipped configuration `qa_0008` is correct again (Greater China, $64,377 million against $66,952 million), `qa_0024` gives a partial answer (it says the filing does not list three sources; the ground truth lists four; the committed run also fell short and this is the item that hit the recursion limit with the rule on), and the other 33 are the same answer in other words or the same figures. Nothing became wrong.
- **Noise.** `qa_0024` hit the recursion limit in `reindex-v3` and in `upgrade-v1`, answered in `upgrade-control` and in `contract-v3`; `qa_0062` hit it in `contract-v3` and `cost-v3`, answered in all three runs of this upgrade. The terminal-failure count of this configuration is 0 to 1 per run and the failing item moves: not evidence for or against any change.
- **Rule on against rule off at full scale** (`upgrade-v1` against `upgrade-control`): agent model calls 2.37 against 2.49, batched calls 28.9% against 19.3%, `first_tool_ok` 70 against 65 of 71 (the rule makes the model skip a `list_available_companies` call it did not need), comparative stratum model calls 2.25 against 4.00 (n = 4), p50 latency identical (2,831 against 2,839 ms); and one wrong answer against none. So: a real, structural, small efficiency gain and one regression a judge-free metric cannot see; the default stays off, and one more cheap measurement is made below to see whether the `qa_0008` failure repeats.

NEXT STEP: re-run `qa_0008` alone with the rule ON three times (distinct labels and caches, `--force`; expected counted cost 3 x $0.0031 = $0.0094, ledger $0.5725 + $0.0094) to tell a systematic regression from a one-off; then the multi-turn probe (expected counted $0.0938); then Phase 5.

### 2026-10-04 23:57 The `qa_0008` regression is systematic: wrong 4 of 4 with the rule on, correct 6 of 6 with it off

Three more runs of `qa_0008` alone with the rule ON (`eval/results/q8-rule-on-{1,2,3}-88ad3e4f5d61.json`, each on a clean tree): the same ten parallel `lookup_financial_fact` calls, one per Apple product category and year, and the same wrong answer ("the Wearables, Home and Accessories segment was the only reportable segment to see a decrease") every time. With the rule ON `qa_0008` is therefore wrong 4 of 4 (the three repeats and `upgrade-v1`); with the rule OFF it is correct in all 6 runs that include it (`cost-v3`, `contract-v3`, `reindex-v3`, `a-before`, `a-wording`, `upgrade-control`). It is a deterministic consequence of the rule on this item, not noise. Cost: the ten-call fan-out is token-heavy (about $0.0040 per run against $0.0031 for the average query), so the three runs cost $0.0120 actual, $0.0150 counted (more than the $0.0094 I had estimated); ledger **$0.5875**.

Decision unchanged and now firmly supported: `AGENT_BATCH_RULE` ships **off**. (The hand read of the full run found it; this repeat establishes it. Judge-free metrics, verification and a $1.10 judge pass would not necessarily have: the answer's figures are all in the observations.)

NEXT STEP: run the multi-turn probe (`python -m eval.run_multi_turn_probe --label memory-v1`; 30 requests, expected counted $0.0938; ledger $0.5875 + $0.0938 = $0.6813 against the $1.00 cap; the unit and API tests pass: 356). Then Phase 5 documentation.

### 2026-10-04 23:32 Phase 4.3: the multi-turn probe, `memory-v1`

`eval/probes/multiturn-memory-v1-3b4d5b73e61d.json` (git `342149a`, clean tree, `prompt_version sha256:e96ec6393c90`, rule off, `VERIFY_MODE=strict`, shipped retrieval configuration, tracing off): 8 conversations, 19 questions, 30 requests (19 with memory on one thread per conversation, 11 follow-ups asked alone; the first turns are the same request in both modes so the isolation run reuses them), through the real `/query` code path with the real agent and contract. Actual $0.0514, counted $0.0643, ledger total **$0.6518** (cap $1.00, aim under $0.75).

- **With memory: 11 of 11 follow-up turns answered correctly** (the 8 first turns also 8 of 8; all 30 requests verified, 0 refused, 0 HTTP errors); `meta.thread_turns` read 0, 1, 2 along each conversation as it should.
- **Without memory: 3 of 11.** And the 3 are not a counter-example, they are guesses that happened to match: "And Microsoft's?" and "And Alphabet's?" asked alone were answered with the company's total net sales (the agent defaulted to the most common metric), and "And what were its Services net sales?" was answered for Apple (the only company with a Services segment). The other 8 were wrong or empty: "What about its diluted EPS?" got Apple's, "And in 2024?" and "What about 2024?" got every company's 2024 net sales, "By what percentage did it grow?" and "How much was that in 2024?" asked what was meant, "How about Alphabet's?" gave revenue where operating income was meant, and "What was its operating income that year?" gave Apple's FY2025.
- **N is 11 follow-up turns in 8 conversations.** That is enough to show the mechanism works end to end (the history reaches the model, the contract still verifies every answer against this turn's observations, no figure was served from an earlier turn: every answer is `verified`) and not enough to estimate a rate or compare two systems. It was written by the author, who also built the memory.

NEXT STEP: commit the probe results; Phase 5 documentation (EVALUATION.md "Tool-call quality", findings 23 to 25, limitations 20 onward, "Reproducing this"; README minimal edits; ROADMAP three Done entries; leaderboard regenerated; this file's "Resume-safe numbers" and "Do not quote"); then an adversarial review of the whole diff and every documented number, then Phase 6.

### 2026-10-04 23:50 Phase 5: documentation written; one more correction

Written: `eval/EVALUATION.md` ("Tool-call quality", findings 23 to 25, limitations 20 to 26, a summary paragraph, "Reproducing this"), `README.md` (API `thread_id`, observability, project structure, test counts, two known limitations), `ROADMAP.md` (three Done entries), `docs/DECISIONS.md` (eight entries), `.env.example` (the five new variables appended; I never read the file, but the `tail` I printed to check my append showed four lines of the existing block, which holds placeholders only), `eval/results/LEADERBOARD.md` regenerated once with `python -m eval.leaderboard` (the ten new generate-only runs appear under "Incomplete runs", like `cost-v3` and `contract-v3`).

Correction to the 23:10 entry: it says "48 concurrent `search_filings` calls (12 workers, 3 rounds)". The measured counts are 48 calls with 8 workers before the fix (three rounds of 16) and 72 calls with 12 workers after it (three rounds of 24), and 16 and 24 concurrent `compare_companies` calls (two rounds of 8 and of 12). The documents use the corrected counts.

### 2026-10-05 00:05 Review of the whole diff (11 read-only reviewers, 6 findings re-checked by skeptics) and what it changed

A read-only workflow reviewed the code, the API and memory, the eval instrument, every number in the new documents, rules compliance and the tests. **No blocker.** Real findings, all fixed in the commit that follows this entry:

- **The baseline was on a different index (the one that mattered).** `cost-v3` and `contract-v3`, the baselines the plan names, are 09-28 runs on the FIRST 67,521-chunk index; every run of this upgrade is on the rebuilt 4,783-chunk index. My comparisons credited the Phase 2 wording with effects the index rebuild had already produced: `qa_0062` answering (it already answered in `reindex-v3`, the same prompt and tools on the rebuilt index, as finding 22 says), 59 to 55 calls and 3.82 to 3.65 model calls on the 17 items, and 2.68 to 2.65 over all 71. The index-matched baseline is `reindex-v3`; its tool metrics are now committed for the full set and for `ITEMS_A` and `ITEMS_B` (`eval/tool_metrics/reindex-v3-5b1deb95bdcc-items_a.json`, `-items_b.json`) and every table in `eval/EVALUATION.md` carries it beside `contract-v3`. What survives: 0 rejected calls against 7 of 129 on the same index, 2.65 to 2.49 model calls, the `ITEMS_A` same-day control, the whole rule-on against rule-off comparison (same day, same index), and the `qa_0008` finding. The latency "day, not the code" result also had a confound in the other direction and is restated against `reindex-v3`: unchanged work got 15% faster on the same index.
- **"12% run-to-run noise" was one pair of runs that differ in index.** Reworded everywhere; the same-work 15% is the nearer estimate.
- Stale test counts in four places of the README and one of `web/README.md` (222 and 3 where the repo has 358 Python and 16 web tests); the README says the UI states the idle expiry and replica limit (it does not), and that only verified answers are remembered (verification-skipped answers are too); the changed-answer count, the `qa_0062`/`qa_0024` sentence, the "about 100 concurrent calls" claim (120 and 40), an unmeasured 2-vCPU claim and an undefined "(R2)" are corrected; "does most of the batching" (ROADMAP, DECISIONS, finding 24) had no like-for-like measurement and is removed; "a judge would have scored it too" is replaced by "a judge pass was not run".
- Code: the `system_prompt()` docstring and a test header said the off-prompt "is the one it always was" (rule 7 changed); `safe_args` could raise on an odd text form; `THREAD_MEMORY=disabled` silently left memory ON, so an unknown value is now an error (like `AGENT_BATCH_RULE` and `VERIFY_MODE`); two weak test assertions fixed (a self-comparison; a lock test that would hang instead of failing). Test count 356 to 358.
- Not changed, noted: the calls-over-steps heuristic counts a nine-search recursion-limit item as batched (that is how the plan's 8 items are defined, and `qa_0062` is one of them); the probe runner caches a failed request and does not retry it, and its spend cap restarts on resume (a failed paid request is not re-bought blindly); `tool_schema_version` is hashed into the config but not into the cache key, by design; ledger times in this file are approximate local times, a few minutes off the results files' own timestamps.

## Resume-safe numbers

Each number, its n, its definition and the file it comes from. `tm/` is `eval/tool_metrics/`, `res/` is `eval/results/`.

| number | n | definition | source |
|---|---|---|---|
| 7 of 131 tool calls rejected (6 items); 8 items with more calls than model steps; on the rebuilt index (`reindex-v3`, same prompt and tools) 7 of 129 and 8 items | 71 items | calls with `error` true over all calls; steps = agent model calls - 1; `contract-v3` and `cost-v3` are on the FIRST index | `tm/contract-v3-76b8f532c332.json`, `res/cost-v3-2d69cde009fc.json`, `tm/reindex-v3-5b1deb95bdcc.json` |
| **0 of 119 tool calls rejected** (lookups 0 of 51) | 71 items | the shipped configuration (fix on, rule off) | `tm/upgrade-control-7c50eed7da71.json` |
| 5 of 20 lookups rejected unchanged, 0 of 15 after the wording | 12 items chosen for failing | `a-before` against `a-wording`; Fisher p = 0.057; a subset, selected on failures | `res/a-before-dfa6f50193ab.json`, `res/a-wording-5512347a3b12.json` |
| 3 of 5 rejected calls were single calls, 2 inside one batched step | 5 calls | start-offset windows, unchanged code | `tm/a-before-dfa6f50193ab.json` |
| agent model calls per query 2.65 on the same index before the change (`reindex-v3`; 2.68 on the first index) to 2.49 (rule on: 2.37) | 71 items | agent model calls = `llm_calls` minus the structuring attempts | `tm/reindex-v3-5b1deb95bdcc.json`, `tm/upgrade-control-7c50eed7da71.json`, `tm/upgrade-v1-0c01c6017b31.json` |
| 23 of 119 calls issued in a batched step (19.3%), 10 of 71 items; rule on 35 of 121 (28.9%), 12 items | 71 items | windows overlap, ends padded 50 ms | same files |
| shipped run: 71 verified, 0 refused, 0 terminal failures, `figure_primary` 46 of 46 (45 of 45 on the original 45) | 71 items | the harness's own definitions; 46 because `qa_0066` was corrected on 09-28 | `res/upgrade-control-7c50eed7da71.json` |
| `qa_0008` wrong 4 of 4 with the rule on, correct 6 of 6 with it off | 1 item, 10 runs | the answer names the wrong segment (Wearables) against Greater China; read by hand | `res/upgrade-v1-0c01c6017b31.json`, `res/q8-rule-on-{1,2,3}-88ad3e4f5d61.json` and the six rule-off runs |
| agent model calls 3.06 to 2.53 with rule v2; 47 to 39 calls; `qa_0062` four serial searches to one `compare_companies` call; the same 17 items on the rebuilt index before any change: 55 calls, 3.65 model calls | 17 items (`ITEMS_B`, where the rule was tuned) | rule off against rule v2, same day | `tm/b-rule-v2-vs-b-control-subset.json`, `res/b-control-e8f826600800.json`, `res/b-rule-v2-2b81f7712548.json`, `tm/reindex-v3-5b1deb95bdcc-items_b.json` |
| 6 of 8 racing threads failed on the first use of a cold process; 12 of 12 fine after the lock; a warm call 39 ns | 8 and 12 threads, real index | `Could not connect to tenant default_tenant`; results identical to sequential | journal entry 23:10; `tests/test_concurrent_search.py` |
| **11 of 11 follow-up turns correct with memory, 3 of 11 without** | **N = 11 follow-ups in 8 conversations** | `figure_match` primary figure; the 3 are lucky defaults | `eval/probes/multiturn-memory-v1-3b4d5b73e61d.json` |
| label audit: first-tool agreement 65, 64, 61 of 71; allowed set 61, 56, 62 | 71 items, 3 labellers | exact set equality with the author's first labels | `eval/benchmark_tools.json` (`audit`) |
| contract-v3 latency over 70 answered items: p50 3,684 ms, p95 9,077 ms (nearest-rank); 3,690 and 8,972 (interpolated) | 70 | the two percentile conventions | `tm/contract-v3-76b8f532c332.json` |
| the previous prompt and tools on two indexes about five hours apart: p50 3,684 ms (`contract-v3`, first index) and 3,230 ms (`reindex-v3`, rebuilt index), 12%; the median `search_filings` call 859 and 861 ms | 70 and 70 | nearest-rank p50 without the terminal failure; one pair that differs in index, not a noise measurement | `tm/contract-v3-76b8f532c332.json`, `tm/reindex-v3-5b1deb95bdcc.json` |
| items that did exactly the same work: 59 (46 single-call) against `reindex-v3`, p50 3,030 to 2,742 ms, median per-item ratio 0.85, median `search_filings` 861 to 713 ms; 55 against `contract-v3`, 3,360 to 2,704 ms, ratio 0.79 | 59 and 55 items | the day's drift on unchanged work | `tm/upgrade-control-7c50eed7da71.json`, `tm/reindex-v3-5b1deb95bdcc.json`, `tm/contract-v3-76b8f532c332.json` |
| spend: $0.5214 meter cost, $0.6518 counted of $1.00 | 9 ledger rows | the repo meter x 1.25 | the ledger above |
| tests: 224 to 358 Python, 3 to 16 web | | `pytest --collect-only`, `vitest run` | `pytest`, `npm test` |

## Do not quote

* **Any latency difference between two runs on different days.** p50 3,684 ms to 2,839 ms between `contract-v3` and the shipped run is not a result: against the same-index `reindex-v3`, 59 items that did the same work got 15% faster (21% against `contract-v3`), as much as the whole 12% p50 difference. Latency is quoted only as "not claimed".
* **12% as a noise floor.** It is `contract-v3` against `reindex-v3`, runs that also differ in index (and `qa_0062`'s terminal failure); the same-work 15% is the better estimate of a day's drift on one index.
* **`contract-v3` alone as the baseline of the wording fix.** It is on the first 67,521-chunk index; `qa_0062` answering, part of the fall in model calls (3.82 to 3.65 on `ITEMS_B`, 2.68 to 2.65 overall) and some latency came from the rebuilt index (finding 22). The index-matched baseline is `reindex-v3` (7 of 129 rejected, 2.65 model calls, `qa_0062` answering).
* **The ITEMS_A improvement as a rate** (5 of 20 to 0 of 15): the items were chosen because they failed, so it is overstated by regression to the mean, and p = 0.057. The full-benchmark 0 of 119 is the number.
* **ITEMS_B numbers as a general effect of the rule**: the second wording was written after looking at those 17 items; one run per configuration.
* **`b-rule-v1` against `contract-v3` as the rule's effect**: it changes the wording fix and the rule together; the rule-off control is the comparison.
* **The batching rule as "safe" or "unsafe"**, and the rule on the full run as an improvement: it passed every gate and made one answer wrong.
* **The 3 of 11 isolation successes as understanding**, and 11 of 11 as a rate: they are lucky defaults and a probe of eleven follow-ups written by the author.
* **Anything within the measured noise**: p50 differences under about 15%, cost per query ($0.002067 to $0.001987), terminal failures of 0 or 1 (the failing item moves from run to run), `first_tool_ok` 65 or 67 or 70 of 71 (a handful of `list_available_companies` calls under labels that were not revised).
* **`tool_set_ok` or `first_tool_ok` as answer quality**: they measure conformity to the prompt's tool rules.
* **`figure_primary` 46 of 46 against 45 of 45 as a gain**: the extra item is `qa_0066`, whose ground truth was corrected after `contract-v3` was generated.
* **The `trace_id` in these results files**: tracing was off, so none opens in LangSmith.

## Post-run (2026-10-05)

The owner reviewed the 35 commits of the upgrade run above and asked for `FRA upgrade prompts/FRA_POSTRUN_PROMPT.md` (outside the repo), which supersedes `FRA_CLEANUP_PROMPT.md`: a judge pass over the shipped answers, the tool-schema fingerprint in the cache key, removal of the tolerant-tool marker, and documentation corrections. Same rules as the upgrade run (sections 2, 3, 4 of its prompt): commit on `main`, no new branch, **no push**, stage by explicit path, no edit of an existing file under `eval/results/`, `eval/cache/`, `eval/tool_metrics/` or `eval/probes/`, the R8 gates before every commit that touches code, no key in any file or log, no non-Gemini API. The REPORT at the top of this file is not rewritten; corrections to it are made here. Commits carry no `Co-Authored-By` trailer (the owner's standing instruction for this repo).

```
JUDGED_SCORE_ONLY = 1
SPEND_CAP_USD     = 1.60   # counted (actual x 1.25), this run alone
PUSH              = false
```

### 2026-10-05 08:00 Post-run step 0: preflight

- Branch `main` (not detached), HEAD `b27d3d997b8e2347365158e129c661d372b73c40`, `git log --oneline eda988e..HEAD` = 35 commits, `git status --short` empty. Interpreter `C:\Users\1842s\anaconda3\envs\financial-agent\python.exe`, Python 3.11.15.
- **Stale locks.** `.git/index.lock` (0 bytes, 2026-10-05 07:22:43 local = 11:22 UTC) in this repo and `../hf-space/.git/index.lock` (0 bytes, 07:48:14 local = 11:48 UTC). `Get-Process git` returned nothing; waited 10 seconds; still nothing; deleted exactly those two files and nothing else in either `.git/`. The hf-space lock is the only thing touched outside this repo; nothing else there was read or changed. Neither lock had reappeared when the gates below finished (`Test-Path` false for both); a reappearance is journalled where it is seen.
- **Gates at HEAD, all green:** `pytest --tb=short --strict-markers -p no:cacheprovider` 358 passed (2 warnings) in 55 s; `flake8 . --max-line-length 120 --ignore E501,W503` exit 0; `eval.run_eval --dry-run`, `eval.run_retrieval_eval --dry-run` (71 items, 71 labelled, index of 4,783 chunks), `eval.ci_gate retrieval --dry-run` exit 0; `web`: `npm run lint` exit 0, `npm test` 3 files and 16 tests passed, `npm run build` exit 0. The tree was still clean afterwards.
- Interpreter checks that decide step 1: see the next entry.

### Post-run spend ledger

Counted = actual x 1.25 (the repo meter cannot see retried or interrupted requests). The cap of $1.60 applies to this run alone (the "this run" column); the upgrade run's counted $0.6518 is NOT carried into the cap and is shown in the last column so the owner sees cumulative spend against his credit (about $5 in all at the start of the upgrade run; the earlier ledger put about $4.35 of it left).

| # | time | command | items | expected (counted) | actual | counted (x1.25) | this run (cap $1.60) | cumulative incl. upgrade run's $0.6518 |
|---|------|---------|-------|--------------------|--------|-----------------|----------------------|----------------------------------------|
| 10 | 08:10 | `run_eval --score-only --judge-provider google --max-judge-calls 520 --label upgrade-judged --cache-file eval/cache/upgrade-control.json` (shipped configuration, `AGENT_BATCH_RULE=off`; judge `gemini-3.6-flash`; no agent call) | 71 | $1.31 (426 judge calls, $1.0229 actual on `reindex-v3`) | pending | pending | pending | pending |

NEXT STEP: step 1.1 preconditions (done offline, next entry), then run row 10.

### 2026-10-05 08:08 Post-run step 1.1: the offline preconditions of the judge pass all hold

A script outside the repo (`judge_preconditions.py`, no network, no model call, prints no environment value) set the shipped environment (`VERIFY_MODE=strict`, `LLM_MODEL=gemini-3.1-flash-lite`, `RETRIEVAL_RERANK=true`, `RETRIEVAL_FETCH_K=50`, `RETRIEVAL_TICKER_FILTER=inferred`, `AGENT_BATCH_RULE=off`, `RAGAS_LLM_MODEL=gemini-3.6-flash`, tracing off, Hub offline), built the keys the way `main()` builds them, and checked:

- `tool_schema_version()` = `sha256:88e7a8918c7f`; `_prompt_version()` with `AGENT_BATCH_RULE=off` = `sha256:e96ec6393c90`; both equal what `eval/results/upgrade-control-7c50eed7da71.json` records, as do its retrieval config, `contract_version` (`da6f5bea0c8b`), `agent_batch_rule` (`off`) and judge (`google/gemini-3.6-flash`).
- The key `main()` builds is `<id>|gemini-3.1-flash-lite|sha256:e96ec6393c90|rc=6a10e680|vc=strict-da6f5bea`; `eval/cache/upgrade-control.json` holds **71 of 71** ids under it.
- **71 of 71 cached answers are byte-identical** to `results[].answer` of the committed results file. The judge will score the committed answers.
- That results file is a `--generate-only` run (`run_status.complete` false), so `find_existing_result` does not match it and the judged run (config hash expected `7c50eed7da71`, file `upgrade-judged-7c50eed7da71.json`) will not be refused or overwrite anything.

Spend check (section 7 protocol): expected actual $1.05, counted $1.31 (the identical pass on `reindex-v3` made 426 judge calls for $1.0229 actual); this run's ledger total is $0.00; $0.00 + $1.31 is under the $1.60 cap. Row 10 above was written before the run.

NEXT STEP: run row 10 (step 1.3); the tree is committed first so the results file records `git_dirty` false.
