# Leaderboard

Regenerated 2026-09-28T22:48:25+00:00 by `python -m eval.leaderboard` from every results file in this directory. Do not edit by hand.

Rows are comparable only within a table and only at the same benchmark version. Judge-scored means count a terminal failure (empty answer, recursion limit) as 0.

## Generation runs (schema 3: per-chunk contexts)

| run | date | benchmark | agent | judge | contexts | n | faithfulness | answer_rel | context_recall | figure_exact | figure_primary | agent_hit (searched, n) | cost/query | p50 latency | config | file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline-v3-87b6dd0 | 2026-09-28 | 9db9724eaeda | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.8263 | 0.7639 | 0.7183 | 0.6000 | 0.7333 | 0.4366 | — | — | `aea128d62403` | [baseline-v3-aea128d62403.json](baseline-v3-aea128d62403.json) |
| baseline-v3-plain-7b245f5 | 2026-09-28 | 9db9724eaeda | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk | 71 | 0.7042 | 0.7676 | 0.6901 | 0.6000 | 0.7333 | 0.4366 | — | — | `4a3f267adb41` | [baseline-v3-plain-4a3f267adb41.json](baseline-v3-plain-4a3f267adb41.json) |
| facts-v3-85f6dc0 | 2026-09-28 | 9db9724eaeda | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.9573 | 0.8935 | 0.8521 | 0.8889 | 1.0000 | 0.4225 | — | — | `70db17ff5e31` | [facts-v3-70db17ff5e31.json](facts-v3-70db17ff5e31.json) |
| rerank-v3-d7470bc | 2026-09-28 | 9db9724eaeda | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.9315 | 0.8688 | 0.8873 | 0.8444 | 0.9111 | 0.6620 | — | — | `764b3da65d36` | [rerank-v3-764b3da65d36.json](rerank-v3-764b3da65d36.json) |

`figure_exact`: share of items whose answer contains every ground-truth figure, context figures included (deterministic, no judge). `figure_primary`: share whose answer contains the figure the question asked for (the first non-year figure in the ground truth). `agent_hit`: share of labelled items where any relevant index chunk appeared in the agent's tool observations, over the items that searched the index at all (an item answered from the fact table cannot have seen a chunk). `cost/query` and `p50 latency` are measured by the harness's own meter on runs generated after it existed (agent calls only; the judge is separate).

## Retrieval runs (retriever alone, no LLM)

| run | date | benchmark | mode | k | ticker_filter | n | hit@5 | recall@5 | mrr | ndcg@5 | recall@25 | p50 ms | config | file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| retrieval-bm25-3bc53167968f | 2026-09-28 | c34109211fce | bm25 | 25 | none | 71 | 0.3944 | 0.3944 | 0.2994 | 0.3001 | 0.6831 | 12.6 | `3bc53167968f` | [retrieval-bm25-3bc53167968f.json](retrieval-bm25-3bc53167968f.json) |
| retrieval-bm25-a308c41b72ea | 2026-09-28 | 9db9724eaeda | bm25 | 25 | none | 71 | 0.3521 | 0.3521 | 0.3036 | 0.2978 | 0.5704 | 241.5 | `a308c41b72ea` | [retrieval-bm25-a308c41b72ea.json](retrieval-bm25-a308c41b72ea.json) |
| retrieval-dense-1e17cf5b5eab | 2026-09-28 | 9db9724eaeda | dense | 25 | no | 71 | 0.5070 | 0.4894 | 0.3569 | 0.3729 | 0.6796 | 17.5 | `1e17cf5b5eab` | [retrieval-dense-1e17cf5b5eab.json](retrieval-dense-1e17cf5b5eab.json) |
| retrieval-dense-eb4d1b4fefba | 2026-09-28 | c34109211fce | dense | 25 | none | 71 | 0.5070 | 0.4894 | 0.3582 | 0.3695 | 0.7254 | 13.6 | `eb4d1b4fefba` | [retrieval-dense-eb4d1b4fefba.json](retrieval-dense-eb4d1b4fefba.json) |
| retrieval-dense-rerank-f100-6a2b5a7610ae | 2026-09-28 | c34109211fce | dense | 25 | none | 71 | 0.6479 | 0.6268 | 0.5156 | 0.5318 | 0.7113 | 1549.8 | `6a2b5a7610ae` | [retrieval-dense-rerank-f100-6a2b5a7610ae.json](retrieval-dense-rerank-f100-6a2b5a7610ae.json) |
| retrieval-dense-rerank-f100-84fdb1858a25 | 2026-09-28 | 9db9724eaeda | dense | 25 | none | 71 | 0.6197 | 0.6021 | 0.4818 | 0.4993 | 0.7007 | 1620.1 | `84fdb1858a25` | [retrieval-dense-rerank-f100-84fdb1858a25.json](retrieval-dense-rerank-f100-84fdb1858a25.json) |
| retrieval-dense-rerank-f100-tf-inferred-34f47fc55166 | 2026-09-28 | c34109211fce | dense | 25 | inferred | 71 | 0.6479 | 0.6268 | 0.5280 | 0.5389 | 0.7535 | 1606.0 | `34f47fc55166` | [retrieval-dense-rerank-f100-tf-inferred-34f47fc55166.json](retrieval-dense-rerank-f100-tf-inferred-34f47fc55166.json) |
| retrieval-dense-rerank-f100-tf-inferred-ad3ace274d29 | 2026-09-28 | 9db9724eaeda | dense | 25 | inferred | 71 | 0.6056 | 0.5880 | 0.4857 | 0.4967 | 0.7148 | 1719.4 | `ad3ace274d29` | [retrieval-dense-rerank-f100-tf-inferred-ad3ace274d29.json](retrieval-dense-rerank-f100-tf-inferred-ad3ace274d29.json) |
| retrieval-dense-rerank-f25-c5ce21431f88 | 2026-09-28 | 9db9724eaeda | dense | 25 | none | 71 | 0.5493 | 0.5317 | 0.4210 | 0.4341 | 0.6796 | 468.6 | `c5ce21431f88` | [retrieval-dense-rerank-f25-c5ce21431f88.json](retrieval-dense-rerank-f25-c5ce21431f88.json) |
| retrieval-dense-rerank-f25-c78dcd0e3370 | 2026-09-28 | c34109211fce | dense | 25 | none | 71 | 0.6479 | 0.6197 | 0.5141 | 0.5266 | 0.7254 | 432.6 | `c78dcd0e3370` | [retrieval-dense-rerank-f25-c78dcd0e3370.json](retrieval-dense-rerank-f25-c78dcd0e3370.json) |
| retrieval-dense-rerank-f50-1417a345c7b7 | 2026-09-28 | 9db9724eaeda | dense | 25 | none | 71 | 0.6197 | 0.5915 | 0.4802 | 0.4926 | 0.7218 | 794.5 | `1417a345c7b7` | [retrieval-dense-rerank-f50-1417a345c7b7.json](retrieval-dense-rerank-f50-1417a345c7b7.json) |
| retrieval-dense-rerank-f50-f685e695b18a | 2026-09-28 | c34109211fce | dense | 25 | none | 71 | 0.6620 | 0.6303 | 0.5286 | 0.5385 | 0.7430 | 785.2 | `f685e695b18a` | [retrieval-dense-rerank-f50-f685e695b18a.json](retrieval-dense-rerank-f50-f685e695b18a.json) |
| retrieval-dense-rerank-f50-tf-inferred-14ba1d196c5a | 2026-09-28 | c34109211fce | dense | 25 | inferred | 71 | 0.6620 | 0.6303 | 0.5407 | 0.5456 | 0.7711 | 750.9 | `14ba1d196c5a` | [retrieval-dense-rerank-f50-tf-inferred-14ba1d196c5a.json](retrieval-dense-rerank-f50-tf-inferred-14ba1d196c5a.json) |
| retrieval-dense-rerank-f50-tf-inferred-8f6ef861d7e7 | 2026-09-28 | 9db9724eaeda | dense | 25 | inferred | 71 | 0.6338 | 0.6056 | 0.4963 | 0.5075 | 0.7500 | 892.8 | `8f6ef861d7e7` | [retrieval-dense-rerank-f50-tf-inferred-8f6ef861d7e7.json](retrieval-dense-rerank-f50-tf-inferred-8f6ef861d7e7.json) |
| retrieval-dense-tf-inferred-32f799903650 | 2026-09-28 | c34109211fce | dense | 25 | inferred | 71 | 0.5493 | 0.5317 | 0.4028 | 0.4123 | 0.7676 | 22.0 | `32f799903650` | [retrieval-dense-tf-inferred-32f799903650.json](retrieval-dense-tf-inferred-32f799903650.json) |
| retrieval-dense-tf-inferred-e3e48c7eb861 | 2026-09-28 | 9db9724eaeda | dense | 25 | inferred | 71 | 0.5352 | 0.5176 | 0.4004 | 0.4113 | 0.7218 | 58.4 | `e3e48c7eb861` | [retrieval-dense-tf-inferred-e3e48c7eb861.json](retrieval-dense-tf-inferred-e3e48c7eb861.json) |
| retrieval-dense-tf-oracle-6affaa6c8917 | 2026-09-28 | c34109211fce | dense | 25 | oracle | 71 | 0.5493 | 0.5317 | 0.4028 | 0.4123 | 0.7676 | 23.1 | `6affaa6c8917` | [retrieval-dense-tf-oracle-6affaa6c8917.json](retrieval-dense-tf-oracle-6affaa6c8917.json) |
| retrieval-dense-tf-oracle-d313b1d8bbf6 | 2026-09-28 | 9db9724eaeda | dense | 25 | oracle | 71 | 0.5352 | 0.5176 | 0.4004 | 0.4113 | 0.7218 | 59.9 | `d313b1d8bbf6` | [retrieval-dense-tf-oracle-d313b1d8bbf6.json](retrieval-dense-tf-oracle-d313b1d8bbf6.json) |
| retrieval-hybrid-a0c4d2d732c9 | 2026-09-28 | c34109211fce | hybrid | 25 | none | 71 | 0.5915 | 0.5845 | 0.4004 | 0.4331 | 0.7500 | 27.3 | `a0c4d2d732c9` | [retrieval-hybrid-a0c4d2d732c9.json](retrieval-hybrid-a0c4d2d732c9.json) |
| retrieval-hybrid-e22f0b7b3525 | 2026-09-28 | 9db9724eaeda | hybrid | 25 | none | 71 | 0.4789 | 0.4718 | 0.3574 | 0.3635 | 0.7077 | 257.9 | `e22f0b7b3525` | [retrieval-hybrid-e22f0b7b3525.json](retrieval-hybrid-e22f0b7b3525.json) |
| retrieval-hybrid-f50-5ad2e1d25835 | 2026-09-28 | c34109211fce | hybrid | 25 | none | 71 | 0.5775 | 0.5669 | 0.4006 | 0.4263 | 0.7500 | 26.9 | `5ad2e1d25835` | [retrieval-hybrid-f50-5ad2e1d25835.json](retrieval-hybrid-f50-5ad2e1d25835.json) |
| retrieval-hybrid-f50-9c97122ce86b | 2026-09-28 | 9db9724eaeda | hybrid | 25 | none | 71 | 0.4789 | 0.4718 | 0.3632 | 0.3685 | 0.7218 | 270.9 | `9c97122ce86b` | [retrieval-hybrid-f50-9c97122ce86b.json](retrieval-hybrid-f50-9c97122ce86b.json) |
| retrieval-hybrid-rerank-f25-1d1fe4ddf911 | 2026-09-28 | c34109211fce | hybrid | 25 | none | 71 | 0.6338 | 0.6232 | 0.5227 | 0.5332 | 0.7500 | 574.6 | `1d1fe4ddf911` | [retrieval-hybrid-rerank-f25-1d1fe4ddf911.json](retrieval-hybrid-rerank-f25-1d1fe4ddf911.json) |
| retrieval-hybrid-rerank-f25-955ff196d13f | 2026-09-28 | 9db9724eaeda | hybrid | 25 | none | 71 | 0.5775 | 0.5599 | 0.4686 | 0.4740 | 0.7077 | 630.1 | `955ff196d13f` | [retrieval-hybrid-rerank-f25-955ff196d13f.json](retrieval-hybrid-rerank-f25-955ff196d13f.json) |
| retrieval-hybrid-rerank-f50-0f8cb0cc6e95 | 2026-09-28 | 9db9724eaeda | hybrid | 25 | none | 71 | 0.5915 | 0.5739 | 0.4706 | 0.4818 | 0.7113 | 1058.6 | `0f8cb0cc6e95` | [retrieval-hybrid-rerank-f50-0f8cb0cc6e95.json](retrieval-hybrid-rerank-f50-0f8cb0cc6e95.json) |
| retrieval-hybrid-rerank-f50-414e872bc3c7 | 2026-09-28 | c34109211fce | hybrid | 25 | none | 71 | 0.6338 | 0.6232 | 0.5216 | 0.5322 | 0.7183 | 979.3 | `414e872bc3c7` | [retrieval-hybrid-rerank-f50-414e872bc3c7.json](retrieval-hybrid-rerank-f50-414e872bc3c7.json) |
| retrieval-hybrid-rerank-f50-tf-inferred-2048c3da9784 | 2026-09-28 | 9db9724eaeda | hybrid | 25 | inferred | 71 | 0.6056 | 0.5880 | 0.4894 | 0.4978 | 0.7394 | 1119.2 | `2048c3da9784` | [retrieval-hybrid-rerank-f50-tf-inferred-2048c3da9784.json](retrieval-hybrid-rerank-f50-tf-inferred-2048c3da9784.json) |
| retrieval-hybrid-rerank-f50-tf-inferred-3bc6bb2b0efc | 2026-09-28 | c34109211fce | hybrid | 25 | inferred | 71 | 0.6338 | 0.6232 | 0.5348 | 0.5392 | 0.7746 | 1043.8 | `3bc6bb2b0efc` | [retrieval-hybrid-rerank-f50-tf-inferred-3bc6bb2b0efc.json](retrieval-hybrid-rerank-f50-tf-inferred-3bc6bb2b0efc.json) |
| retrieval-hybrid-rerank-f50-tf-oracle-7ec033cdeb78 | 2026-09-28 | c34109211fce | hybrid | 25 | oracle | 71 | 0.6338 | 0.6232 | 0.5348 | 0.5392 | 0.7746 | 1042.1 | `7ec033cdeb78` | [retrieval-hybrid-rerank-f50-tf-oracle-7ec033cdeb78.json](retrieval-hybrid-rerank-f50-tf-oracle-7ec033cdeb78.json) |
| retrieval-hybrid-rerank-f50-tf-oracle-a401ac0308ac | 2026-09-28 | 9db9724eaeda | hybrid | 25 | oracle | 71 | 0.6056 | 0.5880 | 0.4894 | 0.4978 | 0.7394 | 1114.4 | `a401ac0308ac` | [retrieval-hybrid-rerank-f50-tf-oracle-a401ac0308ac.json](retrieval-hybrid-rerank-f50-tf-oracle-a401ac0308ac.json) |
| retrieval-hybrid-rrf20-09ddab803fac | 2026-09-28 | 9db9724eaeda | hybrid | 25 | none | 71 | 0.4930 | 0.4859 | 0.3567 | 0.3679 | 0.7077 | 276.1 | `09ddab803fac` | [retrieval-hybrid-rrf20-09ddab803fac.json](retrieval-hybrid-rrf20-09ddab803fac.json) |
| retrieval-hybrid-rrf20-b87bdb385f31 | 2026-09-28 | c34109211fce | hybrid | 25 | none | 71 | 0.5915 | 0.5845 | 0.3940 | 0.4285 | 0.7500 | 30.3 | `b87bdb385f31` | [retrieval-hybrid-rrf20-b87bdb385f31.json](retrieval-hybrid-rrf20-b87bdb385f31.json) |
| retrieval-hybrid-sw0.5-7a07be6a37e4 | 2026-09-28 | 9db9724eaeda | hybrid | 25 | none | 71 | 0.4789 | 0.4613 | 0.3699 | 0.3699 | 0.6796 | 266.4 | `7a07be6a37e4` | [retrieval-hybrid-sw0.5-7a07be6a37e4.json](retrieval-hybrid-sw0.5-7a07be6a37e4.json) |
| retrieval-hybrid-sw0.5-7cd7332109b3 | 2026-09-28 | c34109211fce | hybrid | 25 | none | 71 | 0.5775 | 0.5599 | 0.4010 | 0.4230 | 0.7254 | 26.9 | `7cd7332109b3` | [retrieval-hybrid-sw0.5-7cd7332109b3.json](retrieval-hybrid-sw0.5-7cd7332109b3.json) |
| retrieval-hybrid-sw2-d424a4aae4b7 | 2026-09-28 | c34109211fce | hybrid | 25 | none | 71 | 0.5352 | 0.5352 | 0.3570 | 0.3900 | 0.6831 | 29.7 | `d424a4aae4b7` | [retrieval-hybrid-sw2-d424a4aae4b7.json](retrieval-hybrid-sw2-d424a4aae4b7.json) |
| retrieval-hybrid-sw2-fafb15d81f6c | 2026-09-28 | 9db9724eaeda | hybrid | 25 | none | 71 | 0.4648 | 0.4577 | 0.3361 | 0.3566 | 0.5704 | 269.3 | `fafb15d81f6c` | [retrieval-hybrid-sw2-fafb15d81f6c.json](retrieval-hybrid-sw2-fafb15d81f6c.json) |
| retrieval-hybrid-tf-inferred-164c3493c177 | 2026-09-28 | 9db9724eaeda | hybrid | 25 | inferred | 71 | 0.5070 | 0.5000 | 0.4036 | 0.4047 | 0.7500 | 303.8 | `164c3493c177` | [retrieval-hybrid-tf-inferred-164c3493c177.json](retrieval-hybrid-tf-inferred-164c3493c177.json) |
| retrieval-hybrid-tf-inferred-a70fd4a6a436 | 2026-09-28 | c34109211fce | hybrid | 25 | inferred | 71 | 0.6056 | 0.5986 | 0.4298 | 0.4571 | 0.7923 | 30.6 | `a70fd4a6a436` | [retrieval-hybrid-tf-inferred-a70fd4a6a436.json](retrieval-hybrid-tf-inferred-a70fd4a6a436.json) |

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
- `reindex-v3-ed9525a07d24.json` — --generate-only: generation checkpointed, judge pass not run
