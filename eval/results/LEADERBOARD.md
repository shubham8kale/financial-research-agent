# Leaderboard

Regenerated 2026-09-28T00:52:34+00:00 by `python -m eval.leaderboard` from every results file in this directory. Do not edit by hand.

Rows are comparable only within a table and only at the same benchmark version. Judge-scored means count a terminal failure (empty answer, recursion limit) as 0.

## Generation runs (schema 3: per-chunk contexts)

| run | date | agent | judge | n | faithfulness | answer_rel | context_recall | figure_exact | agent_hit | config | file |
|---|---|---|---|---|---|---|---|---|---|---|---|
| _none yet_ | | | | | | | | | | | |

`figure_exact`: share of items whose answer contains every ground-truth figure (deterministic, no judge). `agent_hit`: share of labelled items where any relevant chunk appeared in the agent's tool observations.

## Retrieval runs (retriever alone, no LLM)

| run | date | mode | k | ticker_filter | n | hit@5 | recall@5 | mrr | ndcg@5 | recall@25 | p50 ms | config | file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| retrieval-dense-1e17cf5b5eab | 2026-09-28 | dense | 25 | no | 71 | 0.5070 | 0.4894 | 0.3569 | 0.3729 | 0.6796 | 17.5000 | `1e17cf5b5eab` | [retrieval-dense-1e17cf5b5eab.json](retrieval-dense-1e17cf5b5eab.json) |

## Legacy generation runs (schema 2: contexts scored as observation blobs)

Kept for the record. `context_recall` here scored each tool observation as one blob of k passages, so it is not comparable with the schema-3 table above.

| run | date | agent | judge | n | faithfulness | answer_rel | context_recall | file |
|---|---|---|---|---|---|---|---|---|
| compare-fix10-c1ef2b0 | 2026-09-11 | gemini-3.1-flash-lite | google/gemini-3.6-flash | 10 | 0.6500 | 0.7375 | 0.9000 | [compare-fix10-c1ef2b0.json](compare-fix10-c1ef2b0.json) |
| compare-fix10-repeat-c1ef2b0 | 2026-09-11 | gemini-3.1-flash-lite | google/gemini-3.6-flash | 10 | 0.6333 | 0.7237 | 0.9000 | [compare-fix10-repeat-c1ef2b0.json](compare-fix10-repeat-c1ef2b0.json) |
| baseline66-af83fa6 | 2026-09-10 | gemini-2.5-flash-lite | google/gemini-3.6-flash | 66 | — | — | — | [baseline66-af83fa6.json](baseline66-af83fa6.json) |
| crossjudge20-af83fa6 | 2026-09-10 | gemini-3.1-flash-lite | groq/openai/gpt-oss-120b | 20 | 0.7726 | 0.7500 | 0.7250 | [crossjudge20-af83fa6.json](crossjudge20-af83fa6.json) |
| rerun66-af83fa6 | 2026-09-10 | gemini-3.1-flash-lite | google/gemini-3.6-flash | 66 | 0.8813 | 0.7625 | 0.6970 | [rerun66-af83fa6.json](rerun66-af83fa6.json) |
| smoke8-69c426f | 2026-09-10 | gemini-2.5-flash | groq/openai/gpt-oss-120b | 8 | — | — | — | [smoke8-69c426f.json](smoke8-69c426f.json) |
| temporal5-07c8597 | 2026-09-10 | gemini-3.1-flash-lite | google/gemini-3.6-flash | 5 | 0.8000 | 0.8467 | 0.8000 | [temporal5-07c8597.json](temporal5-07c8597.json) |
| validation-E-69c426f | 2026-09-10 | gemini-3.1-flash-lite-preview | groq/openai/gpt-oss-120b | 8 | — | — | — | [validation-E-69c426f.json](validation-E-69c426f.json) |
| validation-E-rescore-69c426f | 2026-09-10 | gemini-3.1-flash-lite-preview | groq/openai/gpt-oss-120b | 3 | — | — | — | [validation-E-rescore-69c426f.json](validation-E-rescore-69c426f.json) |

## Incomplete runs (no aggregates)

- `empty-probe-gemini-3.1-flash-lite-af83fa6.json` — --generate-only: generation checkpointed, judge pass not run
- `empty-probe-gemini-3.6-flash-af83fa6.json` — --generate-only: generation checkpointed, judge pass not run
