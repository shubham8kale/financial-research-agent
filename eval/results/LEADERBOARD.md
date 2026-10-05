# Leaderboard

Regenerated 2026-10-05T12:41:16+00:00 by `python -m eval.leaderboard` from every results file in this directory. Do not edit by hand.

Rows are comparable only within a table and only at the same benchmark version. Judge-scored means count a terminal failure (empty answer, recursion limit) as 0.

## Generation runs (schema 3: per-chunk contexts)

| run | date | benchmark | agent | judge | contexts | n | faithfulness | answer_rel | context_recall | figure_exact | figure_primary | agent_hit (searched, n) | cost/query | p50 latency | config | file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| upgrade-judged-1c3a876 | 2026-10-05 | b55a114e8c8a | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.9404 | 0.9122 | 0.8592 | 0.8478 | 1.0000 | 0.7949 (39) | $0.0020 | 2.8 s | `7c50eed7da71` | [upgrade-judged-7c50eed7da71.json](upgrade-judged-7c50eed7da71.json) |
| reindex-v3-013f548 | 2026-09-29 | b55a114e8c8a | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.9487 | 0.8899 | 0.8451 | 0.8696 | 1.0000 | 0.7895 (38) | $0.0020 | 3.2 s | `5b1deb95bdcc` | [reindex-v3-5b1deb95bdcc.json](reindex-v3-5b1deb95bdcc.json) |
| baseline-v3-87b6dd0 | 2026-09-28 | c12868d9d0d0 | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.8263 | 0.7639 | 0.7183 | 0.6000 | 0.7333 | 0.4366 | — | — | `a05e986405ba` | [baseline-v3-a05e986405ba.json](baseline-v3-a05e986405ba.json) |
| baseline-v3-plain-7b245f5 | 2026-09-28 | c12868d9d0d0 | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk | 71 | 0.7042 | 0.7676 | 0.6901 | 0.6000 | 0.7333 | 0.4366 | — | — | `1f47fcda5bae` | [baseline-v3-plain-1f47fcda5bae.json](baseline-v3-plain-1f47fcda5bae.json) |
| facts-v3-85f6dc0 | 2026-09-28 | c12868d9d0d0 | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.9573 | 0.8935 | 0.8521 | 0.8889 | 1.0000 | 0.4225 | — | — | `7b536024e855` | [facts-v3-7b536024e855.json](facts-v3-7b536024e855.json) |
| rerank-v3-d7470bc | 2026-09-28 | c12868d9d0d0 | gemini-3.1-flash-lite | google/gemini-3.6-flash | chunk+provenance | 71 | 0.9315 | 0.8688 | 0.8873 | 0.8444 | 0.9111 | 0.6620 | — | — | `c27752c52dab` | [rerank-v3-c27752c52dab.json](rerank-v3-c27752c52dab.json) |

`figure_exact`: share of items whose answer contains every ground-truth figure, context figures included (deterministic, no judge). `figure_primary`: share whose answer contains the figure the question asked for (the first non-year figure in the ground truth). `agent_hit`: share of labelled items where any relevant index chunk appeared in the agent's tool observations, over the items that searched the index at all (an item answered from the fact table cannot have seen a chunk). `cost/query` and `p50 latency` are measured by the harness's own meter on runs generated after it existed (agent calls only; the judge is separate).

## Retrieval runs (retriever alone, no LLM)

| run | date | benchmark | mode | k | ticker_filter | n | hit@5 | recall@5 | mrr | ndcg@5 | recall@25 | p50 ms | config | file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| retrieval-bm25-61fd1f01d385 | 2026-09-28 | b55a114e8c8a | bm25 | 25 | none | 71 | 0.3944 | 0.3944 | 0.2994 | 0.3001 | 0.6831 | 12.6 | `61fd1f01d385` | [retrieval-bm25-61fd1f01d385.json](retrieval-bm25-61fd1f01d385.json) |
| retrieval-bm25-cfd735ee2410 | 2026-09-28 | c12868d9d0d0 | bm25 | 25 | none | 71 | 0.3521 | 0.3521 | 0.3036 | 0.2978 | 0.5704 | 241.5 | `cfd735ee2410` | [retrieval-bm25-cfd735ee2410.json](retrieval-bm25-cfd735ee2410.json) |
| retrieval-dense-5571ce86feed | 2026-09-28 | b55a114e8c8a | dense | 25 | none | 71 | 0.5070 | 0.4894 | 0.3582 | 0.3695 | 0.7254 | 13.6 | `5571ce86feed` | [retrieval-dense-5571ce86feed.json](retrieval-dense-5571ce86feed.json) |
| retrieval-dense-e53da55931de | 2026-09-28 | c12868d9d0d0 | dense | 25 | no | 71 | 0.5070 | 0.4894 | 0.3569 | 0.3729 | 0.6796 | 17.5 | `e53da55931de` | [retrieval-dense-e53da55931de.json](retrieval-dense-e53da55931de.json) |
| retrieval-dense-rerank-f100-82e4c431f458 | 2026-09-28 | b55a114e8c8a | dense | 25 | none | 71 | 0.6479 | 0.6268 | 0.5156 | 0.5318 | 0.7113 | 1549.8 | `82e4c431f458` | [retrieval-dense-rerank-f100-82e4c431f458.json](retrieval-dense-rerank-f100-82e4c431f458.json) |
| retrieval-dense-rerank-f100-bdd1bfd64fca | 2026-09-28 | c12868d9d0d0 | dense | 25 | none | 71 | 0.6197 | 0.6021 | 0.4818 | 0.4993 | 0.7007 | 1620.1 | `bdd1bfd64fca` | [retrieval-dense-rerank-f100-bdd1bfd64fca.json](retrieval-dense-rerank-f100-bdd1bfd64fca.json) |
| retrieval-dense-rerank-f100-tf-inferred-32df4eef75d6 | 2026-09-28 | c12868d9d0d0 | dense | 25 | inferred | 71 | 0.6056 | 0.5880 | 0.4857 | 0.4967 | 0.7148 | 1719.4 | `32df4eef75d6` | [retrieval-dense-rerank-f100-tf-inferred-32df4eef75d6.json](retrieval-dense-rerank-f100-tf-inferred-32df4eef75d6.json) |
| retrieval-dense-rerank-f100-tf-inferred-b82fb24bab94 | 2026-09-28 | b55a114e8c8a | dense | 25 | inferred | 71 | 0.6479 | 0.6268 | 0.5280 | 0.5389 | 0.7535 | 1606.0 | `b82fb24bab94` | [retrieval-dense-rerank-f100-tf-inferred-b82fb24bab94.json](retrieval-dense-rerank-f100-tf-inferred-b82fb24bab94.json) |
| retrieval-dense-rerank-f25-6ca601a0fbfc | 2026-09-28 | b55a114e8c8a | dense | 25 | none | 71 | 0.6479 | 0.6197 | 0.5141 | 0.5266 | 0.7254 | 432.6 | `6ca601a0fbfc` | [retrieval-dense-rerank-f25-6ca601a0fbfc.json](retrieval-dense-rerank-f25-6ca601a0fbfc.json) |
| retrieval-dense-rerank-f25-e81393d2160b | 2026-09-28 | c12868d9d0d0 | dense | 25 | none | 71 | 0.5493 | 0.5317 | 0.4210 | 0.4341 | 0.6796 | 468.6 | `e81393d2160b` | [retrieval-dense-rerank-f25-e81393d2160b.json](retrieval-dense-rerank-f25-e81393d2160b.json) |
| retrieval-dense-rerank-f50-489f8c258136 | 2026-09-28 | b55a114e8c8a | dense | 25 | none | 71 | 0.6620 | 0.6303 | 0.5286 | 0.5385 | 0.7430 | 785.2 | `489f8c258136` | [retrieval-dense-rerank-f50-489f8c258136.json](retrieval-dense-rerank-f50-489f8c258136.json) |
| retrieval-dense-rerank-f50-cfe2325ee180 | 2026-09-28 | c12868d9d0d0 | dense | 25 | none | 71 | 0.6197 | 0.5915 | 0.4802 | 0.4926 | 0.7218 | 794.5 | `cfe2325ee180` | [retrieval-dense-rerank-f50-cfe2325ee180.json](retrieval-dense-rerank-f50-cfe2325ee180.json) |
| retrieval-dense-rerank-f50-tf-inferred-65a9dd460723 | 2026-09-28 | b55a114e8c8a | dense | 25 | inferred | 71 | 0.6620 | 0.6303 | 0.5407 | 0.5456 | 0.7711 | 750.9 | `65a9dd460723` | [retrieval-dense-rerank-f50-tf-inferred-65a9dd460723.json](retrieval-dense-rerank-f50-tf-inferred-65a9dd460723.json) |
| retrieval-dense-rerank-f50-tf-inferred-b90fb53e560e | 2026-09-28 | c12868d9d0d0 | dense | 25 | inferred | 71 | 0.6338 | 0.6056 | 0.4963 | 0.5075 | 0.7500 | 892.8 | `b90fb53e560e` | [retrieval-dense-rerank-f50-tf-inferred-b90fb53e560e.json](retrieval-dense-rerank-f50-tf-inferred-b90fb53e560e.json) |
| retrieval-dense-tf-inferred-0aab2af05ba4 | 2026-09-28 | b55a114e8c8a | dense | 25 | inferred | 71 | 0.5493 | 0.5317 | 0.4028 | 0.4123 | 0.7676 | 22.0 | `0aab2af05ba4` | [retrieval-dense-tf-inferred-0aab2af05ba4.json](retrieval-dense-tf-inferred-0aab2af05ba4.json) |
| retrieval-dense-tf-inferred-7bdc852f579a | 2026-09-28 | c12868d9d0d0 | dense | 25 | inferred | 71 | 0.5352 | 0.5176 | 0.4004 | 0.4113 | 0.7218 | 58.4 | `7bdc852f579a` | [retrieval-dense-tf-inferred-7bdc852f579a.json](retrieval-dense-tf-inferred-7bdc852f579a.json) |
| retrieval-dense-tf-oracle-34a06b35809e | 2026-09-28 | b55a114e8c8a | dense | 25 | oracle | 71 | 0.5493 | 0.5317 | 0.4028 | 0.4123 | 0.7676 | 23.1 | `34a06b35809e` | [retrieval-dense-tf-oracle-34a06b35809e.json](retrieval-dense-tf-oracle-34a06b35809e.json) |
| retrieval-dense-tf-oracle-ae62e967b843 | 2026-09-28 | c12868d9d0d0 | dense | 25 | oracle | 71 | 0.5352 | 0.5176 | 0.4004 | 0.4113 | 0.7218 | 59.9 | `ae62e967b843` | [retrieval-dense-tf-oracle-ae62e967b843.json](retrieval-dense-tf-oracle-ae62e967b843.json) |
| retrieval-hybrid-28f72601e28f | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | none | 71 | 0.4789 | 0.4718 | 0.3574 | 0.3635 | 0.7077 | 257.9 | `28f72601e28f` | [retrieval-hybrid-28f72601e28f.json](retrieval-hybrid-28f72601e28f.json) |
| retrieval-hybrid-2b28152274f3 | 2026-09-28 | b55a114e8c8a | hybrid | 25 | none | 71 | 0.5915 | 0.5845 | 0.4004 | 0.4331 | 0.7500 | 27.3 | `2b28152274f3` | [retrieval-hybrid-2b28152274f3.json](retrieval-hybrid-2b28152274f3.json) |
| retrieval-hybrid-f50-adb3359a6074 | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | none | 71 | 0.4789 | 0.4718 | 0.3632 | 0.3685 | 0.7218 | 270.9 | `adb3359a6074` | [retrieval-hybrid-f50-adb3359a6074.json](retrieval-hybrid-f50-adb3359a6074.json) |
| retrieval-hybrid-f50-ae2a27a70146 | 2026-09-28 | b55a114e8c8a | hybrid | 25 | none | 71 | 0.5775 | 0.5669 | 0.4006 | 0.4263 | 0.7500 | 26.9 | `ae2a27a70146` | [retrieval-hybrid-f50-ae2a27a70146.json](retrieval-hybrid-f50-ae2a27a70146.json) |
| retrieval-hybrid-rerank-f25-25dd9f13ac04 | 2026-09-28 | b55a114e8c8a | hybrid | 25 | none | 71 | 0.6338 | 0.6232 | 0.5227 | 0.5332 | 0.7500 | 574.6 | `25dd9f13ac04` | [retrieval-hybrid-rerank-f25-25dd9f13ac04.json](retrieval-hybrid-rerank-f25-25dd9f13ac04.json) |
| retrieval-hybrid-rerank-f25-ce9ae7e0fd3a | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | none | 71 | 0.5775 | 0.5599 | 0.4686 | 0.4740 | 0.7077 | 630.1 | `ce9ae7e0fd3a` | [retrieval-hybrid-rerank-f25-ce9ae7e0fd3a.json](retrieval-hybrid-rerank-f25-ce9ae7e0fd3a.json) |
| retrieval-hybrid-rerank-f50-592eb34e4d39 | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | none | 71 | 0.5915 | 0.5739 | 0.4706 | 0.4818 | 0.7113 | 1058.6 | `592eb34e4d39` | [retrieval-hybrid-rerank-f50-592eb34e4d39.json](retrieval-hybrid-rerank-f50-592eb34e4d39.json) |
| retrieval-hybrid-rerank-f50-9432e95c991d | 2026-09-28 | b55a114e8c8a | hybrid | 25 | none | 71 | 0.6338 | 0.6232 | 0.5216 | 0.5322 | 0.7183 | 979.3 | `9432e95c991d` | [retrieval-hybrid-rerank-f50-9432e95c991d.json](retrieval-hybrid-rerank-f50-9432e95c991d.json) |
| retrieval-hybrid-rerank-f50-tf-inferred-16308643fff1 | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | inferred | 71 | 0.6056 | 0.5880 | 0.4894 | 0.4978 | 0.7394 | 1119.2 | `16308643fff1` | [retrieval-hybrid-rerank-f50-tf-inferred-16308643fff1.json](retrieval-hybrid-rerank-f50-tf-inferred-16308643fff1.json) |
| retrieval-hybrid-rerank-f50-tf-inferred-6840e5d6edd4 | 2026-09-28 | b55a114e8c8a | hybrid | 25 | inferred | 71 | 0.6338 | 0.6232 | 0.5348 | 0.5392 | 0.7746 | 1043.8 | `6840e5d6edd4` | [retrieval-hybrid-rerank-f50-tf-inferred-6840e5d6edd4.json](retrieval-hybrid-rerank-f50-tf-inferred-6840e5d6edd4.json) |
| retrieval-hybrid-rerank-f50-tf-oracle-8ba54909dc39 | 2026-09-28 | b55a114e8c8a | hybrid | 25 | oracle | 71 | 0.6338 | 0.6232 | 0.5348 | 0.5392 | 0.7746 | 1042.1 | `8ba54909dc39` | [retrieval-hybrid-rerank-f50-tf-oracle-8ba54909dc39.json](retrieval-hybrid-rerank-f50-tf-oracle-8ba54909dc39.json) |
| retrieval-hybrid-rerank-f50-tf-oracle-fb50cc30a907 | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | oracle | 71 | 0.6056 | 0.5880 | 0.4894 | 0.4978 | 0.7394 | 1114.4 | `fb50cc30a907` | [retrieval-hybrid-rerank-f50-tf-oracle-fb50cc30a907.json](retrieval-hybrid-rerank-f50-tf-oracle-fb50cc30a907.json) |
| retrieval-hybrid-rrf20-5574d89e6b3a | 2026-09-28 | b55a114e8c8a | hybrid | 25 | none | 71 | 0.5915 | 0.5845 | 0.3940 | 0.4285 | 0.7500 | 30.3 | `5574d89e6b3a` | [retrieval-hybrid-rrf20-5574d89e6b3a.json](retrieval-hybrid-rrf20-5574d89e6b3a.json) |
| retrieval-hybrid-rrf20-5ca9e63ee8c6 | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | none | 71 | 0.4930 | 0.4859 | 0.3567 | 0.3679 | 0.7077 | 276.1 | `5ca9e63ee8c6` | [retrieval-hybrid-rrf20-5ca9e63ee8c6.json](retrieval-hybrid-rrf20-5ca9e63ee8c6.json) |
| retrieval-hybrid-sw0.5-2db6cf545199 | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | none | 71 | 0.4789 | 0.4613 | 0.3699 | 0.3699 | 0.6796 | 266.4 | `2db6cf545199` | [retrieval-hybrid-sw0.5-2db6cf545199.json](retrieval-hybrid-sw0.5-2db6cf545199.json) |
| retrieval-hybrid-sw0.5-c6724b8409f9 | 2026-09-28 | b55a114e8c8a | hybrid | 25 | none | 71 | 0.5775 | 0.5599 | 0.4010 | 0.4230 | 0.7254 | 26.9 | `c6724b8409f9` | [retrieval-hybrid-sw0.5-c6724b8409f9.json](retrieval-hybrid-sw0.5-c6724b8409f9.json) |
| retrieval-hybrid-sw2-5a50d0a89612 | 2026-09-28 | b55a114e8c8a | hybrid | 25 | none | 71 | 0.5352 | 0.5352 | 0.3570 | 0.3900 | 0.6831 | 29.7 | `5a50d0a89612` | [retrieval-hybrid-sw2-5a50d0a89612.json](retrieval-hybrid-sw2-5a50d0a89612.json) |
| retrieval-hybrid-sw2-794a142522bd | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | none | 71 | 0.4648 | 0.4577 | 0.3361 | 0.3566 | 0.5704 | 269.3 | `794a142522bd` | [retrieval-hybrid-sw2-794a142522bd.json](retrieval-hybrid-sw2-794a142522bd.json) |
| retrieval-hybrid-tf-inferred-63d8168acb1b | 2026-09-28 | c12868d9d0d0 | hybrid | 25 | inferred | 71 | 0.5070 | 0.5000 | 0.4036 | 0.4047 | 0.7500 | 303.8 | `63d8168acb1b` | [retrieval-hybrid-tf-inferred-63d8168acb1b.json](retrieval-hybrid-tf-inferred-63d8168acb1b.json) |
| retrieval-hybrid-tf-inferred-b103be4450e5 | 2026-09-28 | b55a114e8c8a | hybrid | 25 | inferred | 71 | 0.6056 | 0.5986 | 0.4298 | 0.4571 | 0.7923 | 30.6 | `b103be4450e5` | [retrieval-hybrid-tf-inferred-b103be4450e5.json](retrieval-hybrid-tf-inferred-b103be4450e5.json) |

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

- `a-before-dfa6f50193ab.json` — --generate-only: generation checkpointed, judge pass not run
- `a-wording-5512347a3b12.json` — --generate-only: generation checkpointed, judge pass not run
- `b-control-e8f826600800.json` — --generate-only: generation checkpointed, judge pass not run
- `b-rule-v1-8a8b2d430b78.json` — --generate-only: generation checkpointed, judge pass not run
- `b-rule-v2-2b81f7712548.json` — --generate-only: generation checkpointed, judge pass not run
- `contract-v3-76b8f532c332.json` — --generate-only: generation checkpointed, judge pass not run
- `cost-v3-2d69cde009fc.json` — --generate-only: generation checkpointed, judge pass not run
- `empty-probe-gemini-3.1-flash-lite-af83fa6.json` — --generate-only: generation checkpointed, judge pass not run
- `empty-probe-gemini-3.6-flash-af83fa6.json` — --generate-only: generation checkpointed, judge pass not run
- `q8-rule-on-1-88ad3e4f5d61.json` — --generate-only: generation checkpointed, judge pass not run
- `q8-rule-on-2-88ad3e4f5d61.json` — --generate-only: generation checkpointed, judge pass not run
- `q8-rule-on-3-88ad3e4f5d61.json` — --generate-only: generation checkpointed, judge pass not run
- `upgrade-control-7c50eed7da71.json` — --generate-only: generation checkpointed, judge pass not run
- `upgrade-v1-0c01c6017b31.json` — --generate-only: generation checkpointed, judge pass not run
