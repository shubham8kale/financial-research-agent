# Leaderboard

Regenerated 2026-09-28T18:00:36+00:00 by `python -m eval.leaderboard` from every results file in this directory. Do not edit by hand.

Rows are comparable only within a table and only at the same benchmark version. Judge-scored means count a terminal failure (empty answer, recursion limit) as 0.

## Generation runs (schema 3: per-chunk contexts)

| run | date | agent | judge | contexts | n | faithfulness | answer_rel | context_recall | figure_exact | figure_primary | agent_hit | cost/query | p50 latency | config | file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline-v3-87b6dd0 | 2026-09-28 | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.8263 | 0.7639 | 0.7183 | 0.6400 | 0.7600 | 0.4366 | — | — | `aea128d62403` | [baseline-v3-aea128d62403.json](baseline-v3-aea128d62403.json) |
| baseline-v3-plain-7b245f5 | 2026-09-28 | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk | 71 | 0.7042 | 0.7676 | 0.6901 | 0.6400 | 0.7600 | 0.4366 | — | — | `4a3f267adb41` | [baseline-v3-plain-4a3f267adb41.json](baseline-v3-plain-4a3f267adb41.json) |
| facts-v3-85f6dc0 | 2026-09-28 | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.9573 | 0.8935 | 0.8521 | 0.9000 | 1.0000 | 0.4225 | — | — | `70db17ff5e31` | [facts-v3-70db17ff5e31.json](facts-v3-70db17ff5e31.json) |
| rerank-v3-d7470bc | 2026-09-28 | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.9315 | 0.8688 | 0.8873 | 0.8600 | 0.9200 | 0.6620 | — | — | `764b3da65d36` | [rerank-v3-764b3da65d36.json](rerank-v3-764b3da65d36.json) |

`figure_exact`: share of items whose answer contains every ground-truth figure, context figures included (deterministic, no judge). `figure_primary`: share whose answer contains the figure the question asked for (the first non-year figure in the ground truth). `agent_hit`: share of labelled items where any relevant index chunk appeared in the agent's tool observations. `cost/query` and `p50 latency` are measured by the harness's own meter on runs generated after it existed (agent calls only; the judge is separate).

## Retrieval runs (retriever alone, no LLM)

| run | date | mode | k | ticker_filter | n | hit@5 | recall@5 | mrr | ndcg@5 | recall@25 | p50 ms | config | file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| retrieval-bm25-a308c41b72ea | 2026-09-28 | bm25 | 25 | none | 71 | 0.3521 | 0.3521 | 0.3036 | 0.2978 | 0.5704 | 241.5 | `a308c41b72ea` | [retrieval-bm25-a308c41b72ea.json](retrieval-bm25-a308c41b72ea.json) |
| retrieval-ci-dense-a0bd9c206430 | 2026-09-28 | dense | 25 | none | 71 | 0.5070 | 0.4894 | 0.3569 | 0.3729 | 0.6796 | 34.8 | `a0bd9c206430` | [retrieval-ci-dense-a0bd9c206430.json](retrieval-ci-dense-a0bd9c206430.json) |
| retrieval-ci-shipped-d8ca6cd2f348 | 2026-09-28 | dense | 25 | inferred | 71 | 0.6338 | 0.6056 | 0.4974 | 0.5085 | 0.7500 | 1511.2 | `d8ca6cd2f348` | [retrieval-ci-shipped-d8ca6cd2f348.json](retrieval-ci-shipped-d8ca6cd2f348.json) |
| retrieval-dense-1e17cf5b5eab | 2026-09-28 | dense | 25 | no | 71 | 0.5070 | 0.4894 | 0.3569 | 0.3729 | 0.6796 | 17.5 | `1e17cf5b5eab` | [retrieval-dense-1e17cf5b5eab.json](retrieval-dense-1e17cf5b5eab.json) |
| retrieval-dense-rerank-f100-84fdb1858a25 | 2026-09-28 | dense | 25 | none | 71 | 0.6197 | 0.6021 | 0.4818 | 0.4993 | 0.7007 | 1620.1 | `84fdb1858a25` | [retrieval-dense-rerank-f100-84fdb1858a25.json](retrieval-dense-rerank-f100-84fdb1858a25.json) |
| retrieval-dense-rerank-f100-tf-inferred-ad3ace274d29 | 2026-09-28 | dense | 25 | inferred | 71 | 0.6056 | 0.5880 | 0.4857 | 0.4967 | 0.7148 | 1719.4 | `ad3ace274d29` | [retrieval-dense-rerank-f100-tf-inferred-ad3ace274d29.json](retrieval-dense-rerank-f100-tf-inferred-ad3ace274d29.json) |
| retrieval-dense-rerank-f25-c5ce21431f88 | 2026-09-28 | dense | 25 | none | 71 | 0.5493 | 0.5317 | 0.4210 | 0.4341 | 0.6796 | 468.6 | `c5ce21431f88` | [retrieval-dense-rerank-f25-c5ce21431f88.json](retrieval-dense-rerank-f25-c5ce21431f88.json) |
| retrieval-dense-rerank-f50-1417a345c7b7 | 2026-09-28 | dense | 25 | none | 71 | 0.6197 | 0.5915 | 0.4802 | 0.4926 | 0.7218 | 794.5 | `1417a345c7b7` | [retrieval-dense-rerank-f50-1417a345c7b7.json](retrieval-dense-rerank-f50-1417a345c7b7.json) |
| retrieval-dense-rerank-f50-tf-inferred-8f6ef861d7e7 | 2026-09-28 | dense | 25 | inferred | 71 | 0.6338 | 0.6056 | 0.4963 | 0.5075 | 0.7500 | 892.8 | `8f6ef861d7e7` | [retrieval-dense-rerank-f50-tf-inferred-8f6ef861d7e7.json](retrieval-dense-rerank-f50-tf-inferred-8f6ef861d7e7.json) |
| retrieval-dense-tf-inferred-e3e48c7eb861 | 2026-09-28 | dense | 25 | inferred | 71 | 0.5352 | 0.5176 | 0.4004 | 0.4113 | 0.7218 | 58.4 | `e3e48c7eb861` | [retrieval-dense-tf-inferred-e3e48c7eb861.json](retrieval-dense-tf-inferred-e3e48c7eb861.json) |
| retrieval-dense-tf-oracle-d313b1d8bbf6 | 2026-09-28 | dense | 25 | oracle | 71 | 0.5352 | 0.5176 | 0.4004 | 0.4113 | 0.7218 | 59.9 | `d313b1d8bbf6` | [retrieval-dense-tf-oracle-d313b1d8bbf6.json](retrieval-dense-tf-oracle-d313b1d8bbf6.json) |
| retrieval-hybrid-e22f0b7b3525 | 2026-09-28 | hybrid | 25 | none | 71 | 0.4789 | 0.4718 | 0.3574 | 0.3635 | 0.7077 | 257.9 | `e22f0b7b3525` | [retrieval-hybrid-e22f0b7b3525.json](retrieval-hybrid-e22f0b7b3525.json) |
| retrieval-hybrid-f50-9c97122ce86b | 2026-09-28 | hybrid | 25 | none | 71 | 0.4789 | 0.4718 | 0.3632 | 0.3685 | 0.7218 | 270.9 | `9c97122ce86b` | [retrieval-hybrid-f50-9c97122ce86b.json](retrieval-hybrid-f50-9c97122ce86b.json) |
| retrieval-hybrid-rerank-f25-955ff196d13f | 2026-09-28 | hybrid | 25 | none | 71 | 0.5775 | 0.5599 | 0.4686 | 0.4740 | 0.7077 | 630.1 | `955ff196d13f` | [retrieval-hybrid-rerank-f25-955ff196d13f.json](retrieval-hybrid-rerank-f25-955ff196d13f.json) |
| retrieval-hybrid-rerank-f50-0f8cb0cc6e95 | 2026-09-28 | hybrid | 25 | none | 71 | 0.5915 | 0.5739 | 0.4706 | 0.4818 | 0.7113 | 1058.6 | `0f8cb0cc6e95` | [retrieval-hybrid-rerank-f50-0f8cb0cc6e95.json](retrieval-hybrid-rerank-f50-0f8cb0cc6e95.json) |
| retrieval-hybrid-rerank-f50-tf-inferred-2048c3da9784 | 2026-09-28 | hybrid | 25 | inferred | 71 | 0.6056 | 0.5880 | 0.4894 | 0.4978 | 0.7394 | 1119.2 | `2048c3da9784` | [retrieval-hybrid-rerank-f50-tf-inferred-2048c3da9784.json](retrieval-hybrid-rerank-f50-tf-inferred-2048c3da9784.json) |
| retrieval-hybrid-rerank-f50-tf-oracle-a401ac0308ac | 2026-09-28 | hybrid | 25 | oracle | 71 | 0.6056 | 0.5880 | 0.4894 | 0.4978 | 0.7394 | 1114.4 | `a401ac0308ac` | [retrieval-hybrid-rerank-f50-tf-oracle-a401ac0308ac.json](retrieval-hybrid-rerank-f50-tf-oracle-a401ac0308ac.json) |
| retrieval-hybrid-rrf20-09ddab803fac | 2026-09-28 | hybrid | 25 | none | 71 | 0.4930 | 0.4859 | 0.3567 | 0.3679 | 0.7077 | 276.1 | `09ddab803fac` | [retrieval-hybrid-rrf20-09ddab803fac.json](retrieval-hybrid-rrf20-09ddab803fac.json) |
| retrieval-hybrid-sw0.5-7a07be6a37e4 | 2026-09-28 | hybrid | 25 | none | 71 | 0.4789 | 0.4613 | 0.3699 | 0.3699 | 0.6796 | 266.4 | `7a07be6a37e4` | [retrieval-hybrid-sw0.5-7a07be6a37e4.json](retrieval-hybrid-sw0.5-7a07be6a37e4.json) |
| retrieval-hybrid-sw2-fafb15d81f6c | 2026-09-28 | hybrid | 25 | none | 71 | 0.4648 | 0.4577 | 0.3361 | 0.3566 | 0.5704 | 269.3 | `fafb15d81f6c` | [retrieval-hybrid-sw2-fafb15d81f6c.json](retrieval-hybrid-sw2-fafb15d81f6c.json) |
| retrieval-hybrid-tf-inferred-164c3493c177 | 2026-09-28 | hybrid | 25 | inferred | 71 | 0.5070 | 0.5000 | 0.4036 | 0.4047 | 0.7500 | 303.8 | `164c3493c177` | [retrieval-hybrid-tf-inferred-164c3493c177.json](retrieval-hybrid-tf-inferred-164c3493c177.json) |

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

- `contract-v3-1587bfef1bd1.json` — --generate-only: generation checkpointed, judge pass not run
- `cost-v3-f5faee254363.json` — --generate-only: generation checkpointed, judge pass not run
- `empty-probe-gemini-3.1-flash-lite-af83fa6.json` — --generate-only: generation checkpointed, judge pass not run
- `empty-probe-gemini-3.6-flash-af83fa6.json` — --generate-only: generation checkpointed, judge pass not run
