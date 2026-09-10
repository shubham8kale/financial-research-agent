# Evaluation

## What this document is

A record of what was actually measured, what the measurement is worth, and what
broke while measuring it. The headline numbers are at the bottom on purpose:
with **n = 8**, the findings about the measurement are more informative than the
scores themselves.

**The one-line summary:** on 8 items, the agent scores faithfulness 0.955,
answer_relevancy 0.903 and context_recall 0.813 with full metric coverage — and
n = 8 is a smoke test, not a benchmark. The full 66-item benchmark is committed
and runnable; free-tier quota at 20 requests/day/model is what stopped a full
run, not the harness.

Everything below is reproducible from committed artefacts:

| Artefact | What it is |
|---|---|
| [`benchmark.csv`](benchmark.csv) | 66 labelled items, six question types |
| [`benchmark_smoke.csv`](benchmark_smoke.csv) | 5 of those items, for `--dry-run` and CI |
| [`run_eval.py`](run_eval.py) | The harness |
| [`results/smoke8-69c426f.json`](results/smoke8-69c426f.json) | **The reported run.** 8 items, per-item answers, contexts and scores |
| [`results/validation-E-69c426f.json`](results/validation-E-69c426f.json) | Same 8 items, different agent model — the cross-model observation |
| [`results/validation-E-rescore-69c426f.json`](results/validation-E-rescore-69c426f.json) | 3 items re-scored after the timeout fix — evidence for finding 2 |

---

## Method: what was held constant

Every value below is also stamped into `config` in each results file, so a score
can never be separated from the configuration that produced it.

| | Value |
|---|---|
| Agent model | `gemini-2.5-flash` (provider: google), temperature 0 |
| Judge model | `openai/gpt-oss-120b` (provider: groq), temperature 0 configured |
| Judge sampling | `bypass_n=True`, `answer_relevancy` strictness 3 |
| Embeddings (retrieval **and** judge) | `sentence-transformers/all-MiniLM-L6-v2`, 384-dim |
| k (passages per search) | 5 |
| Agent recursion limit | 20 (≈10 tool-call round trips) |
| System prompt version | `sha256:d1bedac20eb2` (hash of the live prompt text) |
| RAGAS | 0.4.3, seed 42, `max_workers` 2, per-job timeout 900 s |
| Judge throttle | 0.1 requests/second |
| Corpus | 5 × FY2025 10-K filings, 67,521 chunks, 512 chars / 50 overlap |
| Metrics | faithfulness, answer_relevancy, context_recall |

**Seeds do not make this deterministic, and no amount of configuration would.**
RAGAS's `seed=42` governs its own sampling, not an LLM judge's output. See
finding 3.

**Provenance note.** The results files record `git_commit: 69c426f` with
`git_dirty: true` — they were produced by the harness as it stood immediately
before the commit that added it (`ce4d4d4`). That is what the flags are for; the
dirty bit is recorded rather than hidden.

---

## Findings

### 1. Free-tier quota, not engineering, set n = 8

The intent was a 66-item run. It is not achievable on the free tier.

Google **no longer publishes per-model free-tier rate limits** in its
[rate-limit docs](https://ai.google.dev/gemini-api/docs/rate-limits) — the page
now defers to a per-account dashboard. So the ceiling was measured directly, from
live 429 response bodies:

```
"quotaId":      "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
"quotaValue":   "20"
"quotaMetric":  "generativelanguage.googleapis.com/generate_content_free_tier_requests"
```

**20 requests per day, per model, per project.** Separate models have separate
buckets. Groq's free tier for `openai/gpt-oss-120b` is 30 RPM / 1,000 RPD /
8,000 TPM / 200,000 TPD.

Measured cost per item, not estimated:

| | Measured | 66 items | Free-tier ceiling | Time to run |
|---|---|---|---|---|
| Agent (Gemini) | ~2.3 calls/item | ~150 calls | 20/day/model | **~9 days** |
| Judge (Gemini) | 6 calls, ~7.7k tokens/item | ~396 calls | 20/day/model | ~20 days |
| Judge (Groq) | 6 calls, ~7.7k tokens/item | ~510k tokens | 200k tokens/day | ~3 days |

The judge was moved to Groq precisely because 396 judge calls cannot fit a
20/day bucket. The agent side remains the binding constraint at ~9 days, which
is why the reported run is 8 items and not 66.

This is a constraint worth stating rather than working around. Paid quota was
available and deliberately not used: the whole project is a free-tier
demonstration, and an evaluation that quietly requires a credit card is not the
same artefact.

**What this cost in interpretive power:** everything. n = 8 supports no claim
about the system's accuracy. It supports claims about whether the *harness*
works, which is what the rest of this document is about.

### 2. A NaN that looked like a judge verdict and was actually our own throttling

The first validation run scored `faithfulness` on only 5 of 8 items. Three NaN.

A NaN in RAGAS output is indistinguishable from "the judge could not score this
item" — and it is *not* a zero, so pooling it into a mean silently understates
the metric while the column still looks complete.

The cause was `TimeoutError` inside `ragas.executor`, at RAGAS's default 180 s
per-job timeout. `faithfulness` makes **two sequential** judge calls (statement
extraction, then NLI over the contexts), and each has to wait its turn behind
every other in-flight job in a shared rate limiter. Under a 0.1 rps throttle,
that exceeds 180 s. So the harness's own quota discipline was manufacturing
missing data.

Fixed by raising the timeout to 900 s, dropping `max_workers` from 16 to 2
(extra workers add no throughput behind a shared limiter — they only lengthen
each job's wait), and absorbing Groq 429s with `max_retries=8`. Re-scoring the
three items produced 100 % coverage with no NaN
([`validation-E-rescore`](results/validation-E-rescore-69c426f.json)).

The harness now **counts** executor failures into `judge_diagnostics` and labels
a `TimeoutError` as a harness artefact in its own output, because the lesson
generalises: a throttled evaluation harness can fabricate missing data, and if it
does so silently you will report the fabrication as a model result.

### 3. `answer_relevancy` is not deterministic at temperature 0

The earlier 5-item baseline had NaN `answer_relevancy` on 3 of 5 items. Two
distinct mechanisms turned out to be involved, and only one of them was the NaN.

**The NaN.** `ragas/metrics/_answer_relevance.py` returns NaN in exactly one
case: `all(q == "" for q in gen_questions)`. The metric prompts the judge to
generate `strictness` (3) counter-questions from the answer and scores the cosine
similarity between those and the real question. The previous judge,
`gemini-3.1-flash-lite-preview`, returned **one** candidate for an `n=3` request
and returned it malformed, so the metric raised and RAGAS recorded NaN.
Retiring that model fixed it. `bypass_n=True` now makes RAGAS issue N separate
single-candidate requests instead of trusting a provider to honour `n`, so the
degradation cannot recur silently on any provider.

**The non-determinism, which is not a bug and is not fixable.**
`ragas.llms.base.BaseRagasLLM.get_temperature` returns `0.3` whenever `n > 1`,
and `LangchainLLMWrapper.agenerate_text()` **overwrites the model's configured
temperature** with it. Because `answer_relevancy` always requests 3 generations,
its judge calls run at temperature 0.3 no matter what the harness sets:

```python
# ragas/llms/base.py
def get_temperature(self, n: int) -> float:
    """Return the temperature to use for completion based on n."""
    return 0.3 if n > 1 else 0.01
```

That is deliberate on RAGAS's part — the metric is *defined* over a diverse
sample of counter-questions, and forcing temperature 0 would collapse the sample
and change what the metric means. So it is recorded rather than suppressed:
every results file carries `judge_temperature_configured: 0.0` alongside a
`judge_temperature_note` stating the override.

**Consequence for anyone reading a number here:** `faithfulness` and
`context_recall` are reproducible run-to-run; `answer_relevancy` is not, and
re-running it will move the third decimal. Do not treat small
`answer_relevancy` differences as signal.

### 4. A failure that looks like retrieval failure and is not

The April 5-item run scored `faithfulness` 0.0 on `qa_0060`, a cross-company
comparison. Read as a score, that says the retriever fed the model unsupported
evidence. The actual answer was:

> `Sorry, need more steps to process this request.`

That is LangGraph exhausting `recursion_limit=20`. The agent never finished
reasoning. Retrieval was not implicated at all — the item is an
**agent-capability** failure wearing a retrieval-failure score.

Pooling those two into one mean is how a benchmark stops measuring what it
claims to. The harness now detects the marker per item and records
`recursion_limit_hit`, and `print_report` counts those items separately with an
explicit note that they are not retrieval failures. The recursion limit itself
was left at 20: raising it would improve the score, which makes it a tuning
change, and this was an evaluation pass.

No item in the reported run hit the limit, so the detector is verified against
the marker string but has no live occurrence in `smoke8`.

### 5. Retrieval quality varies with the agent model, at fixed k and fixed index

The 8 items were run twice — once on `gemini-2.5-flash`, once on
`gemini-3.1-flash-lite-preview` — with identical retriever, identical embedding
model, identical index, identical k and identical prompt.

**The retrieved passages differed on 7 of 8 items.**

This is not non-determinism in the vector store. The agent *writes its own search
query* inside the ReAct loop, so the query text is model output, and different
models compose different queries. k, the embeddings and the index are all fixed;
what varies is what gets asked of them.

The clearest case is `qa_0047` (multi_hop — "what key personnel do Meta's
operations depend on, and what risks to that person are highlighted?"):

| | `gemini-2.5-flash` | `gemini-3.1-flash-lite-preview` |
|---|---|---|
| Top passage | META chunk 432 — generic key-personnel boilerplate | AMZN chunk 149, plus a second observation |
| Answer | "members of management, key engineering, product development…" | "specifically identifying Mark Zuckerberg… high-risk activities including combat sports" |
| context_recall | **0.00** | 1.00 |
| faithfulness | 0.79 | 1.00 |

The ground truth is the Zuckerberg passage. One model's query surfaced it; the
other's returned boilerplate that reads plausible and is wrong. The score
correctly punished the miss.

`qa_0064` (negative — Vision Pro revenue) shows the mirror image: the reported
run retrieved a product-announcements chunk rather than the net-sales table
(context_recall 0.50) yet still answered correctly (faithfulness 1.00), because
a question about an *absent* disclosure can be answered from weak evidence. A
correct answer for a poor reason.

Two items (`qa_0001`, `qa_0037`) produced byte-identical answers across both
models despite different retrieved contexts, which is why their scores match to
four decimal places. That is expected, not a bug.

### 6. `context_recall` is the metric that actually discriminates here — but read it carefully

In the preview-model run, `context_recall` was **1.0000 on all 8 items**. That
looked like a dead metric, and mid-pass I recorded it as one. The reported run
disproves that: it ranges 0.00–1.00 and is the only metric that separated the
multi_hop failure from the rest. The earlier reading was premature.

The real caveat is structural, and it is a harness artefact worth knowing about:
`_extract_contexts()` captures **each tool observation as one context string**,
and an observation already concatenates all k=5 passages into ~2,100–2,600
characters. So RAGAS scores against observation-sized blobs, not individual
chunks, which makes `context_recall` coarser than a per-chunk measurement would
be — a blob containing one relevant passage among five scores as recalled.

This was deliberately **not** changed. Splitting contexts per chunk would alter
what every score in this document means, and it belongs in the same pass as a
retrieval-variant comparison, not smuggled into a measurement pass.

### 7. Methodology lesson: my own quota estimate was wrong by ~50×, and how it surfaced

The initial survey estimated the Gemini free tier at ~1,000 requests/day and
projected a 66-item run at 45–60 minutes. The real ceiling is 20/day and the
real projection is ~9 days.

The estimate came from prior knowledge of Google's published limits. It was never
checked against the API, because the docs page that would have corrected it no
longer contains numbers.

What surfaced it was not review but **cost**: a diagnostic for finding 3 (5 items
× 3 generations × 2 configurations = ~30 calls) exhausted the entire daily bucket
for `gemini-2.5-flash-lite` in one command, and the 429 body carried the true
limit. The error was discovered by spending the resource it mis-measured.

Two things generalise:

- **A quota assumption is a measurement, and it should be taken from the API, not
  from memory.** The 429 body is authoritative and free to read; the docs page
  was neither.
- **Diagnostics consume the budget the run needs.** The diagnostic was necessary
  work, but running it on the same model earmarked for the baseline cost a day.
  Isolating diagnostic spend onto a model the reported run does not use is now
  the practice — the reported run and validation run E deliberately use
  different agent models, and the cache key includes the agent model so they
  cannot contaminate each other.

### 8. Every score is pinned to a judge model ID, because judge models disappear

The previous judge for this project, `gemini-3.1-flash-lite-preview`, changed
behaviour under us mid-project — and a prior judge model was deprecated outright
by its provider partway through the work. Free-tier models are exactly the ones
providers retire without notice.

That means a score has no meaning as an absolute quantity. `faithfulness 0.955`
is shorthand for "0.955 as scored by `openai/gpt-oss-120b` on Groq, at
`bypass_n=True`, RAGAS 0.4.3, against answers from `gemini-2.5-flash` at prompt
version `sha256:d1bedac20eb2`". Change any of those and the number is not
comparable — not wrong, *incomparable*.

So every results row carries `judge_model`, `judge_provider`, `agent_model`,
`k` and `prompt_version` individually, not just the file header. A row lifted out
of the file and pasted into a table stays interpretable. And `prompt_version` is
derived by hashing the live prompt text rather than stored as a hand-bumped
constant, so it cannot drift out of sync with the prompt it names.

---

## Results

### The reported run

8 items, agent `gemini-2.5-flash`, judge `openai/gpt-oss-120b` (Groq).
Complete: 8/8 generated, 8/8 scored, zero executor failures, zero NaN.
Judge cost: 48 calls, 46,237 in / 15,579 out tokens.

**Metric coverage — 100 % on all three metrics.** No mean below is taken over a
partial column.

| Metric | Mean | n scored | NaN |
|---|---|---|---|
| faithfulness | **0.9554** | 8 | 0 |
| answer_relevancy | **0.9029** | 8 | 0 |
| context_recall | **0.8125** | 8 | 0 |

### By question type

Every row carries n. **None of these rows is a finding.** At n = 1 a cell is one
observation; at n = 2 it is two.

| question_type | n | faithfulness | answer_relevancy | context_recall |
|---|---|---|---|---|
| single_hop | 2 | 1.0000 | 0.9689 | 1.0000 |
| numerical | 2 | 1.0000 | 0.9690 | 1.0000 |
| multi_hop | **1** | 0.7857 | 0.8022 | 0.0000 |
| comparative | **1** | 0.8571 | 0.9018 | 1.0000 |
| negative | **1** | 1.0000 | 0.8023 | 0.5000 |
| list | **1** | 1.0000 | 0.8412 | 1.0000 |

Stated plainly: **`multi_hop`, `comparative`, `negative` and `list` are n = 1 —
anecdotal, not findings.** `single_hop` and `numerical` at n = 2 are too few to
generalise. The only defensible reading of this table is directional: simple
factual lookup and single-company numerical extraction behave well; the one
multi-hop item failed at retrieval. Whether that generalises is exactly what a
66-item run would tell us and this run cannot.

For reference, the strata available in the committed 66-item benchmark are
numerical 33, single_hop 17, list 8, comparative 4, negative 3, **multi_hop 1**.
Note that `multi_hop` is n = 1 in the full benchmark too — a per-type finding for
multi-hop needs more *items*, not just more quota.

### Per item

| id | type | faithfulness | answer_relevancy | context_recall |
|---|---|---|---|---|
| qa_0001 | single_hop | 1.0000 | 0.9590 | 1.0000 |
| qa_0037 | single_hop | 1.0000 | 0.9788 | 1.0000 |
| qa_0005 | numerical | 1.0000 | 0.9408 | 1.0000 |
| qa_0019 | numerical | 1.0000 | 0.9973 | 1.0000 |
| qa_0023 | list | 1.0000 | 0.8412 | 1.0000 |
| qa_0047 | multi_hop | 0.7857 | 0.8022 | 0.0000 |
| qa_0060 | comparative | 0.8571 | 0.9018 | 1.0000 |
| qa_0064 | negative | 1.0000 | 0.8023 | 0.5000 |

### Secondary observation: cross-model, same items

**Clearly labelled as what it is:** 8 items on a stable model versus the same 8
items on a *preview* model, and the preview run's scores have mixed provenance —
it was scored across a harness that changed mid-pass, and only 3 of its 8 items
were re-scored under the final configuration. It is an observation, not a
comparison of record. It is included because it is what produced finding 5.

Divergence where both runs scored the item:

| Metric | n | mean Δ (2.5-flash − preview) | mean \|Δ\| |
|---|---|---|---|
| faithfulness | 5 | −0.0429 | 0.0429 |
| answer_relevancy | 8 | +0.0497 | 0.0837 |
| context_recall | 8 | −0.1875 | 0.1875 |

The `context_recall` divergence is not judge disagreement — it is the retrieval
divergence in finding 5, driven almost entirely by `qa_0047` (Δ −1.00) and
`qa_0064` (Δ −0.50). Two agent models asked the corpus different questions and
got different evidence.

A proper cross-family judge-bias check (re-judging these items with a Gemini
judge to quantify judge disagreement independently of agent behaviour) was
scoped and **not run** — it needed ~20 Gemini calls, i.e. a full day's bucket,
and the deadline took priority. It remains the most valuable next measurement,
because with a Groq judge and a Gemini agent the current setup is already
cross-family and that property has not been verified.

---

## Where the system is weak

Read against n = 8 — these are hypotheses the evidence is consistent with, not
established results.

- **Multi-hop retrieval is the visible failure mode.** The single multi_hop item
  failed at the retrieval step, not the generation step: the agent's
  self-composed query returned generic boilerplate instead of the specific
  passage, and it then answered fluently and wrongly from it. Fluent
  wrongness on a missed retrieval is the most dangerous failure a financial
  Q&A system can have, and it is the one this run caught.
- **Retrieval is only as good as the query the agent writes.** Finding 5 shows
  7 of 8 items retrieving different passages under a different agent model. The
  retriever is not the whole retrieval system; the agent's query composition is
  part of it, and it is currently unmeasured and untuned.
- **Comparative questions strain the agent loop.** `qa_0060` scored the lowest
  faithfulness of any completed item (0.857), and the same item exhausted
  `recursion_limit` entirely on an earlier model. Cross-company synthesis is
  where the loop is closest to its budget.
- **Table-derived numbers remain the known chunking weakness.** 21 of the 66
  items are flagged `requires_table`. The 512-character recursive splitter
  breaks 10-K tables across chunk boundaries. This run's two numerical items
  both scored 1.0, which is encouraging and is also two items.
- **`negative` questions can be right for the wrong reason.** `qa_0064` answered
  correctly on context_recall 0.50. A question about an absent disclosure is
  answerable from weak evidence, so negative items flatter the system and should
  be read with that in mind.

---

## Limitations

1. **n = 8 cannot establish statistical significance.** Neither could the
   intended 66. No confidence interval is computed here because none would be
   meaningful; no significance test is reported because none was run. Four of
   the six question-type strata are n = 1.
2. **Five companies only** — AAPL, MSFT, GOOGL, AMZN, META. All large-cap US
   technology firms, all FY2025 10-Ks. Nothing here speaks to other sectors,
   smaller filers, older filings, or other filing types.
3. **One embedding model.** `all-MiniLM-L6-v2` throughout, for both retrieval and
   the judge's relevancy comparison. Using the same 384-dim space to retrieve
   and to score relevance is convenient and self-contained, and it also means
   the judge shares the retriever's blind spots.
4. **No retrieval-variant comparison.** One k (5), one chunk size (512/50), one
   splitter, one retrieval strategy (agent-composed semantic search). Nothing
   was varied, so nothing here says any of those choices is good — only that
   this configuration produces these scores.
5. **Free-tier judge models get deprecated without notice.** This project has
   already had a judge model change behaviour mid-flight and an earlier one
   deprecated by its provider. Every score in this document is therefore
   comparable **only** within the pinned judge model ID recorded alongside it.
   A future re-run against a different judge is a new baseline, not a
   continuation of this one.
6. **`answer_relevancy` is not reproducible to the third decimal** (finding 3).
   RAGAS runs it at temperature 0.3 by construction.
7. **`context_recall` is measured over observation-sized context blobs**, not
   individual chunks (finding 6), which makes it coarser than the per-chunk
   metric a reader might assume.
8. **The judge is a single judge.** No inter-judge agreement was measured, and
   the cross-family bias check was scoped but not run. Every number is one
   model's opinion.
9. **The reported run was produced from a dirty working tree** (`git_dirty:
   true`, `git_commit: 69c426f`), immediately before the commit that added the
   harness. Recorded rather than concealed, but a strictly clean-tree run would
   be better provenance.

---

## Reproducing this

```bash
# No API calls — validates benchmark parsing, argparse and imports. This is CI.
python -m eval.run_eval --dry-run

# The reported run (needs GEMINI_API_KEY + RAGAS_JUDGE_API_KEY in .env).
# ~20 Gemini calls and ~62k Groq tokens; expect ~25 minutes end to end.
python -m eval.run_eval \
  --ids qa_0001,qa_0005,qa_0023,qa_0047,qa_0060,qa_0064,qa_0019,qa_0037 \
  --judge-provider groq --label smoke8

# Re-judge cached answers with a different judge — zero generation cost.
python -m eval.run_eval --score-only --judge-provider groq --label rejudge

# Accumulate generation across days when a daily bucket runs out.
python -m eval.run_eval --generate-only --limit 8
```

The agent model comes from `LLM_MODEL` in `.env`. Credentials are read from the
environment only and never accepted as command-line flags.

**Attempting the full 66 items** will stop on a quota wall, write everything that
completed, and print the exact command to resume. That is the designed
behaviour, not a failure — the cache is keyed per item, so resuming costs only
the items that did not finish.
