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

**What this evaluation does not establish:** n = 71 on the headline runs (66 on
the before/after model comparison) supports no statistically significant claim,
five of seven question-type strata are n ≤ 8, and every headline score is one
judge's opinion. See [Limitations](#limitations).

**Instrument v2 (results schema 3), added after the runs above.** Contexts are
now scored per chunk instead of per tool observation, every item is labelled
with the chunk(s) that hold its reference passage, the retriever is scored on
its own with no LLM call, and a deterministic figure check scores whether the
answer quoted the right number. The section [Instrument v2](#instrument-v2-per-chunk-contexts-chunk-labels-and-judge-free-metrics)
has the re-baseline; findings 12–15 are what it showed. The headline from it:
**the dense retriever alone puts a relevant chunk in the top 5 on 50.7% of
items, and the agent's own query wording does worse, 43.7%.**

**Upgrades 2–6, built on that instrument.** Nineteen retrieval configurations
were scored for free and one was bought with the judge: dense search over 50
candidates, reranked by a cross-encoder under an inferred ticker filter, takes
hit@5 from 0.507 to **0.662** on the current index ([Retrieval ablation](#retrieval-ablation)).
The tagged XBRL facts became a lookup tool and a calculator, and the answer
contains the figure the question asked for on **100%** of items with one, up
from 73% on the dense baseline, with faithfulness 0.826 → **0.957**
([Structured facts](#structured-facts-exact-figures-and-a-calculator)). A
meter on every run prices the result: p50 1.9 s and **$0.0017 per query**
([Cost and latency](#cost-and-latency)). Every answer is then turned into
cited claims and verified against what was retrieved before it is served —
**70 of 70** verified on the first attempt, at +$0.0004 and +2.2 s per query
([Output contract](#output-contract-and-fail-closed-verification)) — and the
retrieval numbers are a gate on every pull request
([CI quality gate](#ci-quality-gate)). Findings 16–21 are what those showed.

**The index was rebuilt after a review (finding 22).** The cleaner had read
every document in each EDGAR submission — exhibits, XBRL taxonomy files and
the HTML-escaped XBRL instance — and 74% of the 67,521-chunk index was
escaped markup, XBRL identifiers or MetaLinks JSON; 23% was prose. It now reads the 10-K document alone: 4,783 chunks, 86% prose,
built in 72 seconds instead of 26 minutes. **Every retrieval number in this
document is on the rebuilt index.** The judged runs that built the tools
(findings 16–21) predate it and are marked as such; the shipped
configuration was then judged on the rebuilt index (finding 22):
faithfulness 0.949, answer relevancy 0.890, context recall 0.845
against 0.957 / 0.894 / 0.852 on the first index — inside the run-to-run
variance finding 11 measured. The corpus fix changed what retrieval finds,
not what the model answers.

**Tool calls, batching and conversation memory (findings 23–25).** The tool
calls themselves are now measured ([Tool-call quality](#tool-call-quality)): the
meter records each call's arguments and start time, and a label file written
blind scores which tools were chosen. 7 of 131 calls had been rejected for a
missing argument; a wording change took that to **0 of 119** on the benchmark.
A rule asking the model to batch more cut model calls (2.49 to 2.37 per query)
and was left **off** because it made one answer wrong that no judge-free metric
could see (found by reading the changed answers). The API gained per-thread
memory so a follow-up like "and Microsoft?" works: **11 of 11** follow-up turns
answered with it against 3 of 11 without, on eight small conversations (N is
11). A race on the first concurrent use of the search index was found and fixed on
the way.

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
reference passage in the index by whitespace-normalised substring search. On
the current index 65 of 71 locate verbatim, 3 by their first 60 characters,
and 3 were resolved by hand in
[`chunk_labels_overrides.json`](chunk_labels_overrides.json) with the reason
recorded: `qa_0022` and `qa_0023` cite Amazon's segment table, whose column
header sits in the chunk before its rows; `qa_0057`'s sentence has a space
before its comma in the cleaned text. A reference that sits in several
chunks — the 50-character overlap copies text into a neighbour; a table row
like "AWS 90,757 107,556 128,725" appears in several tables — becomes one
*any-of* group; 12 items have such a group. (On the first index, 66 located
verbatim, 3 by prefix and 2 by hand, one of them because Microsoft's cover
page began at chunk 72 behind XBRL context blocks — finding 22.)
Recall is measured over groups, so retrieving one of two overlap neighbours is
not a miss. The benchmark CSV was not edited.

**What the labels are not.** They mark the passage the benchmark author cited,
not every chunk that states the same fact. Finding 13 quantifies the gap.

### Retrieval alone, question sent verbatim

[`run_retrieval_eval.py`](run_retrieval_eval.py) sends each benchmark question
unchanged to the dense retriever (all-MiniLM-L6-v2, ChromaDB, no filter),
asks for 25 chunks, and scores the ranked ids against the labels. 71 items,
no LLM call, 13.6 ms p50 per query on the current 4,783-chunk index.
Evidence: [`retrieval-dense-5571ce86feed.json`](results/retrieval-dense-5571ce86feed.json).

| stratum | n | hit@5 | recall@5 | MRR | nDCG@5 | recall@10 | recall@25 |
|---|---|---|---|---|---|---|---|
| **all** | **71** | **0.5070** | **0.4894** | **0.3582** | **0.3695** | **0.5951** | **0.7254** |
| single_hop | 17 | 0.6471 | 0.6471 | 0.5510 | 0.5558 | 0.8235 | 0.9412 |
| numerical | 34 | 0.5000 | 0.5000 | 0.3377 | 0.3646 | 0.5588 | 0.7059 |
| multi_hop | 1 | 1.0000 | 1.0000 | 0.5000 | 0.6309 | 1.0000 | 1.0000 |
| comparative | 4 | 0.5000 | 0.1875 | 0.3465 | 0.2021 | 0.3125 | 0.3750 |
| negative | 2 | 0.5000 | 0.5000 | 0.1000 | 0.1935 | 0.5000 | 0.5000 |
| list | 8 | 0.3750 | 0.3750 | 0.2375 | 0.2664 | 0.3750 | 0.5000 |
| temporal | 5 | 0.2000 | 0.2000 | 0.1201 | 0.0861 | 0.6000 | 0.8000 |
| requires_table = True | 27 | 0.3704 | 0.3704 | 0.2030 | 0.2247 | 0.4815 | 0.6296 |
| requires_table = False | 44 | 0.5909 | 0.5625 | 0.4535 | 0.4583 | 0.6648 | 0.7841 |

Half the time the retriever does not put a labelled chunk in the top 5, and
on a quarter of items it is not in the top 25 either. `comparative` items
need several passages and get 19% of them at k = 5; `list` items
38%. Items that depend on a table trail the rest (hit@5 0.370
against 0.591). The `negative` row scores retrieval of the passage that
shows a fact is *absent*, which is a weaker notion of relevance — read it as
context for the refusal behaviour, not as a retrieval failure in the usual
sense. Thin strata are thin here as everywhere: `multi_hop` is one item.

The same measurement on the first index gave hit@5 0.507 as well
([`retrieval-dense-e53da55931de.json`](results/retrieval-dense-e53da55931de.json)): the junk that made up
three-quarters of that index was not competing for the top 5 of a dense search, it was
competing with exact-token search and with the reranker (finding 22).

This table was the starting line for the retrieval ablation in the next
section: a variant is measured here first, for free, and only a winner is
spent on with the judge.

### The same 71 answers, re-scored per chunk

*Measured on the first index (67,521 chunks); the figure columns are at
figure-check version 4. See finding 22 for the rebuilt index.*

The 71 `gemini-3.1-flash-lite` answers in the cache (prompt
`sha256:d1bedac20eb2`), re-judged by `gemini-3.6-flash` with contexts split
per chunk and prefixed with their provenance. Zero generation calls; 426
judge calls. Terminal failures (7, all recursion-limit) stay in every mean,
scored on the placeholder text they returned, never excluded. Evidence:
[`baseline-v3-a05e986405ba.json`](results/baseline-v3-a05e986405ba.json).

| stratum | n | faithfulness | answer relevancy | context recall | figure_exact (n) | agent_hit |
|---|---|---|---|---|---|---|
| **all** | **71** | **0.8263** | **0.7639** | **0.7183** | **0.6000** (45) | **0.4366** |
| single_hop | 17 | 0.8824 | 0.8317 | 0.8235 | 1.0000 (4) | 0.6471 |
| numerical | 33 | 0.8232 | 0.7278 | 0.6970 | 0.6061 (33) | 0.4242 |
| multi_hop | 1 | 1.0000 | 0.6225 | 1.0000 | n/a | 1.0000 |
| comparative | 4 | 0.4583 | 0.5611 | 0.7500 | 0.3333 (3) | 0.2500 |
| negative | 3 | 0.8889 | 0.9517 | 0.3333 | n/a | 0.3333 |
| list | 8 | 0.9375 | 0.7614 | 0.5000 | n/a | 0.1250 |
| temporal | 5 | 0.7000 | 0.8528 | 1.0000 | 0.4000 (5) | 0.4000 |

**Do not read the "all" row against the 66-item `rerun66` table.** The item
sets differ (71 against 66), and 10 of the 71 cached answers are not the ones
`rerun66` scored: the four `comparative` and the one `multi_hop` item were
regenerated on 11 September by the compare-fix runs in finding 11, which is
where the `comparative` faithfulness of 0.46 comes from, and the five
`temporal` items were never in `rerun66`. The like-for-like comparison is the
61 answers that are byte-identical in both runs, under the same judge:

| metric, 61 identical answers | schema 2 (blob contexts) | schema 3 (chunk + provenance) | items up / down |
|---|---|---|---|
| faithfulness | 0.8716 | 0.8579 | 0 / 2 |
| answer_relevancy | 0.7742 | 0.7722 | 12 / 11 |
| context_recall | 0.6885 | 0.6885 | 0 / 0 |

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
| `figure_primary_rate` — the figure the question asked for is in the answer | **0.7333** | 45 items whose ground truth holds a figure other than a year |
| `figure_exact_rate` — every ground-truth figure present in the answer | **0.6000** | 45 |
| `figure_recall` — share of ground-truth figures present | 0.7022 | 45 |
| `agent_hit_rate` — a labelled chunk seen anywhere in the agent's tool observations | **0.4366** | 71 |
| `agent_recall` — share of reference groups the agent saw | 0.4296 | 71 |
| `agent_mrr` — by the agent's own first-seen order | 0.2942 | 71 |

Per-stratum values are in the schema-3 baseline results file and on the
leaderboard. Items whose ground truth has no figure (`list`, most `negative`)
are *not applicable* to the figure check and are reported as such, never as a
pass. These are figure-check **version 4** numbers: version 1 read the "10" in
"10-K" as a figure and demanded it of the answer on three `negative` items,
version 3 added the primary-figure rate and precision-aware rounding
(finding 19), and version 4 made a ground truth whose only figure is a year
*not applicable* — five items, one of them a "not disclosed" answer that had
counted as a pass on the strength of the year; every schema-3 results file
was brought to version 4 by
[`recompute_deterministic.py`](recompute_deterministic.py), which records the
recomputation inside the file, and no judge score was touched.

---

## Retrieval ablation

Every retrieval technique in [`retrieval/retriever.py`](../retrieval/retriever.py)
is a field on `RetrievalConfig`, defaulting to dense top-5 — the code default,
what the system shipped with before the ablation — and each
configuration below was scored with `run_retrieval_eval.py` — the benchmark
question sent verbatim, 25 chunk ids returned, compared against the chunk
labels. No LLM call was made for any row; the whole matrix took about twenty
minutes of CPU on the current index (the first matrix, on the 67,521-chunk
index, is in git history and summarised configuration by configuration in
finding 22). Latency is the median wall-clock per query on the development
machine (a laptop CPU), after a warm-up query; the free-tier Space has fewer
cores and should be assumed slower.

The matrix is regenerated from the results files by `python -m eval.ablation
--sort hit@5`, so it cannot disagree with them.

| configuration | hit@5 | recall@5 | MRR | nDCG@5 | recall@10 | recall@25 | table hit@5 | p50 ms | file |
|---|---|---|---|---|---|---|---|---|---|
| dense, rerank fetch=50 | 0.662 | 0.630 | 0.529 | 0.538 | 0.665 | 0.743 | 0.481 | 785.2 | [dense-rerank-f50](results/retrieval-dense-rerank-f50-489f8c258136.json) |
| dense, rerank fetch=50, ticker=inferred | 0.662 | 0.630 | 0.541 | 0.546 | 0.680 | 0.771 | 0.481 | 750.9 | [dense-rerank-f50-tf-inferred](results/retrieval-dense-rerank-f50-tf-inferred-65a9dd460723.json) |
| dense, rerank fetch=100 | 0.648 | 0.627 | 0.516 | 0.532 | 0.665 | 0.711 | 0.481 | 1549.8 | [dense-rerank-f100](results/retrieval-dense-rerank-f100-82e4c431f458.json) |
| dense, rerank fetch=100, ticker=inferred | 0.648 | 0.627 | 0.528 | 0.539 | 0.665 | 0.753 | 0.481 | 1606.0 | [dense-rerank-f100-tf-inferred](results/retrieval-dense-rerank-f100-tf-inferred-b82fb24bab94.json) |
| dense, rerank fetch=25 | 0.648 | 0.620 | 0.514 | 0.527 | 0.655 | 0.725 | 0.481 | 432.6 | [dense-rerank-f25](results/retrieval-dense-rerank-f25-6ca601a0fbfc.json) |
| hybrid, rerank fetch=25 | 0.634 | 0.623 | 0.523 | 0.533 | 0.658 | 0.750 | 0.481 | 574.6 | [hybrid-rerank-f25](results/retrieval-hybrid-rerank-f25-25dd9f13ac04.json) |
| hybrid, rerank fetch=50 | 0.634 | 0.623 | 0.522 | 0.532 | 0.669 | 0.718 | 0.481 | 979.3 | [hybrid-rerank-f50](results/retrieval-hybrid-rerank-f50-9432e95c991d.json) |
| hybrid, rerank fetch=50, ticker=inferred | 0.634 | 0.623 | 0.535 | 0.539 | 0.669 | 0.775 | 0.481 | 1043.8 | [hybrid-rerank-f50-tf-inferred](results/retrieval-hybrid-rerank-f50-tf-inferred-6840e5d6edd4.json) |
| hybrid, rerank fetch=50, ticker=oracle | 0.634 | 0.623 | 0.535 | 0.539 | 0.669 | 0.775 | 0.481 | 1042.1 | [hybrid-rerank-f50-tf-oracle](results/retrieval-hybrid-rerank-f50-tf-oracle-8ba54909dc39.json) |
| hybrid, ticker=inferred | 0.606 | 0.599 | 0.430 | 0.457 | 0.672 | 0.792 | 0.444 | 30.6 | [hybrid-tf-inferred](results/retrieval-hybrid-tf-inferred-b103be4450e5.json) |
| hybrid | 0.592 | 0.585 | 0.400 | 0.433 | 0.644 | 0.750 | 0.407 | 27.3 | [hybrid](results/retrieval-hybrid-2b28152274f3.json) |
| hybrid, rrf_k=20 | 0.592 | 0.585 | 0.394 | 0.428 | 0.644 | 0.750 | 0.407 | 30.3 | [hybrid-rrf20](results/retrieval-hybrid-rrf20-5574d89e6b3a.json) |
| hybrid, fetch=50 | 0.578 | 0.567 | 0.401 | 0.426 | 0.658 | 0.750 | 0.407 | 26.9 | [hybrid-f50](results/retrieval-hybrid-f50-ae2a27a70146.json) |
| hybrid, w=1.0/0.5 | 0.578 | 0.560 | 0.401 | 0.423 | 0.680 | 0.725 | 0.407 | 26.9 | [hybrid-sw0.5](results/retrieval-hybrid-sw0.5-c6724b8409f9.json) |
| dense, ticker=inferred | 0.549 | 0.532 | 0.403 | 0.412 | 0.651 | 0.768 | 0.407 | 22.0 | [dense-tf-inferred](results/retrieval-dense-tf-inferred-0aab2af05ba4.json) |
| dense, ticker=oracle | 0.549 | 0.532 | 0.403 | 0.412 | 0.651 | 0.768 | 0.407 | 23.1 | [dense-tf-oracle](results/retrieval-dense-tf-oracle-34a06b35809e.json) |
| hybrid, w=1.0/2.0 | 0.535 | 0.535 | 0.357 | 0.390 | 0.606 | 0.683 | 0.333 | 29.7 | [hybrid-sw2](results/retrieval-hybrid-sw2-5a50d0a89612.json) |
| dense | 0.507 | 0.489 | 0.358 | 0.369 | 0.595 | 0.725 | 0.370 | 13.6 | [dense](results/retrieval-dense-5571ce86feed.json) |
| bm25 | 0.394 | 0.394 | 0.299 | 0.300 | 0.578 | 0.683 | 0.148 | 12.6 | [bm25](results/retrieval-bm25-61fd1f01d385.json) |

**Read the rows against the baseline `dense` row (hit@5 0.507).** Findings 16
and 17 are what the matrix shows.

### The measured configuration

`dense, rerank fetch=50, ticker=inferred`. The code default stays dense top-5
(so untagged cache entries stay valid); the deployment turns this on with

```
RETRIEVAL_RERANK=true
RETRIEVAL_FETCH_K=50
RETRIEVAL_TICKER_FILTER=inferred
```

| | dense (code default) | dense + rerank 50 + inferred ticker | change |
|---|---|---|---|
| hit@5 | 0.507 | **0.662** | +0.155 |
| recall@5 | 0.489 | **0.630** | +0.141 |
| MRR | 0.358 | **0.541** | +0.182 |
| nDCG@5 | 0.369 | **0.546** | +0.176 |
| recall@25 | 0.725 | **0.771** | +0.046 |
| hit@5, table items (n = 27) | 0.370 | 0.481 | +0.111 |
| hit@5, non-table items (n = 44) | 0.591 | **0.773** | +0.182 |
| p50 latency per query | 13.6 ms | 751 ms | 55× |

Item by item: the labelled chunk enters the top 5 on 14 items that dense missed and leaves it on 3 that dense had (`qa_0015`, `qa_0063`, `qa_0064`), net +11.

### The winner inside the agent

*Generated and judged on the first index; figure columns at version 4.*

The retriever-only numbers say the right chunk is in front of the model more
often. Whether the agent then answers better is a separate question, so the
full benchmark was regenerated with the shipped configuration switched on —
`gemini-3.1-flash-lite`, same prompt, same k = 5 per tool call, only
`RETRIEVAL_RERANK`, `RETRIEVAL_FETCH_K` and `RETRIEVAL_TICKER_FILTER` changed
— and compared with the schema-3 baseline on the judge-free metrics first,
because those cost nothing and cannot drift. 71 agent runs, cached under a
retrieval-tagged key so they can never be confused with the baseline's
answers.

| judge-free metric | baseline: dense top-5 | dense + rerank 50 + inferred ticker | change |
|---|---|---|---|
| `figure_primary_rate` (n = 45) | 0.7333 | **0.9111** | +0.1778 |
| `figure_exact_rate` (n = 45) | 0.6000 | **0.8444** | +0.2444 |
| `figure_recall` (n = 45) | 0.7022 | **0.9122** | +0.2100 |
| `agent_hit_rate` (n = 71) | 0.4366 | **0.6620** | +0.2254 |
| `agent_recall` | 0.4296 | **0.6514** | +0.2218 |
| `agent_mrr` | 0.2942 | **0.4395** | +0.1453 |
| terminal failures (recursion limit) | 7 | **1** | −6 |
| mean messages per item | 6.5 | 5.6 | |

| stratum | n | figure_exact before → after | agent_hit before → after |
|---|---|---|---|
| single_hop | 17 | 1.000 → 1.000 (n = 4) | 0.647 → 0.882 |
| numerical | 33 | 0.606 → **0.909** | 0.424 → 0.697 |
| multi_hop | 1 | n/a | 1.000 → 1.000 |
| comparative | 4 | 0.333 → **0.667** (n = 3) | 0.250 → 0.750 |
| negative | 3 | n/a | 0.333 → 0.000 |
| list | 8 | n/a | 0.125 → 0.375 |
| temporal | 5 | 0.400 → 0.400 | 0.400 → 0.400 |

Three things in this table are worth more than the headline.

**All seven of the baseline's recursion-limit failures now answer, and every
one that has a figure to get right gets it right.** `qa_0003`, `qa_0011`,
`qa_0031`, `qa_0043`, `qa_0054`, `qa_0055` and `qa_0060` all hit the step
budget under dense retrieval; all but `qa_0003` have a figure in the ground
truth and those six pass the figure check now, `qa_0003` answers without one.
One new recursion-limit failure appeared (`qa_0063`, comparative). The agent
was not incapable of those questions; it was searching, not finding, and
searching again until the budget ran out. A "terminal failure" counted
against the agent was a retrieval failure in disguise, which the schema-2
instrument could not have shown because it had no retrieval metric to show
it with.

**The temporal stratum did not improve.** `figure_exact` stays at 0.400, and
the primary-figure rate falls from 0.800 to 0.400: better retrieval puts the
three-year table in front of the model, and the model quotes the prior year
on `qa_0067`, `qa_0069` and `qa_0070` — two of which the baseline had got
right under a wrong year label. Retrieval was never
the cause of finding 2; the period has to be a filter on a structured fact
rather than a choice left to the model, which is the structured-facts
upgrade, not this one.

**The two thin strata moved in opposite directions and neither is a
finding.** `comparative` (n = 4) gained; `negative` (n = 3) lost its one
agent hit — on questions whose "relevant chunk" is the passage showing a
fact is absent, and where a reranker that surfaces the most on-topic passage
is arguably doing its job. Read both as anecdote.

Eleven items gained `figure_exact` and none lost it. 19 items gained an agent
hit, 3 lost one (`qa_0006`, `qa_0064`, `qa_0067`).

**What this comparison is not.** The baseline answers were generated on 10
and 11 September and these on 27 September, on the same model id; the
run-to-run variance of finding 3 was not sampled separately, so a part of
any single item's movement may be that variance. The aggregate movements —
+0.24 on the figure check, −6 terminal failures net — are far outside what
finding 11's two repeat runs showed that variance to be (0.65 against 0.63
faithfulness on ten items).

### Judge-scored, same answers

The same 71 answers, judged by `gemini-3.6-flash` under the schema-3
instrument (426 judge calls), against the schema-3 baseline. Evidence:
[`rerank-v3-c27752c52dab.json`](results/rerank-v3-c27752c52dab.json).

| metric | baseline: dense top-5 | dense + rerank 50 + inferred ticker | change |
|---|---|---|---|
| faithfulness | 0.8263 | **0.9315** | +0.1052 |
| answer relevancy | 0.7639 | **0.8688** | +0.1049 |
| context recall | 0.7183 | **0.8873** | +0.1690 |
| `figure_exact_rate` (n = 45) | 0.6000 | **0.8444** | +0.2444 |
| `agent_hit_rate` (n = 71) | 0.4366 | **0.6620** | +0.2254 |
| terminal failures | 7 | **1** | -6 |

| stratum | n | faithfulness | answer relevancy | context recall |
|---|---|---|---|---|
| single_hop | 17 | 0.882 → 0.882 | 0.832 → 0.940 | 0.824 → 0.882 |
| numerical | 33 | 0.823 → 0.990 | 0.728 → 0.960 | 0.697 → 1.000 |
| multi_hop | 1 | 1.000 → 1.000 | 0.623 → 0.738 | 1.000 → 1.000 |
| comparative | 4 | 0.458 → 0.750 | 0.561 → 0.577 | 0.750 → 1.000 |
| negative | 3 | 0.889 → 0.722 | 0.952 → 0.301 | 0.333 → 0.000 |
| list | 8 | 0.938 → 0.912 | 0.761 → 0.726 | 0.500 → 0.625 |
| temporal | 5 | 0.700 → 1.000 | 0.853 → 0.853 | 1.000 → 1.000 |

Items up / down: faithfulness 11 / 4,
answer relevancy 29 / 22,
context recall 13 / 1. Terminal
failures stay in every mean, scored as returned, and the judge's own run-to-run variance
(finding 7) applies to the relevancy column in particular.

---

## Structured facts: exact figures and a calculator

Retrieval put the right passage in front of the model far more often, and
the `temporal` stratum did not improve: the model still quoted the prior
year on the same items. That is not a retrieval failure. A 10-K's income
statement is a table with three years side by side, and once it is in front
of the model, choosing the column is the model's decision. Upgrade 3 takes
that decision away from it.

### What changed

Every headline figure in a 10-K is machine-tagged inside the filing (inline
XBRL) with its concept, period, unit and segment. [`ingestion/xbrl.py`](../ingestion/xbrl.py)
parses those tags from the five submissions already on disk into
`data/facts.sqlite` — 6,089 facts, about three seconds, the last step of the
ingestion pipeline ([ADR 0001](../docs/adr/0001-sqlite-for-xbrl-facts.md)
is why SQLite). Two tools sit on top of it ([`retrieval/facts.py`](../retrieval/facts.py)),
on the in-process agent and the MCP server alike:

- **`lookup_financial_fact(ticker, concept, fiscal_year, segment)`** resolves
  a plain-language concept through a synonym table ("total net sales" → the
  revenue concepts), a segment alias table ("AWS", "Intelligent Cloud",
  "iPhone"), and a name search that returns *candidates* rather than
  guessing. The fiscal year is a filter, not a choice: unset means the most
  recent year in the filing, and the tool says so in its output. A phrase
  that folds the segment into the concept ("Google Cloud revenue") is split
  before resolving.
- **`compute_metric(operation, a, b)`** does difference, sum, ratio,
  percentage change, margin and CAGR in Python and returns the formula with
  the result. The model picks the operands.

Both tools emit observations in the same header format as the retrieval
tools (`[n] ticker=AAPL  fact_id=123`, `[n] calc=pct_change`), so the
shared parser treats a fact as a citable source and a calculation as
evidence for the judge, and neither counts as an index chunk in the
retrieval metrics. The system prompt gained two rules: figure questions go
to the fact tool first; arithmetic is never done in the model's head.

Every spot check against the benchmark's ground truths matches the tagged
value exactly — $416,161M Apple net sales, $209,586M iPhone, $106,265M
Intelligent Cloud, $58,705M Google Cloud, $128,725M AWS, $2,207M Reality
Labs — because they are the same numbers, read from the same file, without a
model in between.

### Measured on the benchmark

*Generated and judged on the first index; figure columns at version 4.*

All 71 items regenerated with the fact tools available and the reranked
retrieval switched on, `gemini-3.1-flash-lite`, then judged by
`gemini-3.6-flash` (426 judge calls) under the schema-3 instrument. The
comparison is against the reranked run, which is the shipped configuration
without the fact tools, and against the dense baseline. Evidence:
[`facts-v3-7b536024e855.json`](results/facts-v3-7b536024e855.json).

| metric | dense baseline | + reranker | + reranker + fact tools |
|---|---|---|---|
| faithfulness | 0.8263 | 0.9315 | **0.9573** |
| answer relevancy | 0.7639 | 0.8688 | **0.8935** |
| context recall | 0.7183 | 0.8873 | **0.8521** |
| `figure_primary_rate` (n = 45) — the figure the question asked for | 0.7333 | 0.9111 | **1.0000** |
| `figure_exact_rate` — every ground-truth figure, context included | 0.6000 | 0.8444 | **0.8889** |
| `figure_recall` | 0.7022 | 0.9122 | **0.9444** |
| `agent_hit_rate` (index chunks only) | 0.4366 | 0.6620 | 0.4225 |
| terminal failures | 7 | 1 | **1** |
| items that called the fact tool | — | — | 34 of 71 |
| items that called the calculator | — | — | 8 of 71 |

| stratum | n | figure_primary: dense → rerank → facts | figure_exact: dense → rerank → facts | agent_hit: dense → rerank → facts |
|---|---|---|---|---|
| single_hop | 17 | 1.000 → 1.000 → **1.000** (n = 4) | 1.000 → 1.000 → **1.000** | 0.647 → 0.882 → 0.882 |
| numerical | 33 | 0.697 → 1.000 → **1.000** | 0.606 → 0.909 → **0.970** | 0.424 → 0.697 → 0.242 |
| multi_hop | 1 | n/a → n/a → **n/a** | n/a → n/a → **n/a** | 1.000 → 1.000 → 1.000 |
| comparative | 4 | 0.667 → 0.667 → **1.000** (n = 3) | 0.333 → 0.667 → **1.000** | 0.250 → 0.750 → 0.500 |
| negative | 3 | n/a → n/a → **n/a** | n/a → n/a → **n/a** | 0.333 → 0.000 → 0.000 |
| list | 8 | n/a → n/a → **n/a** | n/a → n/a → **n/a** | 0.125 → 0.375 → 0.500 |
| temporal | 5 | 0.800 → 0.400 → **1.000** | 0.400 → 0.400 → **0.200** | 0.400 → 0.400 → 0.000 |

`agent_hit_rate` counts index chunks only, by design: an item answered from
the fact table without a search shows no chunk hit and a correct figure, so
on this run the figure check is the metric to read and the hit rate is
context. Terminal failures stay in every judge mean, scored as returned.

**What moved, and why.** The fact tools were called on 34 of the 71 items
and the calculator on 8. On the items that used the fact tool, faithfulness
is 0.984 (n = 34) against 0.932 on the items that did not (n = 37): a
figure read off a tagged value with its period attached is very hard to
misstate. The primary-figure rate reaches 1.000 and the `temporal` stratum,
which two upgrades of retrieval could not move, goes from 0.400 to 1.000 on
the figure the question asked — because the year is now an argument to a
query, not a column the model picks from a table.

`context_recall` is the one judge metric that fell (0.887 → 0.852; 3 items
up, 6 down), and it fell on the fact-tool items (0.794 against 0.905 on the
rest). RAGAS attributes each sentence of the ground truth to the contexts,
and a context that reads `us-gaap:Revenue… | FY2025 | $416,161 million |
consolidated` supports the figure but not the prose around it; a ground
truth that also mentions a percentage change or a prior year finds no
passage for those. The metric was built for passages and is being handed
rows. It is reported as measured, not adjusted; limitation 16 records it.

`agent_hit_rate` falls from 0.662 to 0.423 for the reason stated above the
table: 33 items no longer searched the index at all. It has become a
diagnostic of which path answered rather than a quality score, and the
figure metrics are what to read on this run.

One terminal failure remains, and it is a different one: `qa_0062`, the
incorporation question, which the reranked run answered and this run
searched nine times without concluding. It has failed the same way on every
run of this configuration since (`cost-v3`, `contract-v3`): a consistent
failure of the fact-first prompt on that one `comparative` item, not
run-to-run variance. `qa_0064`, a
`negative` item, lost faithfulness on a correct refusal, which is the
refusal-scoring quirk finding 11 also recorded.

---

## Cost and latency

Every run made after upgrade 4 carries its own meter
([`agent/meter.py`](../agent/meter.py)): wall-clock latency, model calls,
tokens in and out from the returned messages, cost at the repository's dated
price table ([`agent/pricing.py`](../agent/pricing.py), read 2026-09-27),
each tool call with its duration, and the root run id that LangSmith shows as
the trace. The figures below are the shipped configuration — reranked
retrieval, fact tools, `gemini-3.1-flash-lite` — regenerated once more over
all 71 items with the meter on and tracing on (LangSmith project
`fra-eval-cost-v3`, so every record's `trace_id` opens). Agent calls only; no
judge pass was bought for this run. Evidence:
[`cost-v3-2d69cde009fc.json`](results/cost-v3-2d69cde009fc.json).

| per query (n = 71) | value |
|---|---|
| latency p50 | 1.9 s |
| latency p95 | 5.9 s |
| latency mean | 2.4 s |
| model calls, mean | 2.68 |
| tokens in, mean | 6,199 |
| tokens out, mean | 94 |
| cost, mean | $0.0017 |
| cost, whole benchmark | $0.12 |
| tool calls, mean | 1.85 |
| share of wall time inside tools | 37% |

| tool | calls | p50 | p95 |
|---|---|---|---|
| `search_filings` | 59 | 812 ms | 1,210 ms |
| `lookup_financial_fact` | 58 | 2 ms | 14 ms |
| `compute_metric` | 8 | 0 ms | 1 ms |
| `list_available_companies` | 6 | 0 ms | 0 ms |

7 of 131 tool calls returned an error to the agent (`lookup_financial_fact` 7 of 58).
Every one was the same mistake: the model called the fact lookup with a
ticker and a fiscal year and left out the required `concept`, and was told
"concept: Field required". On most items it corrected itself on the next
call; on `qa_0053` it repeated the mistake, and on `qa_0008` it gave up on
the lookup and searched instead. Each is
a wasted model round trip, recorded per call in the results file, and an
argument for giving that parameter a default or a louder description.

The judge is the expensive part of evaluation, not the agent: a full
benchmark of agent runs costs about $0.12, while one judge pass over the
same 71 answers costs about $1.00–1.19 (`gemini-3.6-flash`, 330–470k tokens
in and 200k out; judged results files record `judge_cost_usd` from upgrade 4
on, and the three committed judge passes, which predate it, price at $1.19,
$1.12 and $1.00 from their recorded token counts).
On the free-tier Space, with two vCPUs against this laptop's, expect the
tool share of latency — the reranker — to be larger.

**Run-to-run variance, same configuration, 1.7 hours apart.** This run
regenerated the same 71 items as the judged fact-tool run with nothing
changed but the clock (`facts-v3` at 03:50 UTC, this run at 05:32 UTC on
2026-09-28). 68 of 71 answers are byte-identical.
`figure_primary` 1.000 → 1.000, `figure_exact`
0.900 → 0.900, `agent_hit` 0.422 → 0.408, terminal
failures 1 → 1. That is the noise floor for the judge-free metrics on
this benchmark; a change smaller than it is not a finding.

---

## Output contract and fail-closed verification

The agent's final message is prose, and prose cannot be checked. Every
answer is now turned into a record — one claim per sentence, each with the
ids of the observations that support it — by a second, cheaper model call
that sees the question, the draft and the observations with their ids, and
the record is checked with no model in the loop
([`agent/contract.py`](../agent/contract.py)): a cited id must be an
observation from this turn (`unknown_source`), a sentence that states a
figure must cite something (`uncited_figure`), the figure must be in an
observation the sentence cites (`unsupported_figure`), and the record must
not lose a figure the draft had (`dropped_figure`). "In" uses the figure
grammar the figure check uses ([`agent/figures.py`](../agent/figures.py)),
read the way a verifier needs it: a claim may round its source but may not
be more precise than it, a percentage may be unsigned in the source (the
calculator prints `= 19.68`), and years are exempt. One repair attempt —
the structuring model is shown the failures — then the answer is refused:
in strict mode, the default, the API serves a refusal that names what it
could not verify, and the UI marks the answer as withheld.

**Measured on the benchmark**, shipped configuration plus the contract, all
71 items regenerated with tracing on (LangSmith project
`fra-eval-contract-v3`), no judge pass. Evidence:
[`contract-v3-76b8f532c332.json`](results/contract-v3-76b8f532c332.json);
the comparison column is the same configuration without the contract,
generated 11 hours earlier the same day.

| | contract-v3 | cost-v3 (no contract) |
|---|---|---|
| items through the contract | 70 (the one recursion failure, `qa_0062`, passes through untouched) | — |
| verified on the first attempt | **70 of 70** | — |
| repaired / refused | 0 / 0 | — |
| claims / figures checked / figures supported | 104 / 77 / 77 | — |
| observations cited | 79 passages, 49 tagged facts, 8 calculator results | — |
| `figure_primary`, served (draft) | 1.00 (1.00) | 1.00 |
| `grounded_rate` | 1.00 | 1.00 |
| drafts byte-identical to cost-v3 | 68 of 71 | — |
| model calls per query | 3.66 | 2.68 |
| tokens in / out per query | 6,940 / 221 | 6,199 / 94 |
| cost per query | $0.0021 | $0.0017 |
| latency p50 / p95 | 3.7 s / 9.1 s | 1.9 s / 5.9 s |

**What this shows and does not show.** Nothing was refused, and that is the
measurement rather than a failure of the instrument: with the fact tool and
the calculator in the loop, every figure the agent serves is a figure it was
shown (finding 20: `grounded_rate` 0.93 on the dense baseline and 1.00 on
every run since the reranker), so a check that every figure is attributable to
a cited observation has nothing left to catch on this benchmark. What the
contract adds is that the property is now *checked on every answer* rather
than measured once on 71: the API's `verification` block says which
observations each answer rests on, and an answer that cannot be attributed
does not leave the server. The checks fire in the tests, and they fired once
live during development, when a test hit the model with a synthetic
observation: the structuring model cited a fact id that did not exist,
`unknown_source` caught it, and the answer was refused — the failure mode
the contract exists for, seen on its first day, on traffic the benchmark
does not contain.

The price is a second, sequential model call: +742 input tokens and
+$0.0004 per query (+22%), and +2.2 s of latency — the p50 doubles, because
a schema-constrained call on `gemini-3.1-flash-lite` takes longer than one
of the agent's own turns. The drafts themselves did not move: 68 of 71
byte-identical to the previous run, the noise floor measured under "Cost
and latency". `VERIFY_MODE=off` restores the previous cost and latency, and
is how runs from before the contract are re-scored from cache.

---

## Tool-call quality

Findings 1–22 ask whether the answer was right. This section asks how the agent
got there: which tools it called, whether the calls were valid, how many model
round trips they took, and what moved when the tool descriptions, the system
prompt and the server's threading changed. Every number is computed from the
per-call record results files now carry and from a label file written before
any results file's tool fields were opened; none needed a judge. All runs:
`gemini-3.1-flash-lite`, the rebuilt 4,783-chunk index, reranked retrieval with
the inferred ticker filter, `VERIFY_MODE=strict`, generation only, tracing off;
costs are the repo meter's, and the ledger in
[`docs/UPGRADE_RUN.md`](../docs/UPGRADE_RUN.md) counts them at 1.25 times that.
The whole upgrade cost $0.65 counted ($0.52 of meter cost) of the $1.00 ceiling.

### The instrument

**The record.** [`agent/meter.py`](../agent/meter.py) stores, for each tool call,
its arguments (values cut to 200 characters, the object to 500), its start
offset `t0_ms` and, for a failed call, the first 300 characters of the error;
appends are under a lock, because a tool node runs the calls of one model step
on worker threads. `name`, `ms` and `error` are as they were and `public_meta`
never carries arguments. Only `cost-v3`, `contract-v3` and `reindex-v3` carry
any per-call record from before; none has arguments or start offsets.

**The labels.** [`benchmark_tools.json`](benchmark_tools.json) holds, for each
of the 71 items, the tools acceptable as the first call, the tools that are
allowed at all, the tools that are required, the companies it concerns and the
fiscal year the question names. It is a sidecar, deliberately not part of
`benchmark_version` (which hashes the CSV and the chunk labels, so a new column
would have orphaned every committed results file); the tool-metrics output
records its sha256 (`0dd9b822…`). It was written from the question, ground
truth, `question_type`, `section`, the prompt's rules and the tool docstrings
alone, **before any results file's tool fields were opened**, and committed on
its own. Three independent labellers (language models handed only those same
fields) then labelled all 71 items from scratch: they matched the author's
first-tool set on 65, 64 and 61 of 71 items and the allowed set on 61, 56 and
62; the author widened where they named a defensible alternative and narrowed
where all three found a fact lookup indefensible (eleven items changed). Six
items require `compute_metric` (a growth rate, change or difference: rule 8).
The labels were **not revised after the first results** (R2), including the
four first-tool misses below, two of which the author finds arguable.

**The metrics** ([`eval/tool_metrics.py`](tool_metrics.py), output in
[`tool_metrics/`](tool_metrics/)), each a count over its denominator, overall
and by `question_type`:

| metric | definition |
|---|---|
| `call_validity` | calls that did not fail over all calls, by tool; a call fails when the framework rejected it or a tolerant tool answered it with the argument-error marker |
| `first_tool_ok` | the first call's tool is in the item's first-tool set (earliest start when the file has `t0_ms`; otherwise the first recorded call, which is completion order, and the output says so) |
| `tool_set_ok` | every tool used is allowed and every required tool was used; `allowed_only_ok` drops the second half |
| `batched` | calls issued in a model step that made two or more; with `t0_ms` a step is a set of calls whose start-to-end windows overlap, each end padded by 50 ms because a 2 ms lookup can finish before its sibling's worker thread has started; older files get the per-item heuristic "tool calls greater than model calls minus 1", marked `heuristic` |
| `redundant_calls` | the same tool with identical arguments again |
| `arg_validity` | lookups only: ticker is one of the item's, fiscal year is the labelled one when the question names one, concept resolves to a row through the existing resolver |

`first_tool_ok` and `tool_set_ok` measure conformity to the prompt's tool rules,
not whether the answer was right; `figure_primary` does that. Latency blocks
carry both the harness's nearest-rank percentiles (the ones quoted in this
document) and linearly interpolated ones, with and without the terminal-failure
item: `contract-v3` over its 70 answered items is p50 3,684 ms and p95 9,077 ms
nearest-rank, 3,690 and 8,972 interpolated.

### The baseline: what the committed runs did

`cost-v3` and `contract-v3` made the same 131 calls in the same per-item
pattern ([`tool_metrics/contract-v3-…`](tool_metrics/contract-v3-76b8f532c332.json)):

| 71 items | |
|---|---|
| calls by tool | `search_filings` 59, `lookup_financial_fact` 58, `compute_metric` 8, `list_available_companies` 6 |
| calls per item | 1: 46 items, 2: 8, 3: 7, 4: 6, 5: 3, 9: 1 |
| valid calls | 124 of 131 (94.7%); lookups 51 of 58 (87.9%) |
| the 7 failed calls | all `lookup_financial_fact`, all "concept: Field required", on 6 items (`qa_0005`, `qa_0008`, `qa_0018`, `qa_0021`, `qa_0041`, `qa_0053`) |
| items with more calls than model steps | 8 (`qa_0007`, `0012`, `0034`, `0043`, `0053`, `0060`, `0062`, `0069`) |
| `first_tool_ok` / `tool_set_ok` | 67 of 71 / 67 of 71 |

The four items that miss are `qa_0008`, `qa_0065`, `qa_0068`, `qa_0071`, all because
the first call was `list_available_companies`; `qa_0068` ("What were Google Cloud
revenues?") and `qa_0071` ("What were AWS's net sales?") name no company, where
a ticker-list call is defensible. `reindex-v3`, a third run of the same
configuration made a day later, has 129 calls, 122 valid and `tool_set_ok` 66 of
71, and its p50 latency is 3,230 ms against 3,684 ms (both without the terminal
failure): **the same configuration moves 12% on p50 between two runs**, which
is the noise every latency comparison below is read against.

### Workstream A: the rejected lookups (finding 23)

The model sometimes called `lookup_financial_fact` with `ticker` and
`fiscal_year` and no `concept`. The fix tried first, and kept, is wording: the
tool description's first sentence, its parameter text and rule 7 now say that
`concept` is REQUIRED on every call and that one call returns one concept for
one fiscal year, with an example call; `mcp_server/server.py` mirrors it and
`tests/test_mcp_contract.py` pins it. Candidates (b) an explicit argument schema
and (c) a tolerant tool were specified and not built, because (a) passed its
gate. The 12 items below are the six whose calls were rejected plus the first
six figure-applicable items that were not (never changed after being fixed):

| 12 items | committed runs | `a-before`, today, unchanged | `a-wording` |
|---|---|---|---|
| lookups rejected | 7 | 5 of 20 | **0 of 15** |
| agent model calls per query | | 3.00 | 2.58 |
| `figure_primary` / verified | 11 of 11 / 12 | 11 of 11 / 12 | 11 of 11 / 12 |
| cost per query | | $0.00206 | $0.00193 |

Of today's 5 rejected calls, **3 were single calls and 2 were inside one
batched step**, so the omission is not a property of batching (an earlier note
that it was came from reading error messages and was wrong; the committed
`cost-v3` has 5 of its 6 failing items with no more calls than model steps).
The gate was "strictly fewer on these items and no control item changes its
figure or its verification": met. It is a **subset result chosen on failures**:
the committed full-run rate is 7 of 58 lookups (12%), the unchanged re-run
reproduced 5 of 20 on the failing items, and 0 of 15 against 5 of 20 is
Fisher exact p = 0.057 two-sided. The full benchmark re-measures it: the
shipped configuration made **0 rejected calls of 119** (lookups 0 of 51), and
every other run made after the wording changed (0 of 47, 0 of 39, 0 of 121) had
none either.

### Workstream B: batched calls (finding 24)

**Can the calls run in parallel safely?** Nothing in a committed run had ever
run `search_filings` in parallel. On the real index with the shipped retrieval
configuration, concurrent `search_filings` calls (48 with 8 workers before the
fix, 72 with 12 workers after it, three rounds each) and concurrent
`compare_companies` calls (16 and 24, two rounds each) were byte-identical to
the sequential results, with no exception: steady state is safe. **The first use of a
cold process was not**: with eight threads racing, six failed with
"Could not connect to tenant default_tenant", because the vectorstore is a lazy
singleton and every thread built its own Chroma client; the process-wide
retriever, its reranker and sparse index, the cross-encoder load and the fact
store had the same unguarded pattern. The API builds the vectorstore on the first
search, so a batched first request would have hit it. Each is now built once
under a double-checked lock taken only while the resource is unbuilt: a warm
call costs 39 ns and never queues, and steady-state searches are not serialised.
After the fix, 12 racing threads built the vectorstore once and all 12 results
were identical to sequential. Five race tests fail on the old code. A
thread pool bought about 1.4x wall-clock over sequential searches on this
12-logical-core machine; on the 2-vCPU Space it will buy less, and what batching
saves reliably is model round trips.

**The rule.** `AGENT_BATCH_RULE=on` appends a ninth rule to the system prompt
(`off` is the default and leaves the prompt, and its recorded version, byte for
byte unchanged). Measured on the 17 items it targets (`ITEMS_B`: the eight
`compute_metric` items, the four comparative and the five temporal):

| 17 items | `contract-v3` | `b-control` (fix, rule off) | `b-rule-v1` (rule v1 on) | `b-rule-v2` (rule v2 on) |
|---|---|---|---|---|
| tool calls | 59 | 47 | 47 | 39 |
| model steps that made them | | 35 | 34 | 26 |
| calls issued in a batched step | n/a | 21 of 47 (44.7%) | 23 of 47 (48.9%) | 23 of 39 (59.0%) |
| agent model calls per query | 3.82 | 3.06 | 3.00 | **2.53** |
| calls rejected | 5 | 0 | 0 | 0 |
| terminal failures | 1 | 0 | 0 | 0 |
| `figure_primary` / verified | 16 of 16 / 16 | 16 of 16 / 17 | 16 of 16 / 17 | 16 of 16 / 17 |
| `first_tool_ok` / `tool_set_ok` | 15 / 15 of 17 | 15 / 14 of 17 | 15 / 15 of 17 | 17 / 16 of 17 |

The control row is the point of the table. The first run (`b-rule-v1`, fix plus
the rule) moved everything against `contract-v3` (59 calls to 47, 3.82 model calls
to 3.00, `qa_0062` answering), and the same items with the rule **off** moved
nearly as much: the Phase 2 wording, which tells the model to "make one call per
figure and year", is itself an instruction to batch. Rule v1 changed the step
structure of 3 of 17 items. Rule v2 was written after seeing which items still
did not batch (`qa_0061`, `qa_0062`: three or four serial `search_filings` calls
for a question about all five companies, with `compare_companies`, which takes a
ticker list, never used); it changed exactly the items its wording addresses:
`qa_0062` four serial searches to **one** `compare_companies` call listing five
tickers (answer still correct), `qa_0061` three searches to one compare call,
`qa_0063` three steps to one, and `qa_0068`/`qa_0071` no longer start with an
unneeded `list_available_companies`. The wording was written after looking at
these 17 items, so they are the set it was tuned on.

**The full benchmark.** The same rule v2 over all 71 items, and the shipped
configuration (fix on, rule off) over all 71 items, against `contract-v3`:

| 71 items | `contract-v3` | **shipped: fix on, rule off** (`upgrade-control`) | fix + rule v2 on (`upgrade-v1`) |
|---|---|---|---|
| tool calls / rejected | 131 / 7 | **119 / 0** | 121 / 0 |
| calls issued in a batched step | n/a | 23 of 119 (19.3%), 10 items | 35 of 121 (28.9%), 12 items |
| agent model calls per query | 2.68 | **2.49** | 2.37 |
| `figure_primary` | 45 of 45 | **46 of 46** (45 of 45 on the original 45) | 46 of 46 |
| verified / refused / terminal failures | 70 / 0 / 1 | **71 / 0 / 0** | 70 / 0 / 1 |
| `first_tool_ok` / `tool_set_ok` | 67 / 67 of 71 | 65 / 64 of 71 | 70 / 69 of 71 |
| cost per query | $0.002067 | $0.001987 | $0.002063 |
| answers that became wrong, by reading every changed answer | n/a | **0** of 35 changed | **1** of 30 changed |

The figure n is 46 rather than 45 because `qa_0066` carries a figure since its
correction on 2026-09-28, after `contract-v3` was generated. The one terminal
failure of the rule-on run is `qa_0024` (a nine-search loop on a list question);
it also hit the recursion limit in `reindex-v3` and answered in the other runs,
as `qa_0062` did in `contract-v3` and `cost-v3` and in all three runs here:
the failing item moves from run to run.

**Every gate passed against `contract-v3`, and the rule is still off, because
reading the answers found what the gates cannot.** For the rule-on run, all six
conditions the plan set held: `figure_primary` not below, verified not below and
no refusal, terminal failures not above, p50 latency better, cost per query
equal, rejected lookups below baseline. But `qa_0008` ("Which of Apple's
reportable segments saw a decrease in net sales in fiscal 2025?", ground truth
Greater China) was answered correctly in all six runs without the rule and
**wrongly in all four runs with it**: the model fans out ten parallel lookups
over Apple's product categories (iPhone, Mac, iPad, Wearables, Services; two
years each), treats them as the reportable segments (they are geographic) and
answers "Wearables, Home and Accessories". The figure check does not apply to a
ground truth with no figure, and the contract verifies figures against the
observations, which the model did retrieve, so no judge-free metric moved. A
hand read of the 30 answers whose text changed between `contract-v3` and the
rule-on run found it; `qa_0014`, `qa_0035` and `qa_0047` are paraphrase-level
changes that also differ between committed runs, and the rest are the same
answer in other words. In the shipped configuration `qa_0008` is correct again
and all 35 changed answers were read the same way, with nothing wrong.

**Latency is not claimed.** p50 fell from 3,684 ms to 2,839 ms between
`contract-v3` and the shipped run, but 55 items used exactly the same tools in
both runs (43 of them a single call) and their p50 fell from 3,360 to 2,704 ms,
a median per-item ratio of 0.79, with nothing about what they did changed; the
median `search_filings` call fell from 859 to 714 ms (against the rule-on run:
52 items, ratio 0.84). The items that did the same work got 21% faster, nearly
all of the 23% by which the overall p50 fell: that is the day, not the code. Only
counts of model calls, steps and batched calls are evidence about the change.
Rule on and off had the same p50 in the full runs (2,831 and 2,839 ms).

### What this does not show

* The tool labels are author-written; the audit is by language models reading the
  same rules, so they are blind but not independent, and two of the four
  baseline first-tool misses are arguable. The `list_available_companies`-first
  misses rise from 4 to 6 in the shipped run, under labels that were not widened
  after the fact.
* Every subset is small and was chosen for a reason: `ITEMS_A` for failing,
  `ITEMS_B` for being where the rule acts and where its wording was tuned. One
  run per configuration; the run-to-run variation of a configuration was
  measured (12% on p50) but not sampled for each new one. The full runs are the
  regression check, not a significance test.
* Whether the model batches is its own choice, 19% to 29% of calls on this
  benchmark; no change here makes it reliable on a question it handles serially.

---

## CI quality gate

Every pull request runs the retrieval instrument and fails below a
threshold; the judged run is a workflow someone has to click. Both go
through [`eval/ci_gate.py`](../eval/ci_gate.py) with the thresholds in
[`eval/ci_gate.json`](../eval/ci_gate.json).

**What runs on every PR.** The runner rebuilds the index from the committed
filings (`python -m ingestion.pipeline`: about a minute of embedding since
the cleaner reads only the 10-K document, cached on the filings' and the
ingestion code's hash), then the two configurations that matter — the dense
baseline every number in this document started from, and the configuration
that ships (dense 50 → cross-encoder → 25 under the inferred ticker filter)
— are scored with the retriever-only runner against the 71 labelled items:
no LLM call, no secret. Seven thresholds. Each hit rate is at or below two
benchmark items under the value in the committed results file it names, so
a two-item loss passes and a three-item loss fails; MRR and nDCG sit 0.03
under it. The dry-run that every build starts with refuses a threshold above
its own source value, so the gate cannot be tightened past what was ever
measured, and the gate refuses an index whose chunk count is not the one the
labels were made on, so a rebuilt index cannot be scored against stale
labels.

| configuration | metric | committed | threshold |
|---|---|---|---|
| dense | hit@5 | 0.507 | ≥ 0.478 |
| dense | mrr | 0.358 | ≥ 0.328 |
| dense | hit@25 | 0.747 | ≥ 0.718 |
| shipped | hit@5 | 0.662 | ≥ 0.633 |
| shipped | mrr | 0.541 | ≥ 0.510 |
| shipped | ndcg@5 | 0.546 | ≥ 0.515 |
| shipped | hit@25 | 0.803 | ≥ 0.774 |

Run locally against the live index (`python -m eval.ci_gate retrieval`) the
gate reproduces every committed value to the third decimal and passes 7 of 7;
its per-item output goes to `eval/results/ci/` (gitignored) and, in CI, to a
build artefact and the run's summary page. An earlier design embedded a
committed 3,991-chunk slice of the old index because a full rebuild took 26
minutes; it was retired when the rebuild became cheap.

**The judged run, on a click.**
[`.github/workflows/eval-judged.yml`](../.github/workflows/eval-judged.yml)
runs the agent and the judge over ten fixed items — one or more from every
stratum, chosen for stability: each was answered identically on the two
committed runs of the shipped configuration 1.7 hours apart (`qa_0062`, which
hit the recursion limit on every run of this configuration on the first
index, and `qa_0064`, a correct "not disclosed" answer the judge scores 0,
are left out on purpose) —
and applies thresholds calibrated from what the committed judged run scored
on those same ten items (`python -m eval.ci_gate calibrate`):

| metric | calibration (`reindex-v3`, 10 items) | threshold |
|---|---|---|
| faithfulness | 1.000 | ≥ 0.85 |
| answer_relevancy | 0.927 | ≥ 0.75 |
| context_recall | 0.800 | ≥ 0.60 |
| figure_primary_rate | 1.000 | ≥ 0.85 |
| terminal failures | 0 | ≤ 1 |

About 60 judge calls, roughly $0.20 with `gemini-3.6-flash`; it never runs
on a push or a schedule, and two clicks cannot run at once. It runs with
`VERIFY_MODE=strict`: the thresholds are calibrated on `reindex-v3`, a
strict-mode run on the rebuilt index, so the gate scores what production
serves — the verified answer, or the refusal — and it pins the contract
version, so a change to the contract fails the gate until it is
recalibrated. (The first calibration, `facts-v3` on the first index before
the contract existed, gave 1.000 / 0.930 / 0.800 / 1.000 on the same ten
items; eight of the ten drafts are byte-identical between the two runs and
the other two differ in wording.) A run whose agent model, retrieval
configuration, verify mode, contract version or item set differs from the
calibration fails regardless of score, as does an incomplete one, one with a
judge NaN the harness cannot explain, or one in which the agent raised. A
run under another judge — the free Groq cross-family judge is an input
option — is scored but flagged, since two judges do not agree to the third
decimal (finding 4).

**What it does and does not catch.** A retrieval regression in code — a
reranker that stops reordering, a filter that stops filtering, a chunk id
format change — fails the build; a chunking change that alters chunk ids
fails it loudly, because the labels stop resolving, until the labels are
regenerated (`python -m eval.chunk_labels`). The judged smoke catches "the
agent broke": recursion failures, lost faithfulness, wrong figures. It cannot
see a one-item change. Two items of slack on the retrieval gate means a one-
or two-item loss passes: it is a regression detector, not the measurement.

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
rather than a tuning change. Instrument v2 built it (the figure check, finding
14) and the fact tools closed the stratum (finding 18).

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

Useful negative control: the three recursion-limit items among the 20
(`qa_0003`, `qa_0011`, `qa_0054`) scored 0.0 under **both** judges, identically.

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
hardcoded assumption. What it argued for was an instrument that can tell two
retrieval strategies apart — which instrument v2 then built — and a measurement
of how stable the agent's own queries are (ROADMAP item 2), rather than
choosing which set of questions to break.

**The change was kept.** The prefix is indefensible on inspection and demonstrably
caused at least one false refusal; reverting a correct fix because a coarse
metric dislikes it would be letting the instrument drive the engineering. But
this is explicitly **not** reported as an improvement: the headline metric went
down, one item produces a wrong figure that did not before, and `comparative` is
n = 4. Nothing here is conclusive in either direction.

This is the strongest argument so far for more items in the thin strata
(ROADMAP, "Not on this list"). A
four-item stratum cannot adjudicate a retrieval change, and two of the four
movements above are metric artefacts rather than quality changes.

### 12. The retriever alone finds a relevant chunk half the time, and the agent's own queries do worse

Instrument v2 measures retrieval two ways on the same 71 items. Sending the
benchmark question verbatim to the dense retriever puts a labelled chunk in
the top 5 on **50.7%** of items (`hit@5`, retriever alone, k = 25 fetched).
Letting the agent compose its own queries inside the ReAct loop and looking at
*everything* it retrieved across all its tool calls — five chunks on most
items, more when it searched again — a labelled chunk appears on **43.7%**
(`agent_hit_rate`). The agent
had more retrieval attempts and a wider net, and still saw the right page less
often, because the query it typed was worse than the question it was asked.

That is finding 3 with a number on it. Retrieval quality has two parts, the
retriever and the query composition, and both are now measured separately:
`run_retrieval_eval.py` moves only with the retriever, `agent_hit_rate` moves
with both. When a retrieval change improves the first and not the second, the
query is the problem, not the index.

Where the retriever is weakest is also where the questions are hardest:
`comparative` items retrieve 19% of their reference passages at k = 5,
`list` items 25%, and items that depend on a table trail the rest at every
depth up to 10 (hit@5 0.46 against 0.53). Chunks cut mid-table are a plausible cause
and now a testable one.

### 13. Chunk labels are a lower bound: 12 items were answered correctly without a labelled chunk

On 12 of 71 items the dense baseline's answer contains every ground-truth figure
(`figure_exact = True`) yet none of its tool observations contained a labelled
chunk (`agent_hit = False`): `qa_0001`, `qa_0009`, `qa_0019`, `qa_0020`, `qa_0032`, `qa_0033`, `qa_0034`, `qa_0044`, `qa_0048`, `qa_0066`, `qa_0068`, `qa_0071` (11 under figure check v2; v3 credits `qa_0034`'s rounded figure, finding 19). `qa_0001` is the plainest case: the label is the cover page
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
figure, three of which scored 1.00 on faithfulness. Under the figure check, on
the cached answers of the dense baseline (`baseline-v3`, which regenerated
`qa_0068` correctly):

| id | answer quotes | ground-truth figures missing from the answer | `figure_exact` |
|---|---|---|---|
| qa_0067 | the correct $106,265M, **labelled "Fiscal Year 2024"** | `2025` | False |
| qa_0068 | $33,088M, $43,229M and $58,705M, each under its own year | — | True |
| qa_0069 | MSFT correct, Alphabet $43,229M (prior year), both labelled 2024 | `2025`, `$58,705 million` | False |
| qa_0070 | $391,035M (prior year) | `$416,161 million`, `2025` | False |
| qa_0071 | $128,725M for 2025 and $107,556M for 2024, both correctly labelled | — | True |

Two of the five (`qa_0069`, `qa_0070`) are caught on the dollar figure. `qa_0067` is the
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
is reported next to `figure_exact`. The output contract's verifier (the
section "Output contract") is the tighter instrument at serving time; this one
exists because it costs nothing, compares against the ground truth, and already
tells the temporal stratum apart from a pass.

### 15. Stripping the provenance header from a context cost 0.13 faithfulness on identical answers

The first schema-3 re-score split each observation into bare chunk text —
the passage with its `[1] ticker=META  chunk_idx=412` header removed. On the
61 items whose cached answer is byte-identical to the schema-2 run, with the
same judge, `context_recall` did not move on a single item and
`answer_relevancy` moved within noise; **faithfulness fell from 0.8716 to
0.7432**, nine items down, seven of them from 1.00 to 0.00, none up.

The cause is what the header carried. Chunk text almost always says "the
Company"; the header is what says *which* company. An answer that begins
"Meta's total revenue for 2025 was…" is a claim about Meta, and a judge
handed a passage that never names Meta cannot verify it. The agent, however,
*did* have the header — it is part of the observation it read — so the bare
chunk under-represents the evidence the agent worked from.

Re-judging the nine dropped items with each context prefixed
`[META 10-K, chunk 412]`, nothing else changed:

| id | schema 2 (blob) | schema 3, bare chunk | schema 3, chunk + provenance (`baseline-v3`) |
|---|---|---|---|
| qa_0013, 0015, 0038, 0041, 0042, 0051, 0057 | 1.00 | 0.00 | **1.00** |
| qa_0040 | 1.00 | 0.50 | **1.00** |
| qa_0044 | 1.00 | 0.67 | 0.67 |

Eight of nine recover completely; `qa_0044` stays at 0.67 (an intermediate
nine-item re-judge, not committed, scored it 0.00 — the judge's run-to-run
variance of finding 7, not the prefix). Across all 61 identical answers the
full re-score with the prefix lands at 0.8579 against the schema-2 0.8716 —
two items down, none up — where the bare-chunk run had landed at 0.7432. The
provenance prefix
is therefore the context format for schema 3, and `context_format` is part of
the hashed configuration so the two formats can never share a results file.
The bare-chunk run is kept as
[`baseline-v3-plain-1f47fcda5bae.json`](results/baseline-v3-plain-1f47fcda5bae.json),
labelled as such, because it is the measurement behind this finding.

What it says beyond this repository: a faithfulness score depends on the
serialisation of the context as much as on its content, and a harness that
changes how contexts are formatted has changed the metric. Report the format
with the number.

### 16. A cross-encoder over 50 dense candidates is worth 15 points of hit@5; 25 candidates get most of it and 100 buys nothing

Re-ordering the dense retriever's top 50 with
`cross-encoder/ms-marco-MiniLM-L-6-v2` and keeping the best 5 moves hit@5
from 0.507 to 0.662 and MRR from 0.358 to 0.529 on the current
index: `single_hop` 0.647 → 0.824, `numerical` 0.500 → 0.676,
`list` 0.375 → 0.625, `temporal` 0.200 → 0.200. Over 25
candidates the gain is nearly all there (0.648); over 100 it is no better
than 50 (0.648) at twice the latency (1.5 s against 0.8 s).
The relevant chunk, when dense retrieval finds it at all, is almost always in
its top 50, so a deeper fetch only gives the cross-encoder more wrong
candidates to be confused by. On the first index the same step was worth 11
points (0.507 → 0.620) and 25 candidates only 4: a cleaner haystack makes
the shallow fetch enough (finding 22).

The gain is uneven in an informative way. Non-table items go 0.591 → 0.773;
table items 0.370 → 0.481. A cross-encoder reads prose well and a run of
numbers badly, and the 512-character chunks cut tables mid-row, so the row
that answers a table question often does not carry the label that names it.
Tables remain the weak stratum, and the lever for them is chunking, not
ranking (ROADMAP item 1).

Cost: 0.8 s of CPU per query on the development machine, against 14 ms
for dense alone. An agent run made 2.1 retrieval calls on average under
dense search and 1.7 reranked (0.8 once the fact tools arrived). On the
free-tier Space, with fewer cores, the reranker is most of the tool time.

### 17. Hybrid BM25 fusion beats dense as a first stage on the clean index and loses to it under the reranker; the inferred ticker filter is free and equals the oracle

**BM25 alone** puts a labelled chunk in the top 5 on 39% of items. **Fused
with dense** by Reciprocal Rank Fusion it lands at 0.592, *above* dense's
0.507 — nine points, and table items go 0.370 → 0.407. The sweeps
(rrf_k 60 → 20: 0.592; sparse weight 0.5: 0.578; sparse weight 2.0: 0.535;
fetch 50: 0.578) do not beat the default fusion, and the inferred ticker
filter lifts it to 0.606. But feeding the reranker hybrid candidates instead
of dense ones scores lower (0.634 against 0.662 at fetch 50, with or
without the filter): the cross-encoder already recovers what
exact tokens add, and the BM25 candidates it is handed displace better dense
ones. The shipped pipeline therefore stays dense → rerank, and hybrid stays
in the code as the best *unreranked* option (30 ms against 0.75 s).

**This reverses the first measurement, and the reversal is the finding.** On
the 67,521-chunk index hybrid fusion scored 0.479, *below* dense, because
half that index was Microsoft's exhibits and XBRL context blocks — tens of
thousands of chunks eligible for exact-token matches on "2025", "million",
"revenue" and every context id. BM25 was not wrong about 10-Ks; it was
scoring a haystack that was mostly not 10-Ks (finding 22). The earlier text
here explained the loss by the structure of 10-K tables; that explanation
was partly right and mostly the index.

**The ticker filter.** Restricting search to the one company the question
names (`retrieval/tickers.py`, a five-entry alias list) produces the same
numbers as the oracle filter that reads the benchmark's ticker column, on
every metric, in every configuration tried — the files are identical row for
row. Every single-company question in this benchmark names its company, so
inference is exact here; multi-company questions correctly get no filter. It
adds 0.042 to dense alone (0.549) and nothing to hit@5 on top of reranking
(0.662 either way) but 0.012 of MRR and 0.028 of recall@25, for no latency. It is on
in the shipped configuration.

**Not measured, and why.** Table-aware chunking needs a re-ingested index and
therefore new chunk labels; it is the next lever and its own pass. Query
rewriting (HyDE, multi-query) costs an LLM call per retrieval and was not
measured within this budget. A larger reranker (`BAAI/bge-reranker-base`,
1.1 GB) was not measured; the small one already saturates at fetch 50 and
the free-tier Space has 16 GB of RAM to share with everything else.

### 18. The wrong-year answers were never a retrieval problem, and a filter fixed what two retrievers could not

Finding 2 showed the agent quoting the prior year on four of five `temporal`
items with faithfulness scoring three of them 1.00. Upgrade 2 put the right
passage in front of the model far more often (hit@5 0.51 → 0.63, agent_hit
0.44 → 0.66) and the `temporal` figure check did not move: 0.40 before,
0.40 after. The three-year table was in front of the model both times; the
model chose the column.

The fact tools remove the choice. A lookup takes `fiscal_year` as an
argument, defaults to the filing's most recent year and says so, and
returns one tagged value with its period. On the same five items the
primary-figure rate goes to 1.000, and the answers read "for fiscal year
2025 was $106,265 million" — the year asked, the figure asked, nothing
else. Across all 45 figure items the primary-figure rate is 1.000 against
0.911 with the reranker and 0.733 for the dense baseline, and the strict
check reaches 0.889. Faithfulness rises again (0.9315 → 0.9573), and on the 34
items that used the fact tool it is 0.984.

Three cautions. The five temporal items are five items. The comparison is
against answers generated 1.5 hours earlier on the same model id, not a
same-session regeneration. And the fact table covers what the filing
tagged: a line item the resolver does not know ("Google Search & other
revenues") falls back to search, which on that item still answered
correctly through the calculator.

### 19. When answers became more exact than the labels, the figure check had to learn what a label means

The first run with the fact tools looked like a regression on the strict
figure check: `temporal` fell from 0.40 to 0.20 and three `numerical` items
flipped to failing. Every one of those answers was right.

| item | answer with the fact tools | why the strict check failed it |
|---|---|---|
| qa_0067 | "$106,265 million for fiscal year 2025" | the ground truth adds "(fiscal 2024: $87,464 million)" as context, and the check demanded it |
| qa_0068, 0070, 0071 | the correct current-year figure, no prior year listed | same: parenthetical prior-year context in the ground truth |
| qa_0007 | "an increase of 13.51%" computed from the tagged figures | the filing's prose, and the ground truth, round it to "14%" |
| qa_0021 | "19.68%" | ground truth "20%" |
| qa_0034 | "$26,448 million" read from the table | ground truth "$26.4 billion" |

Earlier runs had passed the temporal items only because the model recited
all three years' figures, so the prior-year context happened to be present.
An answer that names exactly the year asked and nothing else is better, and
the instrument scored it worse. Two changes, together figure check
**version 3**, applied to every schema-3 results file by
`recompute_deterministic.py` with judge scores untouched:

- **`figure_primary`**: the first non-year figure in the ground truth is the
  figure the question is about; the check reports whether the answer
  contains it. `figure_exact` stays as the strict, every-figure view.
- **Precision-aware matching**: a candidate matches when it rounds to the
  ground-truth figure at the precision the ground truth was written in —
  13.51% rounds to 14%, $26,448 million to $26.4 billion. Years remain
  exact-only, so 2024 still cannot pass for 2025, and a different figure
  ($43,229 million against $58,705 million) still fails.

**Version 4**, after the review: a ground truth whose only figure is a
year is *not applicable*. Version 3 had fallen back to the year as the
primary figure, which let five items pass on the strength of a year alone —
one of them a "not disclosed" answer whose ground truth stated no number at
all. The five leave the denominator; nothing else changes. That item,
`qa_0066`, was also wrong as a label: Meta's segment note and its tagged
XBRL disclose Reality Labs revenue ($2,207 million for fiscal 2025) and the
fact tool finds it, so its ground truth now states the figure and the item
is `numerical`. Runs made on the corrected benchmark count 34 `numerical`
and 2 `negative` items (the retrieval tables above); the runs generated
before the correction keep the strata and the ground truth they were
generated with.

Under version 4, on the 45 items whose ground truth carries a figure other
than a year (50 under version 3; the dense baseline was 0.760 / 0.640 /
0.732 there, the reranker 0.920 / 0.860 / 0.921, the fact tools 1.000 /
0.900 / 0.950):

| run | figure_primary | figure_exact | figure_recall |
|---|---|---|---|
| dense baseline | 0.733 | 0.600 | 0.702 |
| + reranker | 0.911 | 0.844 | 0.912 |
| + reranker + fact tools | **1.000** | **0.889** | **0.944** |

The general point is the same one findings 2, 14 and 15 made from other
directions: a metric is a definition, and the definition has to be revisited
every time the system gets good enough to expose its edges. What is
different here is that the fix was to the instrument's reading of the
*label*, not of the answer — and it took a system that answered more
precisely than the people who wrote the labels to show it.

---

### 20. Three answers did arithmetic in the model's head; the judge scored all three 1.0, and a citation-free grounding check catches them for nothing

The output contract's figure test (`agent/contract.py`, `figure_grounding`)
can be run without citations against any stored record: is every figure in
the answer present in *something* the agent saw? On the dense baseline
([`baseline-v3`](results/baseline-v3-a05e986405ba.json)) 3 of 41 answers
with a figure fail it — `qa_0007` ($12,989 million, the difference between
two Services net sales figures), `qa_0034` ($26,448 million, the difference
between two revenue figures) and `qa_0053` (23.40%, a growth rate) — each a
number the model computed itself from figures that were in its passages.
Faithfulness scored all three 1.0. The figure check credits two of the three.
On every run since (`rerank-v3`, `facts-v3`, `cost-v3`) 0 of 48, 48 and 47
answers fail. `rerank-v3` had no calculator yet: better retrieval alone
removed the three, because the model computed in its head only when the
passage with the figure was not in front of it. The calculator then made
arithmetic an observation (rule 8 of the prompt sends it to `compute_metric`
and the result comes back as one), so the property no longer depends on
retrieval luck.

This is the class of error a faithfulness judge cannot see — a derivation
from grounded numbers reads as grounded — and the class the contract's
`uncited_figure` and `unsupported_figure` checks exist for. `grounded_rate`
is now recorded on every schema-3 generation results file
(`python -m eval.recompute_deterministic <files>` added it to the six
committed ones; the schema-2 files predate per-chunk observations and do
not carry it).

---

### 21. Verifying an answer costs 22% in dollars and doubles the median latency; the second round trip, not the tokens, is the price

The output contract adds one model call per answer: the question, the draft
and the observations with their ids, returning a schema-constrained record.
Over the 71 items it added 742 input tokens and $0.0004 per query, and 2.2 s
per query on average — the p50 went from 1.9 s to 3.7 s, the p95 from 5.9 s
to 9.1 s
([`contract-v3`](results/contract-v3-76b8f532c332.json) against
[`cost-v3`](results/cost-v3-2d69cde009fc.json)). The tokens are cheap on a
flash-lite model; the wall clock is not, because the call is sequential
(it needs the draft) and structured output on this model runs slower than a
plain turn. Two ways to take the latency back, neither measured: a smaller
model for the structuring call, or a prompt that has the agent emit the
record alongside its draft in the final turn, which removes the round trip
but puts the citation discipline inside the same call that writes the
prose.

---

### 22. Three-quarters of the index was XBRL markup, identifiers and metadata, not 10-K text; isolating the 10-K document changed the ablation's story and left its winner in place

The cleaner read every `<DOCUMENT>` in each EDGAR submission. A submission
holds the 10-K, its exhibits, the XBRL taxonomy files, images, and the XBRL
instance document, whose text blocks are HTML-escaped copies of the notes —
which BeautifulSoup decodes into literal `<td style=...>` prose. Nobody had
looked. `python -m ingestion.audit` classifies every chunk by what it holds;
on the first index — the one the judged runs were generated on — and on
the rebuilt one:

| class | first index (67,521 chunks) | rebuilt index (4,783 chunks) |
|---|---|---|
| prose | 22.7% | 85.9% |
| numeric table | 1.2% | 1.7% |
| escaped markup | 39.4% | 0 |
| XBRL identifiers | 25.7% | 0 |
| MetaLinks / JSON | 8.8% | 0 |
| short fragment (< 120 chars) | 2.2% | 12.4% |

Microsoft was 32,886 of the 67,521 chunks and 74% escaped markup; its chunk
0 was a list of years and context ids from the inline XBRL header, which
had passed the cleaner's prose test because "2004 2005 2006 …" is twenty
characters with spaces. The fix ([`ingestion/submission.py`](../ingestion/submission.py),
[`ingestion/cleaner.py`](../ingestion/cleaner.py)) keeps the 10-K document
only, decomposes the `<ix:header>` block, and turns no-break-space table
cells into whitespace. Per ticker the rebuilt index is 582 / 965 / 965 / 816
/ 1,455 chunks (AAPL / MSFT / GOOGL / AMZN / META), a rebuild takes 72
seconds instead of 26 minutes, and the index is 29 MB instead of 359.

**What moved, configuration by configuration.** Every chunk id changed, so
the labels were regenerated (65 exact, 3 prefix, 3 by hand) and the whole
ablation re-run:

| configuration | hit@5, first index | hit@5, rebuilt index | change | MRR first → rebuilt |
|---|---|---|---|---|
| dense, rerank fetch=50 | 0.620 | 0.662 | +0.042 | 0.480 → 0.529 |
| dense, rerank fetch=50, ticker=inferred | 0.634 | 0.662 | +0.028 | 0.496 → 0.541 |
| dense, rerank fetch=100 | 0.620 | 0.648 | +0.028 | 0.482 → 0.516 |
| dense, rerank fetch=100, ticker=inferred | 0.606 | 0.648 | +0.042 | 0.486 → 0.528 |
| dense, rerank fetch=25 | 0.549 | 0.648 | +0.099 | 0.421 → 0.514 |
| hybrid, rerank fetch=25 | 0.578 | 0.634 | +0.056 | 0.469 → 0.523 |
| hybrid, rerank fetch=50 | 0.592 | 0.634 | +0.042 | 0.471 → 0.522 |
| hybrid, rerank fetch=50, ticker=inferred | 0.606 | 0.634 | +0.028 | 0.489 → 0.535 |
| hybrid, rerank fetch=50, ticker=oracle | 0.606 | 0.634 | +0.028 | 0.489 → 0.535 |
| hybrid, ticker=inferred | 0.507 | 0.606 | +0.099 | 0.404 → 0.430 |
| hybrid | 0.479 | 0.592 | +0.113 | 0.357 → 0.400 |
| hybrid, rrf_k=20 | 0.493 | 0.592 | +0.099 | 0.357 → 0.394 |
| hybrid, fetch=50 | 0.479 | 0.578 | +0.099 | 0.363 → 0.401 |
| hybrid, w=1.0/0.5 | 0.479 | 0.578 | +0.099 | 0.370 → 0.401 |
| dense, ticker=inferred | 0.535 | 0.549 | +0.014 | 0.400 → 0.403 |
| dense, ticker=oracle | 0.535 | 0.549 | +0.014 | 0.400 → 0.403 |
| hybrid, w=1.0/2.0 | 0.465 | 0.535 | +0.070 | 0.336 → 0.357 |
| dense | 0.507 | 0.507 | +0.000 | 0.357 → 0.358 |
| bm25 | 0.352 | 0.394 | +0.042 | 0.304 → 0.299 |

Three things to read off that table. Dense search alone did not move: the
junk was never in its top 5, which is why the first instrument could not
see it. Everything that used exact tokens moved a lot — BM25 alone +0.042,
hybrid fusion +0.113 — and finding 17 reversed: on a clean index BM25 fusion is
the best unreranked retriever. And the winner is the same pipeline with a
better number: dense → cross-encoder over 50 → inferred filter, hit@5
0.662 (was 0.634), MRR 0.541 (was 0.496), with reranking 25 candidates now
nearly as good as 50.

**The agent on the rebuilt index.** All 71 items regenerated once with the
shipped configuration and the output contract in strict mode, then judged
by `gemini-3.6-flash` ([`reindex-v3-5b1deb95bdcc.json`](results/reindex-v3-5b1deb95bdcc.json);
tracing on, LangSmith project `fra-eval-reindex-v3`). The judge-free rows
are against the same configuration and contract on the first index
(`contract-v3`):

| judge-free, 71 items | rebuilt index (`reindex-v3`) | first index (`contract-v3`) |
|---|---|---|
| `figure_primary_rate` | **1.000** (n = 46) | 1.000 (n = 45) |
| `figure_exact_rate` | 0.870 | 0.889 |
| `grounded_rate` | 1.000 | 1.000 |
| `agent_hit_rate_searched` | 0.789 (n = 38) | 0.789 (n = 38) |
| verified, no repair needed | 70 of 70 | 70 of 70 |
| terminal failures | 1 (`qa_0024`) | 1 (`qa_0062`) |
| tokens in / out per query | 6,774 / 225 | 6,940 / 221 |
| cost per query | $0.0020 | $0.0021 |
| latency p50 / p95 | 3.2 s / 7.9 s | 3.7 s / 9.1 s |

The figure n differs by one because `qa_0066` carries a figure since its
correction (finding 19's version-4 note) and the first-index run was scored
against the ground truth it was generated with.

The judged rows are against the same configuration judged on the first
index (`facts-v3`, made before the contract existed; its ten-item subset is
the one the CI gate was first calibrated on):

| judged by `gemini-3.6-flash`, 71 items | rebuilt index (`reindex-v3`) | first index (`facts-v3`) | change |
|---|---|---|---|
| faithfulness | 0.949 | 0.957 | -0.009 |
| answer relevancy | 0.890 | 0.894 | -0.004 |
| context recall | 0.845 | 0.852 | -0.007 |
| faithfulness on the items that used the fact tool | 0.990 (n = 34) | 0.984 (n = 34) | |

50 of 71 drafts are byte-identical to `facts-v3`'s, and the three means
moved by less than a hundredth, inside the run-to-run variance finding 11
measured on ten items. Item by item the movement is more instructive than
the means. `qa_0062`, the comparative item that hit the recursion limit on
every first-index run, now answers (faithfulness 0.86); `qa_0064`'s "not
disclosed" answer scores 1.0 where the same words scored 0 before; `qa_0024`,
a `list` item, hits the recursion limit instead, and it is not among the
judged smoke's ten. Two `single_hop` items lost faithfulness: `qa_0015`
gives the same Seattle address as before and scored 0 against contexts that
overlap the old ones (judge variance, finding 7), and `qa_0027` names
Sundar Pichai correctly from chunks that do not say so — a prose claim the
retrieval did not support, served as verified because the contract's checks
attribute figures and citations, not free-text claims (limitation 19).
Cost and latency fell slightly with the smaller index.

**What it says beyond this repository.** Every retrieval number in a RAG
evaluation is a number about a corpus, and a corpus that nobody has sampled
by hand can be mostly something else. The audit is now a command, the CI
gate rebuilds the index it scores, and the first thing a reader should ask
of any retrieval table here is the one the review asked: show me a random
chunk.

---

### 23. The model sometimes dropped a required argument; saying so in the places it reads removed the failure on the benchmark, and it was not a batching problem

7 of the 131 tool calls in the committed cost run (all `lookup_financial_fact`,
on six items) were rejected with `concept: Field required`: the model passed a
ticker and a fiscal year and no concept. Each is a wasted model round trip. The
cause is not visible in the code, which declares `concept` required in every
schema; it is the model emitting a malformed call, and it did so on single calls
as much as inside batches (3 of today's 5 on the same items were single calls).
The fix is words, in four places the model reads: the first sentence of the tool
description, its parameter text, rule 7, and the MCP description, each saying
that `concept` is REQUIRED on every call and one call returns one concept for
one year, with an example. Measured on the 12 items chosen for the failures
(`a-before` against `a-wording`): **5 of 20 lookups rejected today unchanged, 0
of 15 after** (committed runs: 7), with `figure_primary` and verification
unchanged on all twelve. On the full benchmark the shipped configuration made 0
rejected calls of 119 and no later run had any. A tolerant tool, which accepts
a missing concept and returns an observation naming it, was specified as the
fallback and not built; a default `concept` was ruled out in advance, because a
wrong default returns the wrong kind of figure silently, which is worse than a
rejection. What it does not show: the 12 items were selected on failing, so the
effect is overstated by regression to the mean (the unchanged re-run already
fell from 7 to 5), and 0 of 15 against 5 of 20 is Fisher p = 0.057. The
full-benchmark zeros are the stronger evidence: 0 of 119 where the committed
rate was 7 of 131.

---

### 24. A batching rule moved what it was written to move and broke one answer that no judge-free metric can see; reading the changed answers found it, and the rule ships off

LangGraph's tool node already runs the calls of one model step concurrently and
the model already batches some questions (19% of calls over the 71 items in the
shipped run, 45% on the 17 items that need several lookups). A rule asking for
more was measured against a rule-off control on those 17 items (table in
"Tool-call quality"): the first wording moved almost nothing (47 calls either way, 3.00 model
calls against 3.06); a second wording, written after seeing which items still
searched one company at a time, took agent model calls to 2.53 and a five-company
question from four serial searches to one `compare_companies` call, with
`figure_primary` and verification unchanged. On the full benchmark it made
2.37 model calls per query against 2.49 for the shipped configuration and passed
every gate set in advance against `contract-v3`. **Then one answer was wrong**:
`qa_0008` (Apple's reportable segments) fans out ten lookups over product
categories and names the wrong segment, deterministically (4 of 4 runs with the
rule, 6 of 6 without), and the answer is verified because every figure in it is a
real retrieved figure. The cost of finding it was reading 30 changed answers; a
judge pass is $1.10. The decision: `AGENT_BATCH_RULE` is off by default, the rule
text stays behind the switch, and the wording fix of finding 23, which the
control showed already does most of the batching, is on. Two things this finding
is not: it is not evidence that batching is unsafe (the tool calls were proven
safe to run concurrently once the first-use race was fixed, and 0 of 119 to 0 of
121 calls were rejected either way), and it is not a latency result ("Latency is
not claimed" above: the day moved the p50 by about as much as the whole
difference).
The first-use race itself was a real defect found by the safety test the plan
asked for before any rule: eight threads building the Chroma client at once, six
failures, fixed with a lock taken only on the first build.

---

### 25. A follow-up now works through a server-side memory of the previous turns' text; on eight small conversations it answered 11 of 11 follow-ups where the stateless API answered 3 of 11, and the 3 were lucky defaults

The API is stateless, so "and Microsoft?" has no company, year or metric to
resolve. `agent/memory.py` keeps the text of up to six served turns per thread id
the client sends; a request with a thread id hands the model one human message
(a labelled history block, then the current question) and a request without one
is byte for byte what it was before. The design reasons are in
[`docs/DECISIONS.md`](../docs/DECISIONS.md): identical on the direct and MCP
agent, small (text only), and, crucially, the output contract stays turn-scoped:
it checks each figure against THIS turn's observations, so the history block tells
the model to reuse no figure and re-retrieve every one it states, and a figure
served from an earlier turn would fail verification. Only an answer that passed
verification is remembered, never a refusal, an unverified draft or a terminal
failure.

Measured with `eval/multi_turn_probe.json`: 8 conversations of two or three
turns over the five filings, each follow-up leaving out the company, the year, the
metric or relying on a pronoun, every ground-truth figure read from
`data/facts.sqlite` by fact id, run through the real `/query` path with the real
agent and contract (30 requests, $0.051 meter cost,
[`probes/multiturn-memory-v1-3b4d5b73e61d.json`](probes/multiturn-memory-v1-3b4d5b73e61d.json)):

| | with memory | each follow-up asked alone |
|---|---|---|
| follow-up turns answered correctly (figure check) | **11 of 11** | 3 of 11 |
| first turns answered correctly | 8 of 8 | (the same request) |
| answers verified / refused | 30 of 30 / 0 | |

The three that succeeded alone are guesses that matched: "And Microsoft's?" and
"And Alphabet's?" were answered with total net sales (the model's most common
metric) and "And what were its Services net sales?" with Apple's (the only
company with a Services segment). The eight failures answered for the wrong
company or year, gave every company's figure, or asked what was meant. **N is 11
follow-up turns in 8 conversations, written by the author who built the memory:**
it shows the mechanism works end to end and that the contract still verifies
every answer under it, not a rate. The web UI mints one thread id per page load
(React state only), has a "New chat" control and says follow-ups are remembered
for the session and that memory clears when the server restarts, which is the
truth: the store is in process and ephemeral.

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
which is not a random draw. Finding 5 is the only bridge between them.

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
| Results file | [`baseline66`](results/baseline66-af83fa6.json) | [`rerun66`](results/rerun66-af83fa6.json) | [`crossjudge20`](results/crossjudge20-af83fa6.json) | [`baseline-v3`](results/baseline-v3-a05e986405ba.json) | [`dense-rerank-f50-tf-inferred`](results/retrieval-dense-rerank-f50-tf-inferred-b90fb53e560e.json) |

The runs after the instrument — [`rerank-v3`](results/rerank-v3-c27752c52dab.json),
[`facts-v3`](results/facts-v3-7b536024e855.json),
[`cost-v3`](results/cost-v3-2d69cde009fc.json) and
[`contract-v3`](results/contract-v3-76b8f532c332.json) — are stamped the same
way: agent `gemini-3.1-flash-lite`, judge `gemini-3.6-flash` where judged,
retrieval `dense, rerank fetch=50, ticker=inferred`, prompt
`sha256:99d36aed6b9c` from `facts-v3` on (rules 7–8 added), and on
`contract-v3` `verify_mode: strict` with `contract_version: da6f5bea0c8b`.

Held constant across the runs in the table: k = 5, agent temperature 0, agent
recursion limit 20, prompt version `sha256:d1bedac20eb2` (a hash of the live
prompt text, so it cannot drift out of sync with the prompt it names), embeddings
`sentence-transformers/all-MiniLM-L6-v2` (384-dim, used for both retrieval and the
judge's relevancy comparison), corpus of 5 × FY2025 10-K filings at 67,521 chunks
(the first index; 4,783 since finding 22)
of 512 chars / 50 overlap, RAGAS 0.4.3 with seed 42, `max_workers` 2, 900 s
per-job timeout, `bypass_n=True`.

**Seeds do not make this deterministic and no configuration would.** RAGAS's
`seed=42` governs its own sampling, not an LLM judge's output. See finding 7.

**The harness is checkpointed and resumable.** Agent outputs are cached after every
item, keyed on `(item id, agent model, prompt version)` plus the retrieval
configuration and the output contract's mode and version when they differ from
the defaults, so a quota wall costs one
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
# (~10 s; writes eval/benchmark_chunks.json), then score the retriever alone
# (~30 s; writes eval/results/ and regenerates LEADERBOARD.md).
python -m eval.chunk_labels
python -m eval.run_retrieval_eval --label dense

# The schema-3 re-baseline: the cached answers re-scored per chunk. Zero
# generation calls; ~430 judge calls. LLM_MODEL must name the agent whose
# answers are cached.  The cache key holds the prompt hash and, unless
# VERIFY_MODE=off, the output contract's tag; these answers were cached under
# the pre-facts prompt (sha256:d1bedac20eb2), so the two --score-only commands
# reproduce at the commit each results file records in `git_commit`.
VERIFY_MODE=off LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval \
  --score-only --judge-provider google --judge-model gemini-3.6-flash --label baseline-v3

# The reported schema-2 66-item run (kept for the record; writes schema 3 now).
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval \
  --limit 66 --judge-provider google --judge-model gemini-3.6-flash --label rerun66

# Cross-family re-score from cache — zero generation calls. The 20 item ids are
# in that results file's config.benchmark_item_ids; pass them with --ids.
VERIFY_MODE=off LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval \
  --score-only --judge-provider groq --ids <the 20 ids> --label crossjudge20

# Rebuild the leaderboard from every results file.
python -m eval.leaderboard

# What the index holds, class by class (finding 22); the CI quality gate against
# the live index; the judged smoke thresholds next to their calibration values.
python -m ingestion.audit
python -m eval.ci_gate retrieval
python -m eval.ci_gate calibrate

# The output contract on the benchmark: every answer structured, verified,
# repaired once, refused on failure. Agent calls plus one structuring call per
# item, no judge: about $0.15. Runs from before the contract were made with
# VERIFY_MODE=off (the mode did not exist); set it to re-score their cache.
VERIFY_MODE=strict LLM_MODEL=gemini-3.1-flash-lite RETRIEVAL_RERANK=true RETRIEVAL_FETCH_K=50 RETRIEVAL_TICKER_FILTER=inferred \
  python -m eval.run_eval --generate-only --label contract-v3 --cache-file eval/cache/agent_outputs_contract.json
# The same on the rebuilt index (finding 22) was labelled reindex-v3, then judged
# from its cache with no agent call:
VERIFY_MODE=strict LLM_MODEL=gemini-3.1-flash-lite RETRIEVAL_RERANK=true RETRIEVAL_FETCH_K=50 RETRIEVAL_TICKER_FILTER=inferred \
  python -m eval.run_eval --score-only --label reindex-v3 --cache-file eval/cache/agent_outputs_reindex.json

# Tool-call quality ("Tool-call quality"): computed from a results file with no
# model call and no network; output in eval/tool_metrics/. --baseline adds a
# side-by-side block; --ids restricts to a subset.
python -m eval.tool_metrics eval/results/upgrade-control-7c50eed7da71.json \
  --baseline eval/results/contract-v3-76b8f532c332.json
python -m eval.tool_metrics eval/results/b-rule-v2-2b81f7712548.json --ids <the 17 ITEMS_B ids> \
  --baseline eval/results/b-control-e8f826600800.json --out eval/tool_metrics/b-rule-v2-vs-b-control-subset.json

# The generation runs of the tool-call upgrade (about $0.15 a full run; every one
# used its own cache file, a distinct label and LANGSMITH_TRACING=false; the
# shipped configuration is AGENT_BATCH_RULE=off):
VERIFY_MODE=strict LLM_MODEL=gemini-3.1-flash-lite RETRIEVAL_RERANK=true RETRIEVAL_FETCH_K=50 RETRIEVAL_TICKER_FILTER=inferred \
  AGENT_BATCH_RULE=off python -m eval.run_eval --generate-only --label upgrade-control --cache-file eval/cache/upgrade-control.json
#   the same with AGENT_BATCH_RULE=on, --label upgrade-v1: the batching rule over all 71 items
#   --ids <12 or 17 ids> with --label a-before / a-wording / b-control / b-rule-v1 / b-rule-v2: the subsets
#   (a-before, a-wording, b-rule-v1 and b-rule-v2 ran at different prompt, tool and rule wordings: check out the
#   commit each results file records in `git_commit`; prompt_version, tool_schema_version and agent_batch_rule
#   in its config say which)

# The conversation-memory probe: eight conversations through the real /query
# path, once with a thread per conversation and once with each follow-up alone
# (30 requests, about $0.05; --dry-run prints the plan and the expected cost).
python -m eval.run_multi_turn_probe --dry-run
python -m eval.run_multi_turn_probe --label memory-v1
```

A run whose resolved configuration already has a complete results file prints
that file's report and exits; `--force` runs it again. Credentials are read
from the environment only, never accepted as flags.

---

## Limitations

Including the ones that weaken the numbers above.

1. **n = 71 (66 on the before/after model comparison) establishes no statistical
   significance.** No confidence intervals are computed because none would be
   meaningful at this size; no significance test is reported because none was run.
2. **Five of seven strata are n ≤ 8.** `multi_hop` is a single item — in the full
   benchmark, not just a sample — so a per-type multi-hop finding needs more
   *items*, not more quota. `negative` (3), `comparative` (4), `temporal` (5) and
   `list` (8) are all too thin to generalise. Only `numerical` (33) and
   `single_hop` (17) support any reading.
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
6. **The retrieval comparison covers ranking, not chunking.** Nineteen
   retriever configurations were measured (Retrieval ablation, findings
   16–17), but all over the same 512-character chunks and the same index.
   Table-aware chunking, query rewriting and a larger reranker were not
   measured; the first is ROADMAP item 1.
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
    code is a hand-synced copy, last synced at commit `11bb229` (2026-09-28),
    which carries the six upgrades measured here; the deployment machinery
    differs by design (it
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
    The reranked configuration was compared against this file rather than
    against a freshly generated dense baseline, to hold the judge spend at two
    runs: its baseline answers date from 10–11 September and its own from 27
    September, on the same model id. The judge-free deltas (+0.22 on the
    figure check, −6 terminal failures) are far larger than the variance
    finding 11 measured on repeat runs, but a same-session dense regeneration
    has not been run.
15. **The figure check tests presence, not attribution** (finding 14). An
    answer that quotes the right figure and the wrong one side by side passes
    unless a year is missing. The output contract's verifier ("Output
    contract" above) is the tighter instrument at serving time — every figure
    must be in an observation the sentence cites — but it verifies against
    what was retrieved, not against the ground truth, so a wrong-year figure
    that is in the passage still verifies.
16. **`context_recall` was designed for passages and is now handed fact
    rows.** On items answered from the XBRL table the contexts are terse
    structured lines, and RAGAS finds no support in them for the prose parts
    of a ground truth (a percentage change, a prior-year mention), so recall
    on those items reads 0.794 against 0.905 elsewhere. Reported as measured.
    `agent_hit_rate` likewise counts index chunks only and drops when a
    question is answered without a search.
17. **The contract refused nothing on the benchmark.** 70 of 70 answers
    verified on the first attempt, so the refusal path — one repair, then a
    refusal that names what could not be verified — is covered by the tests
    and by one live incident during development, not by benchmark traffic.
    The verified rate is a property of the shipped configuration (every
    served figure is a retrieved figure, finding 20), not evidence of how
    well the verifier discriminates on a system that hallucinates. The
    dense baseline would have given it three answers to refuse (finding
    20); it was not re-run under the contract to keep the spend at one
    generation pass.
18. **The CI gate measures with slack.** Its thresholds sit two items under
    the committed values, so a one- or two-item regression passes, and the
    judged smoke run scores ten items. Both gates are regression detectors;
    the numbers in this document remain the measurement.
19. **The judged before/after of each upgrade is the first index's, and the
    contract checks figures, not prose.** Findings 16–21 were judged on the
    first index; on the rebuilt index only the shipped configuration was
    judged (finding 22), where the two indexes agree to within a hundredth.
    The baseline and the reranker-only configurations were not re-judged.
    Finding 22 also shows the contract's edge: `qa_0027` served a correct
    name its retrieved chunks did not contain, verified, because the checks
    attribute figures and citations and cannot see an unsupported prose claim.
20. **The tool labels are blind but not independent.** `eval/benchmark_tools.json`
    was written by the author before any results file's tool fields were opened
    and audited by three language models given the same rules; there was no human
    annotator. Two of the four baseline first-tool misses are arguable and the
    labels were not revised after seeing them. `first_tool_ok` and
    `tool_set_ok` measure conformity to the prompt's tool rules, not correctness.
21. **Every tool-call subset is small and was chosen for a reason, and each
    configuration ran once.** `ITEMS_A` (12) was chosen on failures, so its
    improvement is overstated by regression to the mean; `ITEMS_B` (17) is where
    the batching rule acts and where its second wording was tuned. The run-to-run
    variation of the shipped configuration was measured (12% on p50 between two
    committed runs of it; 68 of 71 answers byte-identical across another pair) but
    not re-sampled for each new configuration; the two full runs are a regression
    check, not a significance test.
22. **Latency across days is confounded; only structural counts are claimed.**
    55 items that did exactly the same work in `contract-v3` and in the shipped
    run got 21% faster, nearly the whole of the overall p50 improvement. Counts
    of model calls, steps and batched calls are evidence about a change; a
    latency difference between runs on different days is not.
23. **Judge-free metrics cannot see answer correctness on a question with no
    figure.** The regression in finding 24 passed `figure_primary`, verification
    and every tool metric and was found by the author reading the 30 answers that
    changed; that read is not an independent annotation, and it covered the
    answers that changed, not all 71 in every run.
24. **The memory is in process, ephemeral and measured on a probe of eleven
    follow-ups.** It lives in one server process (a restart, or a second replica,
    forgets it), holds text only, and its protection against reusing an earlier
    turn's figure is the contract, not the history block's instruction. The probe
    is eight conversations written by the author who built the memory; its three
    isolation successes are lucky defaults. No persistent, cross-session or
    per-user memory exists or was started.
25. **Whether the model batches is its own choice.** 19% to 29% of calls were in
    batched steps on this benchmark, and the explicit rule is off. The lock on
    first use fixes the race that was measured; steady-state thread safety of the
    embedding model, the cross-encoder and Chroma was verified by about 100
    concurrent calls per tool, not proven, and the thread pool buys little wall
    time on two vCPUs.
26. **Tracing was off for every run of this upgrade** (the owner's LangSmith
    setting was overridden so no run called a non-Gemini API), so the `trace_id`
    recorded in these results files does not open as a LangSmith trace.
