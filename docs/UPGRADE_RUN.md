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

### 2026-10-04 22:20 Phase 0.2: reading done

Read: `agent/*.py`, `api/main.py`, `mcp_server/server.py`, `retrieval/facts.py`, `eval/run_eval.py`, `tests/test_meter.py`, `test_tool_wiring.py`, `test_mcp_contract.py`, `test_query_stream.py`, `test_query_meta.py`, `test_api_edges.py`, `conftest.py`, ROADMAP, DECISIONS, and EVALUATION "Cost and latency", "Output contract" and finding 11. Facts re-verified against the code (section 4 of the prompt): all hold. Details that matter later:

- `QueryMeter.run_inline = True` (callbacks run on the calling thread), but a ToolNode runs simultaneous calls on worker threads, so the appends still need a lock.
- `tests/test_query_stream.py` and `test_api_edges.py` call `api_main._sse_event_stream(request, question)` and the fake agents' `ainvoke(payload, config=None)`; changes to the API's internal helpers must keep those call shapes working.
- `eval/run_eval.py` regenerates `eval/results/LEADERBOARD.md` at the end of every run (`write_leaderboard`). That is a generated file, and Phase 5 regenerates it on purpose; between runs I will leave any leaderboard change uncommitted and commit it once, in Phase 5, so each run's `git_dirty` flag stays honest.
- `conftest.py` forces `VERIFY_MODE=off` for tests.
- The harness's cache key hashes the system prompt only (prompt trap, section 4.5); every experiment uses its own `--cache-file` and `--label`.

Decisions so far: none needed.

NEXT STEP: commit this journal (Phase 0.3), then Phase 1.2 (blind tool labels) BEFORE anything opens a results file's `tools_used` / `meter.tool_calls`; then Phase 1.1 (meter), 1.3, 1.4.
