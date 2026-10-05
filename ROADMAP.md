# Roadmap

What is worth doing next, each item prompted by something the evaluation
actually found. Full evidence in [eval/EVALUATION.md](eval/EVALUATION.md);
every number below traces to a file under [eval/results/](eval/results/).

---

## Done: score contexts per chunk, not per tool observation

**Was.** The eval harness captured each tool observation as one context string
holding all k = 5 passages, so `context_recall` could not tell "retrieved the
right passage" from "retrieved something next to it". No retrieval variant was
worth building because the instrument could not have told two apart.

**Now (results schema 3).** Contexts are split per chunk. Every benchmark item
is labelled with the chunk(s) that hold its reference passage
([`eval/benchmark_chunks.json`](eval/benchmark_chunks.json): on the current
index 65 located verbatim, 3 by prefix, 3 by hand). A retriever-only runner scores the
retriever against those labels with no LLM call at all, in under 30 seconds,
and a ground-truth figure check scores whether the answer quoted the right
number. Every run carries a config hash and lands on
[`eval/results/LEADERBOARD.md`](eval/results/LEADERBOARD.md).

**What it measured on the shipped dense retriever**, question sent verbatim,
k = 25, 71 items: a relevant chunk in the top 5 on **50.7%** of items, in the
top 25 on 74.7%, MRR 0.358, nDCG@5 0.369, 13.6 ms per query
([`retrieval-dense-5571ce86feed.json`](eval/results/retrieval-dense-5571ce86feed.json); the same 50.7% on the first index).
Half the time the dense retriever does not put the right page in front of the
model. That is the number the next item exists to move.

---

## Done: retrieval variants, as a measured ablation

**Was.** One retrieval strategy: dense similarity over 512-character chunks,
top 5, no filter, no reranking. hit@5 0.507.

**Now.** Every retrieval technique is a field on `RetrievalConfig`
([retrieval/retriever.py](retrieval/retriever.py)), set by `RETRIEVAL_*` env
vars and defaulting to dense top-5, the code default. Nineteen configurations were
scored with the retriever-only runner — BM25, hybrid fusion with weight and
depth sweeps, a cross-encoder reranker at three fetch depths, ticker filters
inferred from the question and the oracle upper bound — for no LLM calls
([eval/EVALUATION.md](eval/EVALUATION.md), "Retrieval ablation", findings
16–17). The winner, dense + reranker over 50 + inferred ticker filter, takes
hit@5 to **0.662** and MRR from 0.358 to 0.541 at 0.75 s per query on
the rebuilt index. Hybrid BM25 fusion is the best unreranked retriever there
(0.592) and loses under the reranker (0.634 against 0.662); on the
first index it had lost to dense outright, an artefact of the exhibits
(finding 22). It stays in the code, off.

---

## Done: structured facts and a calculator

**Was.** Every figure came from reading prose or a table chunk. With the
fiscal year unstated the model quoted the prior year on the temporal items
(finding 2), and better retrieval did not change that: `temporal`
figure_exact was 0.40 before and after the reranker.

**Now.** The inline XBRL in each filing on disk is parsed into
`data/facts.sqlite` (6,089 tagged facts, three seconds, last step of the
ingestion pipeline; [docs/adr/0001](docs/adr/0001-sqlite-for-xbrl-facts.md))
and the agent has `lookup_financial_fact` and `compute_metric`
([eval/EVALUATION.md](eval/EVALUATION.md), "Structured facts"). The fiscal
year is a filter, the calculator does the arithmetic. On the 45 benchmark
items whose ground truth carries a figure other than a year, the answer
contains the figure the question asked for on **100%** (`figure_primary`), against
91% with the reranker alone and 73% for the dense baseline; every temporal
item now names the year asked. The
fact tool was used on 34 of 71 items and the calculator on 8.

**Left open.** `agent_hit_rate` counts index chunks only, so it falls when
a question is answered from the fact table without a search (0.66 to 0.42);
read it now as which path answered, not as quality. One `comparative` item
(`qa_0062`) hit the recursion limit on this run and on every run of this
configuration since — a consistent failure of the fact-first prompt on that
item, not variance. The concept resolver is a synonym
table plus a name search; a line item it does not know ("Google Search &
other revenues") falls back to search, which still answered correctly.

---

## Done: a meter on every run

**Was.** Nothing measured what a query cost. `tokens_used` in the API was
always null, no results file carried latency, and the only trace was a
Python log line.

**Now.** [agent/meter.py](agent/meter.py) rides every agent run as a
callback: latency, model calls, tokens, cost at a dated price table the
repo owns, each tool call with its duration, and the root run id LangSmith
shows as the trace. The API returns it as `meta` on `/query` and as a
`meta` SSE event; the chat UI shows seconds, tokens, dollars, tools and the
trace id under each answer; every results record carries the same fields
and the leaderboard has cost-per-query and p50 columns
([eval/EVALUATION.md](eval/EVALUATION.md), "Cost and latency").

**Measured on the shipped configuration**, all 71 items, tracing on: p50
1.9 s, p95 5.9 s, 6,199 tokens in and 94 out per query, **$0.0017 per
query**, $0.12 for the whole benchmark. 37% of wall time is inside tools,
almost all of it the reranked search at 0.8 s per call. The same run is a
second sample of the fact-tool configuration 1.7 hours apart: 68 of 71
answers byte-identical, every judge-free metric within noise — the noise
floor the next comparisons are read against.

**Left open.** 7 of 58 fact lookups were rejected because the model left
out the required `concept` argument (it mostly retried; on one item it
repeated the mistake, on another it fell back to search); each is a wasted model
round trip and an argument for a default or a louder description on that
parameter. The reranker is the latency; on the free-tier Space its share
will be larger than here.

---

## Done: a quality gate in CI

**Was.** CI linted, ran a dry-run that made no retrieval call, and ran the
unit tests. A change that halved hit@5 would have passed.

**Now.** Every pull request rebuilds the index from the committed filings
(about a minute, cached), scores the dense baseline and the shipped
configuration against the 71 labelled items and fails below thresholds set
two items under the committed values ([eval/ci_gate.py](eval/ci_gate.py),
[eval/ci_gate.json](eval/ci_gate.json); a threshold above its own source
value fails the dry-run). The judged run is a manual workflow over ten fixed
items with thresholds calibrated from the committed judged run, about $0.20
a click ([eval/EVALUATION.md](eval/EVALUATION.md), "CI quality gate").

**Left open.** The gate is a regression detector, not a quality measure:
two items of slack means a one- or two-item loss passes. A chunking change
(item 1 below) invalidates the labels; they are regenerated with one command
and the gate refuses to score an index they do not match.

---

## Done: the index is the 10-K, and the review's other findings

**Was.** A second review found that the index was mostly not the 10-K, that
the primary-figure headline counted years, that the MCP server was untested
and drifting, that the API had no question bound and kept spending after a
client left, that the fact store's segment filter matched axis names and its
CAGR could return a complex number, that the harness's core loop was
untested, and that the repository had no license.

**Now.** The cleaner keeps only the 10-K document and the index is 4,783
chunks of prose and tables instead of 67,521 chunks, three-quarters of them
XBRL markup, identifiers and metadata; every retrieval number was re-measured and finding 22 records what
moved (the winner stayed, hybrid fusion flipped from loser to best unreranked
option, the rebuild fell from 26 minutes to 72 seconds). The figure check is
at version 4; the MCP server honours the retriever's configuration, runs off
the event loop, has DNS-rebinding protection on and a contract test; the API
bounds the question, shares one deadline across both agents and cancels the
agent when the stream's client leaves; the two fact-store bugs are fixed with
tests; the generate / cache / stop loop is tested; and
[docs/DECISIONS.md](docs/DECISIONS.md) and a license exist.

**Left open.** The shipped configuration was judged on the rebuilt index
(faithfulness 0.949 against 0.957, inside run-to-run variance) and the
judged CI gate is recalibrated on that run in strict mode; the baseline and
reranker-only configurations were not re-judged, so findings 16–21's
before/after remain the first index's. The Space still ships its index
through LFS although a build-time rebuild would now fit.

---

## Done: an output contract, verified before the answer leaves the API

**Was.** The answer was prose with sources listed beside it. Nothing checked
that a figure in the prose came from a source, and finding 20 shows the
judge cannot: three in-head calculations on the dense baseline scored a
faithfulness of 1.0.

**Now.** Every answer becomes claims with cited observation ids (one extra
model call) and is checked deterministically — cited ids exist, every figure
is in an observation its sentence cites, nothing dropped — repaired once,
then refused ([agent/contract.py](agent/contract.py)). The API returns the
verdict and marks cited sources; in strict mode it serves a refusal instead
of an unverifiable draft; the UI shows both. Measured over all 71 items:
**70 of 70** verified on the first attempt, 77 of 77 figures supported, 0
refused, drafts unchanged; +$0.0004 and +2.2 s per query
([eval/EVALUATION.md](eval/EVALUATION.md), "Output contract").

**Left open.** The verifier attributes figures, not years: a right figure
for the wrong year that is in the cited passage verifies (limitation 15).
The structuring call doubles p50 latency (finding 21); a smaller model for
it, or a record emitted in the agent's final turn, would take that back.
Zero refusals here means the refusal path is exercised by tests and one live
incident, not by benchmark traffic (limitation 17).

---

## Done: tool calls measured, and the dropped argument fixed

**Was.** The model sometimes called `lookup_financial_fact` with a ticker and a
fiscal year and no `concept`: 7 of 131 calls on 6 items in the committed cost
run, each a wasted model round trip. Nothing measured tool use at all: no
arguments, no start times, no notion of the right tool for a question.

**Now.** The meter records each call's arguments, start offset and error;
[`eval/tool_metrics.py`](eval/tool_metrics.py) scores validity, first tool, tool
set, batching, redundant calls and lookup arguments against a label file
written before any results were read
([`eval/benchmark_tools.json`](eval/benchmark_tools.json)); and `concept` is
stated as required in the tool's description, its parameter text, rule 7 and
the MCP description. On the 12 items chosen for failing, 5 of 20 lookups were
rejected unchanged and 0 of 15 after (Fisher p = 0.057); on the shipped
configuration over all 71 items, **0 of 119 calls were rejected**
([eval/EVALUATION.md](eval/EVALUATION.md), "Tool-call quality", finding 23).

**Left open.** The labels are blind but written by the author and audited by
models, not by a person, and two of the four baseline first-tool misses are
arguable. A tolerant tool (accept a missing concept, return an observation that
names it) was specified and not built because the wording was enough.

---

## Done: batching measured, a race fixed, a rule left off

**Was.** LangGraph runs the calls of one model step concurrently and the model
sometimes issued several, but nothing counted it, and `search_filings` had never
run in parallel in any committed run.

**Now.** Start offsets make batching measurable: 19% of calls in the shipped run
were issued in a batched step. Running searches concurrently on the real index
found a real defect, a race on the first use of a cold process (6 of 8 threads
failed building the Chroma client), now fixed with a lock taken only on the first
build; steady-state concurrent calls were byte-identical to sequential ones. A
prompt rule asking for more batching was measured against a rule-off control:
over the whole benchmark it cut agent model calls from 2.49 to 2.37 per query
and took a five-company question from four serial searches to one
`compare_companies` call (finding 24).

**Left open.** The rule is **off**. It made one answer wrong, deterministically
(`qa_0008`, 4 of 4 runs: ten lookups fanned out over Apple's product
categories, taken for its reportable segments), and nothing judge-free reads an
answer to a question with no figure; reading the changed answers found it. The
next step is a rule that does not fan out over entities the question did not
name, or a check that reads non-figure answers. The wording fix above already
does most of the batching, and a latency gain is not claimed: the day moved the
p50 by about as much as the whole difference.

---

## Done: conversation memory

**Was.** The API was stateless, so a follow-up such as "and Microsoft?" had no
company, year or metric to resolve.

**Now.** An optional `thread_id` on `/query` and `/query/stream` gives the model
the text of the thread's earlier served turns ([agent/memory.py](agent/memory.py)),
in front of the question, with an instruction to reuse no figure and re-retrieve
every one, so the verification contract still checks every figure against this
turn. The web UI mints one id per page load, has a "New chat" control and says
that follow-ups are remembered for the session. On eight small conversations,
11 of 11 follow-up turns were answered with memory and 3 of 11 without (the 3
were lucky defaults); N is 11 (finding 25).

**Left open.** The memory is in process: a restart or a second replica forgets
it, and nothing persists across sessions or users, a decision with its own
privacy questions that was not started. The probe is eight conversations written
by the author who built the memory.

---

## 1. Table-aware chunking, then re-label

**Now.** 26 of 71 items depend on a table and they are the stratum nothing
above fixed: hit@5 0.462 → 0.500 with the reranker, against 0.533 → 0.711
for prose items. The recursive splitter cuts a 10-K table mid-row, so the row
holding the figure is often not the chunk that names the line item, and a
cross-encoder reads a bare run of numbers badly.

**What it takes.** A structure-preserving pass at ingest that keeps a table
as one chunk with a serialised text form, then a rebuilt index — which
changes every chunk id, so
[eval/benchmark_chunks.json](eval/benchmark_chunks.json) is regenerated by
`python -m eval.chunk_labels` against the new index and the two manual
overrides are re-resolved. Measure on the `requires_table` and `temporal`
strata first, with the retriever-only runner, before any judge call.

**One ingestion fact to fold into the same pass.** The exhibit and XBRL
bloat that once made Microsoft half the index is gone (finding 22), but the
splitter still emits heading-only chunks: 12% of the rebuilt index is under
120 characters. Folding those into their neighbour belongs to the same
structure-preserving pass.

---

## 2. Measure query stability

**Now partly measured.** The agent composes its own search queries, so query
text is model output. Instrument v2 puts a number on what that costs: with the
benchmark question sent to the retriever verbatim, a relevant chunk lands in
the top 5 on 50.7% of items; across the agent's own tool calls on the same
items it lands anywhere in what the agent saw on **43.7%**
(`agent_hit_rate`, reported on every generation run). The agent's rewording
loses retrieval relative to the plain question. With the reranker and ticker
filter switched on, `agent_hit_rate` rises to 66.2% — the reranker recovers
some of what the rewording loses, because it re-reads the query and the
candidates together.

**Still unmeasured.** Stability. The same item on two agent models changed the
retrieved passages on 7 of 8 (finding 3), and the same item on the same model
answered with a different year on two runs (finding 2). Worth measuring
directly: same item, repeated runs, how stable is the emitted query, and how
much does `agent_hit_rate` move with it. The per-record `retrieved_chunk_ids`
field now makes that a diff between two results files.

---

## 3. Put the MCP path under evaluation

**Now.** The eval harness deliberately runs the in-process agent, to measure
answer quality without a network hop confounding it. That was the right call
for the eval, and it leaves the MCP server with **no score against it** —
despite being a headline feature of the architecture. A contract test now pins
its tool discovery, argument schemas and observation format
(`tests/test_mcp_contract.py`), but no benchmark run goes through it.

The deployed Space does not exercise it either: `/health` there reports
`mcp_server: false`, so production always runs the in-process fallback. Running
an MCP server as a second container is not something a single-container demo
deployment warrants, so this is a deliberate deployment choice rather than a
defect — but it does mean the MCP path has neither an evaluation score nor
production traffic behind it.

**Why it matters.** The two paths can diverge silently. `langchain-mcp-adapters`
returns tool results in a different shape from the in-process tools (a list of
content blocks rather than a plain string), so an MCP-backed run could produce
different retrieved contexts, different citations, or a different answer for the
same question, and nothing today would catch it. The API falls back from MCP to
the direct agent on failure, which means a user can silently get served by
whichever path happened to work.

The cheap first step, a contract test on the MCP tool schemas and result
shapes, is done (`tests/test_mcp_contract.py`). The useful second step is running the existing benchmark through the MCP
agent and diffing per-item scores against the direct-agent baseline — the
harness already caches by agent configuration, and both tool sources now parse
through `agent/observations.py`, so this costs one generation pass and no new
infrastructure.

---

## Not on this list, and why

**A larger benchmark.** All 71 items have been run, so size is no longer the
constraint it was when free-tier quota capped a run at 20 requests per day per
model. More items of the existing kinds would tighten the numbers without
teaching anything new. What would teach something is more items in the *thin*
strata — `multi_hop` is a single item in the whole benchmark, and `negative`
and `comparative` are 3 and 4 — since those are exactly the rows the results
tables cannot support. Added only when an upgrade specifically needs them.

**Exhaustive answer-bearing labels.** The chunk labels mark the passage the
benchmark author cited, not every chunk that states the same fact. On 12 of 71
items the dense baseline produced every ground-truth figure without touching a
labelled chunk.
Retrieval scores are therefore a lower bound. Labelling every answer-bearing
chunk would tighten them, and would also invite labelling toward whatever the
retriever happens to return; the figure check already answers "was the answer
right" independently, so this is deferred.
