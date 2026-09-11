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
`mean_failures_as_zero` alongside RAGAS's default. 17 tests in
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

This was deliberately not changed — splitting contexts per chunk would alter what
every score in this document means, and it belongs in the same pass as a
retrieval-variant comparison. It is item 1 on the [roadmap](../ROADMAP.md), and it
is the reason no hybrid-retrieval comparison has been attempted: the instrument
cannot currently separate two retrieval strategies.

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

| | Before run | After run | Cross-judge |
|---|---|---|---|
| Agent model | `gemini-2.5-flash-lite` | `gemini-3.1-flash-lite` | `gemini-3.1-flash-lite` |
| Judge model | `gemini-3.6-flash` | `gemini-3.6-flash` | `openai/gpt-oss-120b` (Groq) |
| Items | 66 | 66 | 20 |
| Judge calls | 386 | 396 | 121 |
| Results file | [`baseline66`](results/baseline66-af83fa6.json) | [`rerun66`](results/rerun66-af83fa6.json) | [`crossjudge20`](results/crossjudge20-af83fa6.json) |

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

# The reported 66-item run.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval \
  --judge-provider google --judge-model gemini-3.6-flash --label rerun66

# Cross-family re-score from cache — zero generation calls.
LLM_MODEL=gemini-3.1-flash-lite python -m eval.run_eval \
  --score-only --judge-provider groq --label crossjudge20
```

Credentials are read from the environment only, never accepted as flags.

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
6. **No retrieval-variant comparison.** One k, one chunk size, one splitter, one
   strategy. Nothing was varied, so nothing here says any of those choices is good
   — and finding 9 explains why a variant comparison is not yet measurable.
7. **The judge-bias result is n = 3** on the comparative stratum. A signal, not a
   proof (finding 4).
8. **`answer_relevancy` is not reproducible to the third decimal** (finding 7).
9. **`context_recall` is measured over observation-sized blobs**, not individual
   chunks (finding 9), making it coarser than a reader would assume.
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
