# Evaluation

## Summary

A 71-item labelled benchmark over five FY2025 SEC 10-K filings, seven question
types, scored with RAGAS on faithfulness, answer relevancy and context recall.
Every item's answer, retrieved contexts and scores are committed under
[`results/`](results/) and can be opened directly.

The two full before/after runs below cover items 1–66. The five `temporal`
items (67–71) were added afterwards, in response to a failure seen on the
deployed demo, and were run separately — see finding 2. Each results file
records the exact item ids it covers, so no run is ever reported as broader
than it was.

**Three things matter here.**

**1. The agent silently returned nothing on 15% of items, and four layers of the
system failed to notice.** On `gemini-2.5-flash-lite`, 10 of 66 items produced an
empty final answer — `finish_reason: STOP`, no text. The agent returned it, the
API served it as HTTP 200, the streaming endpoint rendered it as a blank message
with source citations attached, and RAGAS scored it as `NaN` and then **excluded
it from the mean**, raising reported faithfulness from 0.71 to 0.84. The system's
ten worst items made its score look better. This is now guarded at every layer
and covered by tests.

**2. Changing one variable — the agent model — moved the headline by 0.17.**
Same prompt, same retrieval, same k, same judge, same items. `gemini-3.1-flash-lite`
eliminated all ten empty answers and lifted faithfulness from 0.714 to 0.881.
But terminal failures did not disappear so much as change shape: recursion-limit
hits went 2 → 6. The system fails less often, and differently.

**3. Retrieval quality depends on the agent model even with retrieval held
fixed.** The agent composes its own search queries, so query text is model output.
Running identical items on a different agent changed the retrieved passages on 7
of 8 items at fixed k, embeddings and index. "The retriever" is not the whole
retrieval system.

**What this evaluation does not establish:** n = 66 supports no statistically
significant claim, four of six question-type strata are n ≤ 8, and every headline
score is one judge's opinion. See [Limitations](#limitations).

**Instrument v2 (results schema 3), added after the runs above.** Contexts are
now scored per chunk instead of per tool observation, every item is labelled
with the chunk(s) that hold its reference passage, the retriever is scored on
its own with no LLM call, and a deterministic figure check scores whether the
answer quoted the right number. The section [Instrument v2](#instrument-v2-per-chunk-contexts-chunk-labels-and-judge-free-metrics)
has the re-baseline; findings 12–15 are what it showed. The headline from it:
**the dense retriever alone puts a relevant chunk in the top 5 on 50.7% of
items, and the agent's own query wording does worse, 43.7%.**

---

## Results

Two full runs of the same 66 items. **Agent model is the only variable** — judge,
prompt, k, retriever, embeddings, index and RAGAS version are identical.

Terminal failures (empty answer or recursion limit) are **counted as 0 in both
tables**, not excluded. This is the honest framing and it is not RAGAS's default;
see finding 1.

### Before — agent `gemini-2.5-flash-lite`

Terminal failures: **12 / 66** — 10 empty answers, 2 recursion limit.

| question type | n | faithfulness | answer relevancy | context recall |
|---|---|---|---|---|
| **all** | **66** | **0.7136** | **0.5547** | **0.5152** |
| single_hop | 17 | 0.7647 | 0.7053 | 0.7059 |
| numerical | 33 | 0.7677 | 0.5572 | 0.5152 |
| multi_hop | 1 | 0.0000 | 0.0000 | 0.0000 |
| comparative | 4 | 0.4167 | 0.1938 | 0.2500 |
| negative | 3 | 0.8889 | 0.3181 | 0.3333 |
| list | 8 | 0.5542 | 0.5631 | 0.3750 |

`context_recall` is scored from the retrieved passages alone, so RAGAS returns a
number for it even on items whose answer was blank. Eight of the twelve terminal
failures had retrieved the right passages and scored 1.0 on recall while
answering nothing. Zeroing them, as the rule above requires, moves overall recall
from the as-scored 0.6364 to **0.5152** — an earlier revision of this table
published the un-zeroed figure, which credited the run for evidence it never
used. The `multi_hop` row is the starkest case: n = 1, and that one item was an
empty answer on perfectly retrieved context.

### After — agent `gemini-3.1-flash-lite`

Terminal failures: **6 / 66** — 0 empty answers, 6 recursion limit.

| question type | n | faithfulness | answer relevancy | context recall |
|---|---|---|---|---|
| **all** | **66** | **0.8813** | **0.7625** | **0.6970** |
| single_hop | 17 | 0.8824 | 0.8319 | 0.8235 |
| numerical | 33 | 0.8485 | 0.7312 | 0.6970 |
| multi_hop | 1 | 1.0000 | 0.6640 | 1.0000 |
| comparative | 4 | 1.0000 | 0.6081 | 0.7500 |
| negative | 3 | 0.8889 | 0.9410 | 0.3333 |
| list | 8 | 0.9375 | 0.7666 | 0.5000 |

### Change

| question type | faithfulness | answer relevancy | context recall |
|---|---|---|---|
| **all** | **+0.1677** | **+0.2078** | **+0.1818** |
| single_hop | +0.1176 | +0.1266 | +0.1176 |
| numerical | +0.0808 | +0.1740 | +0.1818 |
| multi_hop | +1.0000 | +0.6640 | +1.0000 |
| comparative | +0.5833 | +0.4142 | +0.5000 |
| negative | 0.0000 | +0.6229 | 0.0000 |
| list | +0.3833 | +0.2034 | +0.1250 |

**Read the thin rows as anecdote, not measurement.** `multi_hop` is a single item
— its +1.00 is one question changing from failure to success, not a finding.
`negative` (n = 3), `comparative` (n = 4) and `list` (n = 8) are all too small to
generalise. Only `numerical` (33) and `single_hop` (17) carry real weight, and
both moved modestly: +0.08 and +0.12 faithfulness.

**Almost all of the headline gain is the elimination of terminal failures, not
better answers on items that already worked.** That is visible in the agent-effect
comparison in finding 5: on 8 items held under a single judge, changing the agent
moved faithfulness by only −0.045.

### Where the remaining failures are

All six `gemini-3.1-flash-lite` terminal failures are recursion-limit hits, all at
`msgs=20`, and all scored 0.0 on every metric:

```
qa_0003 single_hop   qa_0011 numerical   qa_0031 numerical
qa_0043 numerical    qa_0054 numerical   qa_0055 numerical
```

Unlike an empty answer, the recursion placeholder is *non-empty*, so RAGAS scored
it rather than dropping it — it entered the mean as a visible 0. That asymmetry is
the whole argument for the guard.

`context_recall` moves +0.18, and essentially all of that is the terminal-failure
rule rather than better retrieval: eight of the baseline's twelve zeroed items had
scored 1.0 on recall before the rule was applied. Compare like with like and
retrieval barely changed — on the 54 baseline items that were *not* terminal
failures, recall is 0.6296 against 0.6970 after. That is expected, because
retrieval, embeddings, index and k never changed; the only reason it moves at all
is finding 3.

---

## Instrument v2: per-chunk contexts, chunk labels and judge-free metrics

Results schema 3, September 2026. Everything in [Results](#results) was
measured with the schema-2 instrument and is left exactly as measured. The
re-baseline below re-scores the **same cached answers** under the new
instrument, so the two can be compared item for item; nothing was regenerated.

### What changed

| | Schema 2 | Schema 3 |
|---|---|---|
| Context unit | one tool observation, all k = 5 passages concatenated | one retrieved chunk, prefixed with its provenance (`[META 10-K, chunk 412]`) — finding 15 is why the prefix stays |
| Retriever scored against | the judge's reading of the blob | labelled chunk ids in [`benchmark_chunks.json`](benchmark_chunks.json) |
| Retrieval metrics | none | hit@k, recall@k (k = 5, 10, 25), MRR, nDCG@5 — no LLM call |
| Right-figure check | none | `figure_recall` / `figure_exact`: every ground-truth figure must appear in the answer; bare integers such as years must match exactly |
| Agent-level retrieval | none | `agent_hit_rate`: a labelled chunk appeared anywhere in the agent's own tool observations |
| Run identity | label + commit | SHA-256 of the resolved config; an identical configuration is reported, not re-run |
| Where runs land | `results/` | `results/` plus [`LEADERBOARD.md`](results/LEADERBOARD.md), regenerated on every run |

**Chunk labels.** [`chunk_labels.py`](chunk_labels.py) finds each item's
reference passage in the index by whitespace-normalised substring search. 66 of
71 locate verbatim, 3 by their first 60 characters, and 2 were resolved by hand
in [`chunk_labels_overrides.json`](chunk_labels_overrides.json) with the reason
recorded: `qa_0057`'s sentence straddles MSFT chunks 295–297 (the splitter
produced a two-word chunk, "Activision Blizzard", in the middle of it), and
`qa_0062`'s Microsoft cover-page reference straddles chunks 72–73, because
chunks 0–71 of that filing are XBRL context blocks the cleaner left in. A
reference that sits in several chunks — the 50-character overlap copies text
into a neighbour; a table row like "AWS 90,757 107,556 128,725" appears in
several tables — becomes one *any-of* group; 20 items have such a group.
Recall is measured over groups, so retrieving one of two overlap neighbours is
not a miss. The benchmark CSV was not edited.

**What the labels are not.** They mark the passage the benchmark author cited,
not every chunk that states the same fact. Finding 13 quantifies the gap.

### Retrieval alone, question sent verbatim

[`run_retrieval_eval.py`](run_retrieval_eval.py) sends each benchmark question
unchanged to the shipped dense retriever (all-MiniLM-L6-v2, ChromaDB, no
filter), asks for 25 chunks, and scores the ranked ids against the labels. 71
items, no LLM call, 27 s wall clock, 17.5 ms p50 per query. Evidence:
[`retrieval-dense-1e17cf5b5eab.json`](results/retrieval-dense-1e17cf5b5eab.json).

| stratum | n | hit@5 | recall@5 | MRR | nDCG@5 | recall@10 | recall@25 |
|---|---|---|---|---|---|---|---|
| **all** | **71** | **0.5070** | **0.4894** | **0.3569** | **0.3729** | **0.5880** | **0.6796** |
| single_hop | 17 | 0.5882 | 0.5882 | 0.5215 | 0.5187 | 0.7059 | 0.8824 |
| numerical | 33 | 0.5758 | 0.5758 | 0.4028 | 0.4368 | 0.6364 | 0.6970 |
| multi_hop | 1 | 1.0000 | 1.0000 | 0.2000 | 0.3869 | 1.0000 | 1.0000 |
| comparative | 4 | 0.5000 | 0.1875 | 0.1750 | 0.1345 | 0.1875 | 0.1875 |
| negative | 3 | 0.0000 | 0.0000 | 0.0417 | 0.0000 | 0.3333 | 0.3333 |
| list | 8 | 0.2500 | 0.2500 | 0.1845 | 0.1875 | 0.3750 | 0.3750 |
| temporal | 5 | 0.4000 | 0.4000 | 0.1364 | 0.1635 | 0.6000 | 0.9000 |
| requires_table = True | 26 | 0.4615 | 0.4615 | 0.2798 | 0.3059 | 0.5769 | 0.7115 |
| requires_table = False | 45 | 0.5333 | 0.5056 | 0.4015 | 0.4116 | 0.5944 | 0.6611 |

Half the time the retriever does not put a labelled chunk in the top 5, and on
a third of items it is not in the top 25 either. `comparative` items need
several passages and get 19% of them; `list` items fare little better. Items
that depend on a table trail the rest on every column. The `negative` row
scores retrieval of the passage that shows a fact is *absent*, which is a
weaker notion of relevance — read it as context for the refusal behaviour, not
as a retrieval failure in the usual sense. Thin strata are thin here as
everywhere: `multi_hop` is one item.

This table is the starting line for every retrieval variant in
[ROADMAP.md](../ROADMAP.md) item 1: a variant is measured here first, for
free, and only a winner is spent on with the judge.

### The same 71 answers, re-scored per chunk

The 71 `gemini-3.1-flash-lite` answers in the cache (prompt
`sha256:d1bedac20eb2`), re-judged by `gemini-3.6-flash` with contexts split
per chunk and prefixed with their provenance. Zero generation calls; 426
judge calls. Terminal failures (7, all recursion-limit) count as 0. Evidence:
[`baseline-v3-aea128d62403.json`](results/baseline-v3-aea128d62403.json).

| stratum | n | faithfulness | answer relevancy | context recall | figure_exact (n) | agent_hit |
|---|---|---|---|---|---|---|
| **all** | **71** | **0.8263** | **0.7639** | **0.7183** | **0.6346** (52) | **0.4366** |
| single_hop | 17 | 0.8824 | 0.8317 | 0.8235 | 1.0000 (8) | 0.6471 |
| numerical | 33 | 0.8232 | 0.7278 | 0.6970 | 0.5758 (33) | 0.4242 |
| multi_hop | 1 | 1.0000 | 0.6225 | 1.0000 | n/a | 1.0000 |
| comparative | 4 | 0.4583 | 0.5611 | 0.7500 | 0.3333 (3) | 0.2500 |
| negative | 3 | 0.8889 | 0.9517 | 0.3333 | 1.0000 (3) | 0.3333 |
| list | 8 | 0.9375 | 0.7614 | 0.5000 | n/a | 0.1250 |
| temporal | 5 | 0.7000 | 0.8528 | 1.0000 | 0.4000 (5) | 0.4000 |

**Do not read the "all" row against the 66-item `rerun66` table.** The item
sets differ (71 against 66), and 9 of the 71 cached answers are not the ones
`rerun66` scored: the four `comparative`, four `temporal` and the one
`multi_hop` item were regenerated on 11 September by the compare-fix runs in
finding 11, which is where the `comparative` faithfulness of 0.46 comes from.
The like-for-like comparison is the 62 answers that are byte-identical in
both runs, under the same judge:

| metric, 62 identical answers | schema 2 (blob contexts) | schema 3 (chunk + provenance) | items up / down |
|---|---|---|---|
| faithfulness | 0.8737 | 0.8602 | 0 / 2 |
| answer_relevancy | 0.7764 | 0.7744 | 12 / 11 |
| context_recall | 0.6935 | 0.6935 | 0 / 0 |

`context_recall` did not move on a single item. Splitting a blob into its
chunks changes what a reader thinks the score means, and changes nothing
about what the RAGAS judge returns — it attributes the ground truth's
sentences to the contexts as a whole either way. What made retrieval
measurable was the chunk labels and the retriever-only metrics, not the
split. `answer_relevancy` moved both ways within its known noise (finding 7),
and faithfulness lost two items to the same judge variance finding 15
observed on `qa_0044`. The instrument is validated: the schema-3 baseline
scores the same answers the same way, and now also says which chunks were
retrieved and whether the figure was right.

### Judge-free metrics on the cached answers

Computed by `attach_deterministic_metrics()` on the same 71 answers:

| | value | n |
|---|---|---|
| `figure_exact_rate` — every ground-truth figure present in the answer | **0.6346** | 52 items whose ground truth contains a figure |
| `figure_recall` — share of ground-truth figures present | 0.7231 | 52 |
| `agent_hit_rate` — a labelled chunk seen anywhere in the agent's tool observations | **0.4366** | 71 |
| `agent_recall` — share of reference groups the agent saw | 0.4296 | 71 |
| `agent_mrr` — by the agent's own first-seen order | 0.2942 | 71 |

Per-stratum values are in the schema-3 baseline results file and on the
leaderboard. Items whose ground truth has no figure (`list`, most `negative`)
are *not applicable* to the figure check and are reported as such, never as a
pass.

---

## Retrieval ablation

Every retrieval technique in [`retrieval/retriever.py`](../retrieval/retriever.py)
is a field on `RetrievalConfig`, defaulting to the shipped behaviour, and each
configuration below was scored with `run_retrieval_eval.py` — the benchmark
question sent verbatim, 25 chunk ids returned, compared against the chunk
labels. No LLM call was made for any row; the whole matrix took under fifteen
minutes of CPU. Latency is the median wall-clock per query on the development
machine (a laptop CPU), after a warm-up query; the free-tier Space has fewer
cores and should be assumed slower.

The matrix is regenerated from the results files by `python -m eval.ablation
--sort hit@5`, so it cannot disagree with them.

| configuration | hit@5 | recall@5 | MRR | nDCG@5 | recall@10 | recall@25 | table hit@5 | p50 ms | file |
|---|---|---|---|---|---|---|---|---|---|
| dense, rerank fetch=50, ticker=inferred | 0.634 | 0.606 | 0.496 | 0.507 | 0.672 | 0.750 | 0.500 | 892.8 | [dense-rerank-f50-tf-inferred](results/retrieval-dense-rerank-f50-tf-inferred-8f6ef861d7e7.json) |
| dense, rerank fetch=100 | 0.620 | 0.602 | 0.482 | 0.499 | 0.655 | 0.701 | 0.500 | 1620.1 | [dense-rerank-f100](results/retrieval-dense-rerank-f100-84fdb1858a25.json) |
| dense, rerank fetch=50 | 0.620 | 0.592 | 0.480 | 0.493 | 0.658 | 0.722 | 0.500 | 794.5 | [dense-rerank-f50](results/retrieval-dense-rerank-f50-1417a345c7b7.json) |
| dense, rerank fetch=100, ticker=inferred | 0.606 | 0.588 | 0.486 | 0.497 | 0.655 | 0.715 | 0.462 | 1719.4 | [dense-rerank-f100-tf-inferred](results/retrieval-dense-rerank-f100-tf-inferred-ad3ace274d29.json) |
| hybrid, rerank fetch=50, ticker=inferred | 0.606 | 0.588 | 0.489 | 0.498 | 0.680 | 0.739 | 0.462 | 1119.2 | [hybrid-rerank-f50-tf-inferred](results/retrieval-hybrid-rerank-f50-tf-inferred-2048c3da9784.json) |
| hybrid, rerank fetch=50, ticker=oracle | 0.606 | 0.588 | 0.489 | 0.498 | 0.680 | 0.739 | 0.462 | 1114.4 | [hybrid-rerank-f50-tf-oracle](results/retrieval-hybrid-rerank-f50-tf-oracle-a401ac0308ac.json) |
| hybrid, rerank fetch=50 | 0.592 | 0.574 | 0.471 | 0.482 | 0.637 | 0.711 | 0.462 | 1058.6 | [hybrid-rerank-f50](results/retrieval-hybrid-rerank-f50-0f8cb0cc6e95.json) |
| hybrid, rerank fetch=25 | 0.578 | 0.560 | 0.469 | 0.474 | 0.623 | 0.708 | 0.462 | 630.1 | [hybrid-rerank-f25](results/retrieval-hybrid-rerank-f25-955ff196d13f.json) |
| dense, rerank fetch=25 | 0.549 | 0.532 | 0.421 | 0.434 | 0.609 | 0.680 | 0.500 | 468.6 | [dense-rerank-f25](results/retrieval-dense-rerank-f25-c5ce21431f88.json) |
| dense, ticker=inferred | 0.535 | 0.518 | 0.400 | 0.411 | 0.630 | 0.722 | 0.538 | 58.4 | [dense-tf-inferred](results/retrieval-dense-tf-inferred-e3e48c7eb861.json) |
| dense, ticker=oracle | 0.535 | 0.518 | 0.400 | 0.411 | 0.630 | 0.722 | 0.538 | 59.9 | [dense-tf-oracle](results/retrieval-dense-tf-oracle-d313b1d8bbf6.json) |
| dense | 0.507 | 0.489 | 0.357 | 0.373 | 0.588 | 0.680 | 0.462 | 17.5 | [dense](results/retrieval-dense-1e17cf5b5eab.json) |
| hybrid, ticker=inferred | 0.507 | 0.500 | 0.404 | 0.405 | 0.651 | 0.750 | 0.346 | 303.8 | [hybrid-tf-inferred](results/retrieval-hybrid-tf-inferred-164c3493c177.json) |
| hybrid, rrf_k=20 | 0.493 | 0.486 | 0.357 | 0.368 | 0.623 | 0.708 | 0.269 | 276.1 | [hybrid-rrf20](results/retrieval-hybrid-rrf20-09ddab803fac.json) |
| hybrid | 0.479 | 0.472 | 0.357 | 0.363 | 0.623 | 0.708 | 0.269 | 257.9 | [hybrid](results/retrieval-hybrid-e22f0b7b3525.json) |
| hybrid, fetch=50 | 0.479 | 0.472 | 0.363 | 0.368 | 0.620 | 0.722 | 0.269 | 270.9 | [hybrid-f50](results/retrieval-hybrid-f50-9c97122ce86b.json) |
| hybrid, w=1.0/0.5 | 0.479 | 0.461 | 0.370 | 0.370 | 0.609 | 0.680 | 0.269 | 266.4 | [hybrid-sw0.5](results/retrieval-hybrid-sw0.5-7a07be6a37e4.json) |
| hybrid, w=1.0/2.0 | 0.465 | 0.458 | 0.336 | 0.357 | 0.556 | 0.570 | 0.308 | 269.3 | [hybrid-sw2](results/retrieval-hybrid-sw2-fafb15d81f6c.json) |
| bm25 | 0.352 | 0.352 | 0.304 | 0.298 | 0.458 | 0.570 | 0.154 | 241.5 | [bm25](results/retrieval-bm25-a308c41b72ea.json) |

**Read the rows against the baseline `dense` row (hit@5 0.507).** Findings 16
and 17 are what the matrix shows.

### The configuration that ships

`dense, rerank fetch=50, ticker=inferred`, set in the environment as

```
RETRIEVAL_RERANK=true
RETRIEVAL_FETCH_K=50
RETRIEVAL_TICKER_FILTER=inferred
```

| | dense (shipped before) | dense + rerank 50 + inferred ticker | change |
|---|---|---|---|
| hit@5 | 0.507 | **0.634** | +0.127 |
| recall@5 | 0.489 | **0.606** | +0.117 |
| MRR | 0.357 | **0.496** | +0.139 |
| nDCG@5 | 0.373 | **0.507** | +0.134 |
| recall@25 | 0.680 | **0.750** | +0.070 |
| hit@5, table items (n = 26) | 0.462 | 0.500 | +0.038 |
| hit@5, non-table items (n = 45) | 0.533 | **0.711** | +0.178 |
| p50 latency per query | 17.5 ms | 893 ms | 51× |

Item by item: the labelled chunk enters the top 5 on 15 items that dense
missed and leaves it on 6 that dense had (`qa_0006`, `qa_0015`, `qa_0020`,
`qa_0063`, `qa_0070`, `qa_0071`). Two of the six are `temporal` items whose
retrieved neighbour states the same figure in a different table — finding 13's
label-coverage caveat cuts both ways.

---

## Findings

Ranked. The first two are the ones worth your time.

### 1. An empty answer passed through four layers without being noticed

On `gemini-2.5-flash-lite`, 10 of 66 items returned a final message with no text.
Not a timeout, not a safety block, not an extraction bug — `finish_reason: STOP`
with empty content. The API deliberately returned nothing.

Every layer accepted it:

| Layer | What it did |
|---|---|
| Agent loop | Returned the empty string as a valid final answer |
| `POST /query` | Served HTTP **200** with `answer: ""` |
| `POST /query/stream` | Streamed a blank message **with source citations attached** |
| RAGAS | Scored `NaN` on faithfulness, then **excluded those items from the mean** |

The last one is the dangerous one. `NaN` is not zero. Dropping ten failures from a
66-item mean raised reported faithfulness from **0.7136 to 0.8411** — the system's
worst items improved its score by 0.13 by being unscoreable. Coverage was 84.9%,
and without a coverage check nothing in the output distinguishes that from a
complete column.

The user-facing version is worse than the metric version. A blank answer rendered
with source chips reads as *"the filings say nothing about this"* — a specific,
false and confident claim about SEC disclosures.

**Diagnosis.** The empty cases all stopped at `msgs=4`: one tool call, one
observation, then an empty terminal message. Stronger models iterate 2–9 tool
calls. Re-running the same 10 items with agent model as the only variable:

| Agent model | Empty | Avg messages | Avg answer |
|---|---|---|---|
| `gemini-2.5-flash-lite` | **10/10** | 4.0 | 0 chars |
| `gemini-3.1-flash-lite` | **0/10** | 6.8 | 225 chars |
| `gemini-3.6-flash` | **0/10** | 10.4 | 409 chars |

`gemini-3.1-flash-lite` is the **same size class** and fixed all ten, so this is
not a small-model capability ceiling. The trigger is specific to that model
generation in a multi-turn tool loop. Seven of the ten had `context_recall = 1.0`
— retrieval had already found the right passages when the generator gave up.

**The trigger was the model. The silence was the architecture.** A model-specific
quirk reached a published metric because nothing at any layer treated "no answer"
as a distinct outcome.

**Fixed.** `agent/financial_agent.py` now defines named terminal states
(`empty_answer`, `recursion_limit`) with typed exceptions, shared by every layer.
`/query` returns **502** with a generic message. `/query/stream` emits an `error`
event and no `token` or `done`. The eval harness records `terminal_failure` by
name, splits NaN into *terminal-failure* vs *unexplained*, and reports
`mean_failures_as_zero` alongside RAGAS's default. 33 tests in
[`tests/test_terminal_failures.py`](../tests/test_terminal_failures.py) cover both
directions.

**The same missing guard had a second symptom, on the endpoint the live UI uses.**
The recursion-limit placeholder — LangGraph's `"Sorry, need more steps to process
this request."` — is not empty, so the streaming endpoint's `if not answer.strip()`
check passed it straight through. Users would have seen that placeholder rendered
as a real answer with source chips. Same absent concept, opposite symptom, and it
was on `/query/stream`, not the less-used JSON route.

### 2. Faithfulness cannot detect a right-figure-wrong-year answer

Added after the 66-item runs, because the deployed demo was seen answering a
cloud-revenue comparison with both companies' **prior-year** figures. No item in
the benchmark asked a question with the fiscal year unstated, so the behaviour
was entirely unmeasured. Five `temporal` items now do
([`results/temporal5`](results/temporal5-07c8597.json), agent
`gemini-3.1-flash-lite`, judge `gemini-3.6-flash`).

**4 of 5 answered with the prior year.**

| id | question | answered | correct |
|---|---|---|---|
| qa_0067 | Microsoft Intelligent Cloud revenue | $109,433M — wrong line item *and* wrong year | $106,265M (FY2025) |
| qa_0068 | Google Cloud revenues | $43,229M (2024) | $58,705M (2025) |
| qa_0069 | Compare MSFT vs Alphabet cloud revenue | correct year | — |
| qa_0070 | Apple total net sales | $391,035M (FY2024) | $416,161M (FY2025) |
| qa_0071 | AWS net sales | $107,556M (2024) | $128,725M (2025) |

The agent's own system prompt instructs it to "search for the most recent data
available" when a query is ambiguous about the fiscal year. It does not.

**The measurement finding is the more important half.** Look at what the metrics
said about those four wrong answers:

| | faithfulness | answer_relevancy |
|---|---|---|
| qa_0067 (wrong figure + wrong year) | 0.00 | 0.87 |
| qa_0068 (prior year) | **1.00** | 0.85 |
| qa_0070 (prior year) | **1.00** | 0.91 |
| qa_0071 (prior year) | **1.00** | 0.84 |

**Faithfulness scored a perfect 1.00 on three of the four wrong answers, and
answer_relevancy never dropped below 0.84 on any of them.** Both metrics are
working exactly as defined. The prior-year figure *is* in the retrieved context,
so an answer quoting it is genuinely faithful to its source; and it *is*
topically responsive, so it is genuinely relevant. The answer is simply to a
different question than the one asked.

Only `context_recall` caught anything, and only on qa_0067 (0.00), where the
retrieved passage did not contain the ground-truth figure at all.

This is a blind spot in the metric suite, not a bug in it. Faithfulness answers
"is this grounded in what was retrieved?" — a question that a confidently wrong
year passes. Nothing in faithfulness, relevancy or recall asks "is this the
figure the question was about?" On a financial-research system, where quoting
last year's revenue as this year's is precisely the error that matters,
**the headline metrics would have reported this system as performing well.**

The temporal stratum exists so that failure is at least visible as a stratum
score. Closing it properly needs a metric with access to the ground-truth value
— an exact-match check on the expected figure — which is a measurement change
rather than a tuning change and is not attempted here.

Note qa_0069 answered with the correct year, while the same comparison put to
the deployed demo answered with prior-year figures for both companies. That is
finding 3 again: the agent composes its own query, and the behaviour is not
stable across runs. At n=5 this stratum is anecdote, not measurement.

### 3. Retrieval quality varies with the agent model at fixed k, embeddings and index

The agent writes its own search queries inside the ReAct loop, so query text is
model output. Running 8 identical items on two agent models — same retriever, same
embedding model, same index, same k, same prompt — **changed the retrieved
passages on 7 of 8**.

The clearest case, `qa_0047` (multi_hop, "what key personnel do Meta's operations
depend on, and what risks to that person are highlighted?"):

| | `gemini-2.5-flash` | `gemini-3.1-flash-lite-preview` |
|---|---|---|
| Top passage | META chunk 432 — generic key-personnel boilerplate | the Zuckerberg risk passage |
| Answer | "members of management, key engineering, product development…" | "specifically identifying Mark Zuckerberg… combat sports, extreme sports" |
| context_recall | **0.00** | 1.00 |
| faithfulness | 0.79 | 1.00 |

One model's query surfaced the passage the ground truth depends on; the other's
returned plausible-sounding boilerplate, from which the agent answered fluently and
wrongly. Fluent wrongness on a missed retrieval is the most dangerous failure mode
a financial Q&A system has.

**Consequence:** the retriever is not the whole retrieval system. Query composition
is part of it, it is currently unmeasured and untuned, and it is the largest
uncontrolled variable behind every number in this document. Measuring query
stability is item 2 on the [roadmap](../ROADMAP.md).

### 4. Cross-family judging is worth adopting as standard practice — on a signal, not a proof

The same 20 agent outputs, scored by two judges from different model families
(`gemini-3.6-flash` and Groq `openai/gpt-oss-120b`), zero new generation calls:

| metric | Gemini judge | Groq judge | mean Δ | mean abs Δ | agree within 0.1 |
|---|---|---|---|---|---|
| faithfulness | 0.8333 | 0.7726 | −0.061 | 0.061 | 80% |
| answer_relevancy | 0.7639 | 0.7500 | −0.014 | 0.033 | 95% |
| context_recall | 0.7500 | 0.7250 | −0.025 | 0.025 | 95% |

The judges broadly agree. Groq is consistently slightly harsher, never kinder on
average, and relevancy and recall agree within 0.1 on 19 of 20 items.

The disagreement is concentrated rather than spread: on all **three** comparative
items the Gemini judge gave faithfulness 1.00 and the Groq judge gave 0.71–0.75. A
Gemini judge awarding a flat perfect score to every cross-company answer produced
by a Gemini agent, on the hardest question type, is the shape same-family judge
bias would take.

**It is a signal, not a proof, and the distinction matters.** n = 3. Three items
can line up by chance, the comparative stratum is the smallest in the benchmark,
and no significance test was run because none would mean anything at that size.
What the result justifies is a *practice* — score with a judge from a different
family than the generator, because here it costs nothing and the one place the
judges diverged is exactly where a same-family judge would be least trustworthy.
It does not justify the claim that the Gemini judge is biased.

Useful negative control: the six recursion-limit items scored 0.0 under **both**
judges, identically.

### 5. Separating judge effect from agent effect

The 8-item and 66-item runs differ in agent model, judge model *and* item set, so
their headline numbers are not directly comparable. Holding the judge fixed (Groq)
across 8 shared items isolates agent effect:

| metric | agent `gemini-2.5-flash` | agent `gemini-3.1-flash-lite` | Δ |
|---|---|---|---|
| faithfulness | 0.9554 | 0.9107 | −0.045 |
| answer_relevancy | 0.9029 | 0.8453 | −0.058 |
| context_recall | 0.8125 | 0.9375 | +0.125 |

Judge effect ≈ 0.03–0.06; agent effect on shared items ≈ 0.05, mixed sign.
**Neither is large enough to explain the +0.168 improvement across the full 66.**
That gain came from eliminating terminal failures, not from better answers on items
that already worked — consistent with the per-stratum deltas above.

Caveat: these 8 were the original smoke set, not a random draw.

### 6. A repo verified reproducible one day was unreproducible the next

**9 September 2026.** Cold-clone check passed: fresh `git clone`, README followed
verbatim, ingestion built the index in 1,531 s producing **exactly 67,521 chunks**,
matching development chunk-for-chunk. All 22 direct dependencies pinned the same
day.

**10 September 2026, under 24 hours later.** Google withdrew `gemini-2.5-flash`:

```
404 NOT_FOUND — "This model models/gemini-2.5-flash is no longer available to
new users. Please update your code to use models/gemini-3.6-flash"
```

Both agent entry points defaulted to that exact id. **A cold clone that did not set
`LLM_MODEL` would have hard-404'd on its first query.** Nothing in the repository
changed.

The failure was selective in the least helpful direction: the withdrawal is scoped
to *new users*, so the deployed Space — an existing consumer on its own project —
kept working. The repo was broken for anyone cloning it while the demo looked fine.

**Pinning does not cover this.** `requirements.txt` pins packages resolvable from
an immutable index. A model id is a service endpoint addressed by name, whose
availability is a vendor policy decision. There is no lockfile for it, and CI did
not catch it because the CI suite deliberately makes no LLM calls — it stayed green
throughout.

Adopted in response: defaults are now a *measured* choice rather than an inherited
one; every results file records exact model ids; historical references are
annotated rather than rewritten; and a reproducibility claim is dated, because
"verified reproducible" without a date is a claim about a moment presented as a
property.

### 7. `answer_relevancy` is not deterministic at temperature 0

`ragas.llms.base.BaseRagasLLM.get_temperature` returns `0.3` whenever `n > 1`, and
`LangchainLLMWrapper.agenerate_text()` **overwrites the model's configured
temperature** with it. `answer_relevancy` always requests `strictness = 3`
generations, so its judge calls run at 0.3 regardless of what the harness sets:

```python
# ragas/llms/base.py
def get_temperature(self, n: int) -> float:
    return 0.3 if n > 1 else 0.01
```

That is deliberate on RAGAS's part — the metric is *defined* over a diverse sample
of counter-questions — so it is recorded rather than suppressed. Every results file
carries `judge_temperature_configured: 0.0` beside a `judge_temperature_note`
stating the override.

**Practical consequence:** faithfulness and context_recall are reproducible
run-to-run; `answer_relevancy` is not. Do not read small relevancy differences as
signal.

An earlier judge, `gemini-3.1-flash-lite-preview`, also returned **one** candidate
for an `n = 3` request and returned it malformed, producing NaN. `bypass_n=True`
now makes RAGAS issue N separate single-candidate requests rather than trusting a
provider to honour `n`, so that degradation cannot recur silently on any provider.

### 8. A throttled harness can fabricate its own missing data

An early run scored faithfulness on only 5 of 8 items. The cause was `TimeoutError`
inside `ragas.executor` at its default 180 s per-job timeout: faithfulness makes
**two sequential** judge calls, each queuing behind every other in-flight job in a
shared rate limiter. Under a 0.1 rps throttle that exceeds 180 s. The harness's own
quota discipline was manufacturing NaN.

Fixed by raising the timeout to 900 s, dropping `max_workers` from 16 to 2 (extra
workers add no throughput behind a shared limiter — they only lengthen each job's
wait), and absorbing 429s with `max_retries=8`. Re-scoring produced 100% coverage.

The harness now counts executor failures into `judge_diagnostics` and labels a
`TimeoutError` as a harness artefact in its own output, because a NaN caused by our
throttling is indistinguishable, in the results file, from a judge that genuinely
could not score an item — and those mean opposite things.

Validated under real load: the Groq cross-judge pass absorbed **42 rate-limit
429s** with zero NaN and 100% coverage.

### 9. `context_recall` is coarser than it looks

`_extract_contexts()` captures each tool observation as **one** context string, and
an observation already concatenates all k = 5 passages into ~2,100–2,600
characters. RAGAS therefore scores against observation-sized blobs, not individual
chunks: a blob containing one relevant passage among five scores as recalled.

This was deliberately not changed at the time — splitting contexts per chunk
alters what every score in the [Results](#results) section means, and it
belonged in its own pass with a re-baseline.

**Closed by instrument v2.** Results schema 3 splits every observation into
its chunks, stores the raw observation so either view can be recomputed, and
scores the retriever directly against labelled chunk ids rather than through
the judge. The schema-2 tables above are kept as measured and are listed
separately on the leaderboard; their `context_recall` is not comparable with
schema-3 runs. See [Instrument v2](#instrument-v2-per-chunk-contexts-chunk-labels-and-judge-free-metrics)
and findings 12–15.

### 10. My own free-tier quota estimate was wrong by ~50×

The initial survey estimated Gemini's free tier at ~1,000 requests/day and
projected a 66-item run at 45–60 minutes. The real ceiling was **20 requests per
day, per model, per project** — measured from a live 429 body, because Google no
longer publishes per-model free-tier numbers. The real projection was ~9 days.

What surfaced it was cost, not review: a diagnostic burned an entire daily bucket
in one command and the 429 carried the true limit. Two things generalise — a quota
assumption is a measurement and should be taken from the API rather than from
memory; and diagnostics consume the budget the real run needs, so they belong on a
model the reported run does not use.

This constraint was later removed by billing activation, which is why this document
reports 66 items rather than 8. The earlier run is preserved below.

### 11. A defensible code fix that the metrics scored as a regression

`compare_companies` prefixed every retrieval query with the literal string
`"total net sales"`, so *"which two are incorporated outside Delaware"* was
embedded as *"total net sales which two are incorporated outside Delaware"*.
The prefix assumed all comparisons are about revenue. Removing it is not a
judgement call — it is deleting an injected term that corrupts the query the
agent composed.

Measured on the 10 items that route through that tool (comparative, temporal,
multi_hop), run **twice** to separate the effect from the run-to-run variance in
finding 3:

| | before | after (run 1) | after (run 2) |
|---|---|---|---|
| faithfulness | 0.900 | 0.650 | 0.633 |
| answer_relevancy | 0.733 | 0.738 | 0.724 |
| context_recall | 0.800 | **0.900** | **0.900** |

The two after-runs agree closely, so the drop is reproducible, not noise.
Retrieval — the thing the change actually touches — improved and stayed
improved. Faithfulness fell by a quarter.

Decomposing the fall, per item:

- **`qa_0062` improved and was scored down.** Before, the agent returned a false
  refusal: *"the filings do not contain information regarding the state of
  incorporation"* — faithfulness **1.00**, because a refusal makes no claims to
  verify. After, it answered *"Apple and Microsoft… Apple is incorporated in
  California"*, which **is the ground truth** — faithfulness **0.33**. The fix
  turned a wrong answer into a right one and the metric marked it down. This is
  finding 2's blind spot from the other direction: faithfulness rewards
  declining to answer.
- **`qa_0063` genuinely regressed.** It now reports Microsoft's total revenue as
  $371,902M; the correct figure is $281,724M. A real wrong number.
- **`qa_0060` now exhausts the recursion limit** in both after-runs, where before
  it answered in 4 messages. Different retrieval, more exploration, over budget.
- **`qa_0067` retrieval improved outright** (context_recall 0.00 → 1.00) and the
  figure it quotes is now correct, though it still mislabels the fiscal year —
  finding 2 again, untouched by this change.

**What the prefix was actually doing, found by testing the live demo.** The
removal was verified end to end after deployment, and the picture sharpened:

- The target case now works. *"Among Apple, Amazon, Alphabet, Meta, and
  Microsoft, which two are incorporated outside Delaware?"* returns *"Apple and
  Microsoft… Apple is incorporated in California, and Microsoft is incorporated
  in Washington. Alphabet, Amazon, and Meta are all incorporated in Delaware"* —
  the exact ground truth, identical across two runs. Before removal this same
  question produced a false refusal.
- A revenue comparison that previously worked now does not. *"Compare Microsoft
  and Alphabet cloud revenue in their most recent fiscal years"* returned
  Microsoft $106,265M (FY2025) and Alphabet $58,705M (2025) before; it now
  returns Microsoft's FY2024 figure and fails to locate Google Cloud revenue at
  all. Reproduced twice.

So the prefix was **wrong in general and helpful by accident**: injecting
"total net sales" corrupted every non-revenue comparison, while steering revenue
comparisons toward the right tables. Removing it trades one failure mode for
another rather than eliminating one.

That is a real finding about the retrieval design, not a reason to reinstate a
hardcoded assumption. What it argues for is the roadmap's item 1 — a retriever
whose query is not silently rewritten, measured by an instrument that can tell
two retrieval strategies apart — rather than choosing which set of questions to
break.

**The change was kept.** The prefix is indefensible on inspection and demonstrably
caused at least one false refusal; reverting a correct fix because a coarse
metric dislikes it would be letting the instrument drive the engineering. But
this is explicitly **not** reported as an improvement: the headline metric went
down, one item produces a wrong figure that did not before, and `comparative` is
n = 4. Nothing here is conclusive in either direction.

This is the strongest argument so far for the roadmap's thin-strata item. A
four-item stratum cannot adjudicate a retrieval change, and two of the four
movements above are metric artefacts rather than quality changes.

### 12. The retriever alone finds a relevant chunk half the time, and the agent's own queries do worse

Instrument v2 measures retrieval two ways on the same 71 items. Sending the
benchmark question verbatim to the dense retriever puts a labelled chunk in
the top 5 on **50.7%** of items (`hit@5`, retriever alone, k = 25 fetched).
Letting the agent compose its own queries inside the ReAct loop and looking at
*everything* it retrieved across all its tool calls — usually more than five
chunks — a labelled chunk appears on **43.7%** (`agent_hit_rate`). The agent
had more retrieval attempts and a wider net, and still saw the right page less
often, because the query it typed was worse than the question it was asked.

That is finding 3 with a number on it. Retrieval quality has two parts, the
retriever and the query composition, and both are now measured separately:
`run_retrieval_eval.py` moves only with the retriever, `agent_hit_rate` moves
with both. When a retrieval change improves the first and not the second, the
query is the problem, not the index.

Where the retriever is weakest is also where the questions are hardest:
`comparative` items retrieve 19% of their reference passages at k = 5,
`list` items 25%, and items that depend on a table trail the rest on every
metric (hit@5 0.46 against 0.53). Chunks cut mid-table are a plausible cause
and now a testable one.

### 13. Chunk labels are a lower bound: 12 items were answered correctly without a labelled chunk

On 12 of 71 items the agent's answer contains every ground-truth figure
(`figure_exact = True`) yet none of its tool observations contained a labelled
chunk (`agent_hit = False`): `qa_0001`, `qa_0009`, `qa_0019`, `qa_0020`,
`qa_0032`, `qa_0033`, `qa_0044`, `qa_0048`, `qa_0065`, `qa_0066`, `qa_0068`,
`qa_0071`. `qa_0001` is the plainest case: the label is the cover page
(`AAPL_10K_chunk_0`, "For the fiscal year ended September 27, 2025"); the
agent retrieved five chunks from the body of the filing, none of them the cover
page, and answered from a passage that states the same date.

The labels mark the passage the benchmark author cited, not every chunk that
states the fact, so every retrieval score in this document is a **lower
bound**: some misses are the retriever finding a different, equally valid
passage. Two consequences. First, the figure check, not the retrieval score,
is the metric that says whether the answer was right. Second, exhaustive
answer-bearing labels would tighten the retrieval numbers, and were
deliberately not attempted yet, because labelling chunks by whatever the
retriever returns is how a label set drifts toward the retriever it is meant
to test. See [ROADMAP.md](../ROADMAP.md).

### 14. The figure check catches the wrong-year answers faithfulness passed — including one it can only catch through the year

Finding 2 recorded four `temporal` answers that quoted the prior year's
figure and scored 1.00 on faithfulness. Under the figure check, on the cached
answers:

| id | answer quotes | ground-truth figures missing from the answer | `figure_exact` |
|---|---|---|---|
| qa_0067 | the correct $106,265M, **labelled "Fiscal Year 2024"** | `2025` | False |
| qa_0068 | $43,229M (prior year) | `$58,705 million` | False |
| qa_0069 | MSFT correct, Alphabet $43,229M (prior year), both labelled 2024 | `2025`, `$58,705 million` | False |
| qa_0070 | $391,035M (prior year) | `$416,161 million`, `2025` | False |
| qa_0071 | $128,725M for 2025 and $107,556M for 2024, both correctly labelled | — | True |

Three of the five are caught on the dollar figure. `qa_0067` is the
interesting one: the answer contains the *right* number and attaches it to
the *wrong* year, so a check on the money alone would pass it. It fails only
because the ground truth's `2025` is treated as a figure that must match
exactly. An earlier draft of the check applied the same 0.05% relative
tolerance to every figure, which for a year is about ±1.0 and let 2024 pass
for 2025; the tolerance now applies only to scaled amounts, and bare integers
must be equal. That draft would have reported this system as right on
`qa_0067`.

Two limits, stated plainly. The check tests *presence*: `qa_0071` lists both
years' figures and passes, which is correct here because it labels them
correctly, but an answer that listed both figures and mislabelled them would
also pass unless a year was missing. And it is only as good as the ground
truth's wording: a ground truth that mentions a prior-year figure for context
(`qa_0005`) demands that figure of the answer too, which is why `figure_recall`
is reported next to `figure_exact`. The verbatim-citation check planned for the
output verifier is the tighter instrument; this one exists because it costs
nothing and already tells the temporal stratum apart from a pass.

### 15. Stripping the provenance header from a context cost 0.13 faithfulness on identical answers

The first schema-3 re-score split each observation into bare chunk text —
the passage with its `[1] ticker=META  chunk_idx=412` header removed. On the
62 items whose cached answer is byte-identical to the schema-2 run, with the
same judge, `context_recall` did not move on a single item and
`answer_relevancy` moved within noise; **faithfulness fell from 0.8737 to
0.7473**, nine items down, seven of them from 1.00 to 0.00, none up.

The cause is what the header carried. Chunk text almost always says "the
Company"; the header is what says *which* company. An answer that begins
"Meta's total revenue for 2025 was…" is a claim about Meta, and a judge
handed a passage that never names Meta cannot verify it. The agent, however,
*did* have the header — it is part of the observation it read — so the bare
chunk under-represents the evidence the agent worked from.

Re-judging the nine dropped items with each context prefixed
`[META 10-K, chunk 412]`, nothing else changed:

| id | schema 2 (blob) | schema 3, bare chunk | schema 3, chunk + provenance |
|---|---|---|---|
| qa_0013, 0015, 0038, 0041, 0042, 0051, 0057 | 1.00 | 0.00 | **1.00** |
| qa_0040 | 1.00 | 0.50 | **1.00** |
| qa_0044 | 1.00 | 0.67 | 0.00 |

Eight of nine recover completely. `qa_0044` moved the other way, which is the
judge's run-to-run variance (finding 7), not the prefix. Across all 62
identical answers the full re-score with the prefix lands at 0.8602 against
the schema-2 0.8737 — two items down, none up — where the bare-chunk run had
landed at 0.7473. The provenance prefix
is therefore the context format for schema 3, and `context_format` is part of
the hashed configuration so the two formats can never share a results file.
The bare-chunk run is kept as
[`baseline-v3-plain-4a3f267adb41.json`](results/baseline-v3-plain-4a3f267adb41.json),
labelled as such, because it is the measurement behind this finding.

What it says beyond this repository: a faithfulness score depends on the
serialisation of the context as much as on its content, and a harness that
changes how contexts are formatted has changed the metric. Report the format
with the number.

### 16. A cross-encoder over 50 dense candidates is worth 12 points of hit@5; 100 candidates buys nothing more

Re-ordering the dense retriever's top 50 with
`cross-encoder/ms-marco-MiniLM-L-6-v2` and keeping the best 5 moves hit@5
from 0.507 to 0.620 and MRR from 0.357 to 0.480, every stratum with n > 4
included: `single_hop` 0.588 → 0.824, `numerical` 0.576 → 0.667, `list`
0.250 → 0.375. Over 25 candidates the gain is a third of that (0.549); over
100 it is the same as 50 (0.620) at twice the latency (1.6 s against 0.8 s).
The relevant chunk, when dense retrieval finds it at all, is almost always in
its top 50: the reranked list's recall@25 is 0.722 at fetch 50 and 0.701 at
fetch 100, so a deeper fetch only gives the cross-encoder more wrong
candidates to be confused by.

The gain is uneven in an informative way. Non-table items go 0.533 → 0.689;
table items 0.462 → 0.500. A cross-encoder reads prose well and a run of
numbers badly, and the 512-character chunks cut tables mid-row, so the row
that answers a table question often does not carry the label that names it.
Tables remain the weak stratum, and the lever for them is chunking, not
ranking (ROADMAP item 1).

Cost: 0.8 s of CPU per query on the development machine, against 18 ms for
dense alone. An agent run makes two to four retrieval calls. On the free-tier
Space, with fewer cores, expect the reranker to add several seconds to a
question that already takes ten to thirty.

### 17. Hybrid BM25 fusion loses to dense on this corpus; the inferred ticker filter is free and equals the oracle

**BM25 alone** puts a labelled chunk in the top 5 on 35% of items. **Fused
with dense** by Reciprocal Rank Fusion it lands at 0.479, *below* dense's
0.507, and the gap is concentrated where BM25 is weakest: table items fall
from 0.462 to 0.269. Sweeping the fusion (rrf_k 60 → 20: 0.493; sparse
weight 0.5: 0.479; sparse weight 2.0: 0.465; fetch 50: 0.479) does not
recover it, and feeding the reranker hybrid candidates instead of dense ones
scores lower too (0.592 against 0.620 at fetch 50). Hybrid's one advantage is
depth — recall@25 0.708–0.750 against 0.680 — which never reaches the top 5.

Why, on a corpus that is supposedly full of exact tokens: a 10-K table row
is a line-item name followed by three years of figures, and the question
names the line item and a year. "2025", "million", "total", "revenue" appear
in thousands of chunks, so IDF gives them nothing, and the figure itself is
not in the question. Meanwhile the MSFT filing contributes half the index
(32,886 chunks, largely exhibits), all of it eligible for exact-token
matches on generic terms. BM25 has the right shape for a different corpus.
This is a measured negative result and the hybrid mode stays in the code,
off by default, because the measurement is what says it should be off.

**The ticker filter.** Restricting search to the one company the question
names (`retrieval/tickers.py`, a five-entry alias list) produces the same
numbers as the oracle filter that reads the benchmark's ticker column, on
every metric, in every configuration tried — the files are identical row for
row. Every single-company question in this benchmark names its company, so
inference is exact here; multi-company questions correctly get no filter. It
adds 0.028 to dense alone (0.535) and 0.014 on top of reranking (0.634), for
about 40 ms. It is on in the shipped configuration.

**Not measured, and why.** Table-aware chunking needs a re-ingested index and
therefore new chunk labels; it is the next lever and its own pass. Query
rewriting (HyDE, multi-query) costs an LLM call per retrieval and was not
measured within this budget. A larger reranker (`BAAI/bge-reranker-base`,
1.1 GB) was not measured; the small one already saturates at fetch 50 and
the free-tier Space has 16 GB of RAM to share with everything else.

---

## Prior result: the n = 8 run

Kept deliberately. The sequence matters: the quota constraint was real, it was
measured off 429 bodies rather than assumed, it was documented honestly, and then
it was removed.

8 items, agent `gemini-2.5-flash` (**since deprecated by Google, September 2026** —
the API now returns 404 "no longer available to new users", so this run is no
longer reproducible as measured), judge `openai/gpt-oss-120b` (Groq). Complete:
8/8 generated, 8/8 scored, 100% metric coverage, zero NaN.

| Metric | Mean | n | NaN |
|---|---|---|---|
| faithfulness | 0.9554 | 8 | 0 |
| answer_relevancy | 0.9029 | 8 | 0 |
| context_recall | 0.8125 | 8 | 0 |

Evidence: [`results/smoke8-69c426f.json`](results/smoke8-69c426f.json).

**Do not compare these numbers directly with the 66-item tables.** Different agent
model, different judge, different item set — and the 8 items were the smoke set,
which is not a random draw. Finding 4 is the only bridge between them.

---

## Method and provenance

Every value is also stamped into `config` in each results file, so a score can
never be separated from the configuration that produced it.

| | Before run | After run | Cross-judge | Schema-3 baseline | Retrieval (winner) |
|---|---|---|---|---|---|
| Agent model | `gemini-2.5-flash-lite` | `gemini-3.1-flash-lite` | `gemini-3.1-flash-lite` | `gemini-3.1-flash-lite` (cached answers) | none |
| Judge model | `gemini-3.6-flash` | `gemini-3.6-flash` | `openai/gpt-oss-120b` (Groq) | `gemini-3.6-flash` | none |
| Items | 66 | 66 | 20 | 71 | 71 |
| Judge calls | 386 | 396 | 121 | 426 | 0 |
| Contexts | observation blobs | observation blobs | observation blobs | chunk + provenance | chunk ids vs labels |
| Results file | [`baseline66`](results/baseline66-af83fa6.json) | [`rerun66`](results/rerun66-af83fa6.json) | [`crossjudge20`](results/crossjudge20-af83fa6.json) | [`baseline-v3`](results/baseline-v3-aea128d62403.json) | [`dense-rerank-f50-tf-inferred`](results/retrieval-dense-rerank-f50-tf-inferred-8f6ef861d7e7.json) |

Held constant across all three: k = 5, agent temperature 0, agent recursion limit
20, prompt version `sha256:d1bedac20eb2` (a hash of the live prompt text, so it
cannot drift out of sync with the prompt it names), embeddings
`sentence-transformers/all-MiniLM-L6-v2` (384-dim, used for both retrieval and the
judge's relevancy comparison), corpus of 5 × FY2025 10-K filings at 67,521 chunks
of 512 chars / 50 overlap, RAGAS 0.4.3 with seed 42, `max_workers` 2, 900 s
per-job timeout, `bypass_n=True`.

**Seeds do not make this deterministic and no configuration would.** RAGAS's
`seed=42` governs its own sampling, not an LLM judge's output. See finding 7.

**The harness is checkpointed and resumable.** Agent outputs are cached after every
item, keyed on `(item id, agent model, prompt version)`, so a quota wall costs one
item rather than a run, and `--score-only` re-judges from cache at zero generation
cost — which is how the cross-family check cost nothing. A partial run withholds
aggregates in stdout *and* sets `"aggregates": null` in the JSON, so a stopped run
cannot be mistaken for a finished one.

**Provenance note.** All three results files record `git_commit: af83fa6` with
`git_dirty: true` — they were produced by the harness as it stood before the commit
that added the guard. The dirty bit is recorded rather than hidden.

### Reproducing this

```bash
# No API calls — validates benchmark parsing, argparse and imports. This is CI.
python -m eval.run_eval --dry-run
python -m eval.run_retrieval_eval --dry-run

# Instrument v2, no LLM calls: label the benchmark against the local index
# (~10 s), then score the retriever alone (~30 s). Both write to eval/results/
# and regenerate LEADERBOARD.md.
python -m eval.chunk_labels
python -m eval.run_retrieval_eval --label dense

# The schema-3 re-baseline: the cached answers re-scored per chunk. Zero
# generation calls; ~430 judge calls. LLM_MODEL must name the agent whose
# answers are cached.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval \
  --score-only --judge-provider google --judge-model gemini-3.6-flash --label baseline-v3

# The reported schema-2 66-item run (kept for the record; writes schema 3 now).
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval \
  --judge-provider google --judge-model gemini-3.6-flash --label rerun66

# Cross-family re-score from cache — zero generation calls.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval \
  --score-only --judge-provider groq --label crossjudge20

# Rebuild the leaderboard from every results file.
python -m eval.leaderboard
```

A run whose resolved configuration already has a complete results file prints
that file's report and exits; `--force` runs it again. Credentials are read
from the environment only, never accepted as flags.

---

## Limitations

Including the ones that weaken the numbers above.

1. **n = 66 establishes no statistical significance.** No confidence intervals are
   computed because none would be meaningful at this size; no significance test is
   reported because none was run.
2. **Four of six strata are n ≤ 8.** `multi_hop` is a single item — in the full
   benchmark, not just a sample — so a per-type multi-hop finding needs more
   *items*, not more quota. `negative` (3), `comparative` (4) and `list` (8) are
   all too thin to generalise. Only `numerical` (33) and `single_hop` (17) support
   any reading, and both moved modestly.
3. **The headline improvement is mostly failure elimination, not answer quality.**
   +0.168 faithfulness across 66 items versus −0.045 on 8 items under a fixed
   judge. If you care about answer quality on items that already worked, this
   evaluation shows very little movement.
4. **Five companies only** — AAPL, MSFT, GOOGL, AMZN, META, all large-cap US
   technology, all FY2025 10-Ks. Nothing here speaks to other sectors, smaller
   filers, older filings or other filing types.
5. **One embedding model**, used for both retrieval and the judge's relevancy
   comparison. That is self-contained and convenient, and it also means the judge
   shares the retriever's blind spots.
6. **No retrieval-variant comparison yet.** One k, one chunk size, one splitter,
   one strategy. Nothing was varied, so nothing here says any of those choices
   is good. Instrument v2 makes a comparison measurable (finding 12); none has
   been run.
7. **The judge-bias result is n = 3** on the comparative stratum. A signal, not a
   proof (finding 4).
8. **`answer_relevancy` is not reproducible to the third decimal** (finding 7).
9. **Chunk labels are reference-passage labels, so every retrieval score is a
   lower bound** (finding 13). Schema-2 results files additionally scored
   `context_recall` over observation-sized blobs (finding 9) and are not
   comparable with schema-3 runs; the leaderboard keeps them apart.
10. **Single judge per run**, with no inter-judge agreement measured beyond the
    20-item cross-family check. Every headline number is one model's opinion.
11. **Scores are comparable only within a pinned judge model id.** Free-tier and
    preview models are retired without notice — this project lost its agent model
    mid-work (finding 6) and had a judge model change behaviour mid-project. A
    future re-run against a different judge is a new baseline, not a continuation.
12. **The deployed demo is a manually synced copy.** The Hugging Face Space is a
    separate repository, not built from this one on every push. Its application
    code currently matches `main`, including the agent model and the
    terminal-failure guard, but the deployment machinery differs by design (it
    ships a prebuilt Chroma index via Git LFS). Because the sync is manual it can
    drift again, so the revision serving any given demo session is not guaranteed
    to be the revision measured here. See the README.
13. **Open dependency advisories are tracked rather than auto-patched.** `npm
    audit` now reports 0 vulnerabilities — the test-runner devDependency chain
    was cleared by moving to Node 22 and vitest 4. What remains is Python-side:
    four ChromaDB advisories and one `ragas` advisory, none of which has a
    patched release upstream, so no version bump clears them. See the README's
    limitations for the ChromaDB index-compatibility constraint.
14. **The schema-3 baseline re-scores cached answers; it is not a fresh
    generation.** The same 71 answers the schema-2 tables scored were re-judged
    per chunk, so the two instruments are compared on identical outputs — and
    no new sample of the run-to-run variance in findings 2 and 3 was taken.
    A retrieval variant will be compared against a baseline generated in the
    same session, not against this file.
15. **The figure check tests presence, not attribution** (finding 14). An
    answer that quotes the right figure and the wrong one side by side passes
    unless a year is missing. The verbatim-citation verifier is the tighter
    instrument and is not built yet.
