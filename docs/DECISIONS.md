# Decisions

One entry per design decision that a reader might ask about, with the
evidence it rests on. Numbers live in [eval/EVALUATION.md](../eval/EVALUATION.md)
and the results files under [eval/results/](../eval/results/); each entry
points at the section or finding that measured it. Where a decision was
reversed, the entry says so and why.

## Corpus and ingestion

**Five FY2025 10-K filings, committed to the repository.** The SEC EDGAR
full-submission files are public, 84 MB together and under GitHub's per-file
limit, and committing them makes every index reproducible from a clean clone
with no network step. Five large-cap technology filers is a demo-sized corpus
by design (limitation 4).

**Clean only the 10-K document of a submission.** The original cleaner read
the whole EDGAR envelope, and 74% of the index it built was escaped markup,
XBRL identifiers and MetaLinks JSON from the exhibits, the taxonomy files
and the XBRL instance; 23% was prose (finding 22; `python -m ingestion.audit` shows the composition). Keeping the
10-K document alone cut the index from 67,521 chunks to 4,783, all prose and
tables, and cut a rebuild from 26 minutes to about one.

**512-character chunks with 50 characters of overlap, recursive splitting.**
The size every number in the evaluation was measured with. A 10-K table cut
mid-row is the known cost — table questions are the weakest stratum at every
retrieval configuration — and table-aware chunking is ROADMAP item 1, to be
measured with the retriever-only runner before any judged run.

**all-MiniLM-L6-v2 embeddings, run locally.** Pinned, free, CPU-fast and
reproducible; a general-web model whose weakness on financial terminology
was answered by reranking its candidates rather than by a larger encoder
(the ablation). The one constant to change if that is revisited is
`EMBEDDING_MODEL`, followed by a re-index and a relabel.

**SQLite for the tagged XBRL facts.** 6,089 facts is a small relational
table queried by (ticker, concept, fiscal year, segment); a vector store's
metadata filter is neither indexed for that nor able to express it.
[docs/adr/0001](adr/0001-sqlite-for-xbrl-facts.md).

## Retrieval

**Reciprocal Rank Fusion with k = 60 for hybrid search.** RRF needs no score
calibration between a cosine similarity and a BM25 score, and 60 is the
constant from the original RRF paper (Cormack, Clarke and Buettcher, 2009);
the sweep to 20 and the sparse-weight sweep are in the ablation matrix.

**Dense candidates into a cross-encoder, not hybrid ones.** Nineteen
configurations were scored on the current index ("Retrieval ablation" in
eval/EVALUATION.md). The shipped pipeline — dense search over 50 candidates,
reranked, under the inferred ticker filter — has hit@5 0.662 against 0.507 for
dense alone. Hybrid BM25 fusion is the best *unreranked* option (0.592) but
feeding its candidates to the reranker scores lower (0.634 against 0.662).
The first ablation, on the uncleaned index, had hybrid losing to dense
outright; that reversed on the clean index (finding 22), which is why this
is recorded as a measurement to re-run after any ingestion change rather
than a preference.

**A cross-encoder over 50 dense candidates.** Reranking is the single largest
gain in the matrix at every index; 100 candidates gained nothing over 50
(finding 16) and costs twice the latency.

**An inferred ticker filter, not an oracle one.** Restricting search to the
one company a question names costs nothing at query time and matched the
oracle filter that the agent could never have (finding 17).

**The code default stays dense top-5; the measured configuration is switched
on by environment variables.** Flipping the default would have orphaned every
cached agent output keyed on the untagged configuration; the deployment sets
the variables instead (README, "Retrieval switches").

## Answering

**Facts first, search second.** The prompt sends any exact-figure question
to `lookup_financial_fact` and any arithmetic to `compute_metric`. The
temporal stratum, where the agent quoted the prior year on four of five
items under two retrievers, closed only when the fiscal year became a query
argument (finding 18), and in-head arithmetic vanished once the calculator's
result was an observation (finding 20).

**`gemini-3.1-flash-lite` at temperature 0.** The model the benchmark was
measured on; its predecessor returned an empty answer on 10 of 66 items
(finding 1). Temperature 0 for a tool-calling loop that must decide reliably
when to stop.

**Terminal failures are named states, not answers.** An empty answer or a
recursion-limit placeholder is refused at every layer and counted as a
failure in every mean, never excluded (finding 1).

**The fact lookup says `concept` is required in every place the model reads
it, and the tool never guesses.** 7 of 131 tool calls in the committed
cost run were rejected for a missing `concept`, on six items. With start
offsets recorded, the 5 rejections of an unchanged re-run were 3 single calls
and 2 inside one batched step, so the omission is not a property of batching.
The fix is wording: the first sentence of the tool
description, its parameter text and rule 7 of the system prompt say `concept`
is REQUIRED on every call and one call returns one concept for one year, with
an example call; the MCP server's description mirrors it and the contract test
pins it (finding 23). A tolerant tool (accept a missing concept and return an
observation that names the missing argument) was specified as the fallback and
not built, because the wording was enough; a default `concept` was ruled out
before any measurement, since a wrong default silently returns the wrong kind of
figure, which is worse than a rejected call. The marker such a tool would
return and the metric that counts it as a failure stay in the code, so the
instrument is ready if a tool ever needs it.

**Batching is a prompt rule behind `AGENT_BATCH_RULE`, off by default.** The
tool node already runs the calls of one step concurrently and the model
already batches some questions. A rule that asks for more of it changed what
it was written to change (a five-company question from four serial searches to
one `compare_companies` call; agent model calls 3.06 to 2.53 per query on the
17 items it targets, against a control with the rule off) and, on the full
benchmark, produced one wrong answer that the figure check and the contract
verified (finding 24). Because nothing judge-free reads an answer to a question
with no figure, the default is decided by a measured gate that includes reading
the changed answers, and the rule did not pass it. It stays available behind
the switch; the wording fix above is on unconditionally.

**Every lazily built singleton is built once, under a lock.** A tool node runs
one step's tool calls on worker threads, and the first batched step of a cold
process raced to build the Chroma client: six of eight threads failed. The
vectorstore, the process-wide retriever, its reranker and sparse index, the
cross-encoder model and the fact store now build under a double-checked lock,
taken only while the resource is unbuilt, so a warm call never queues (39 ns)
and steady-state searches stay concurrent. The alternative, serialising every
search, would have given up the wall time batching exists to save.

## Verification

**Every answer becomes cited claims and is checked before it is served.** A
faithfulness judge cannot see a correct-looking derivation from grounded
numbers (finding 20) or a right figure under the wrong year (finding 2); the
contract's checks are deterministic, run on every answer, and refuse rather
than serve what they cannot attribute. What happens when verification fails:
one repair attempt with the failures shown to the structuring model, then
in strict mode a refusal that names what could not be verified; `VERIFY_MODE`
selects strict, warn or off. The price is a second model call: about +22% in
dollars and a doubled median latency (finding 21).

## Conversation memory

**Follow-ups are answered from the text of earlier turns, kept in the server's
memory, and handed to the model as one human message.** The API is stateless,
so "and Microsoft?" failed. `agent/memory.py` keeps, per client-minted thread
id, the question and answer text of up to six served turns (500 and 1,200
characters each), and a request that carries the id sends the model a labelled
history block followed by the current question. The reasons it is a text window
and not a LangGraph checkpointer: it behaves identically on the direct and the
MCP agent, because it only builds the text the agent is given; the history stays
small (a few hundred characters a turn instead of thousands of tokens of tool
output a turn); the output contract stays turn-scoped, since it checks figures
against this turn's observations, and the history block tells the model to reuse
no figure and call the tools again for every one it states, so no figure can be
served from an earlier turn without being re-checked this turn; and a request
with no thread id is byte for byte what it was before. Only an answer that
passed verification (or ran with it skipped) is remembered, never a refusal,
an unverified draft or a terminal failure (finding 25).

**The memory is in process and ephemeral, on purpose.** The Space's disk is
ephemeral and the free-tier rule forbids a paid store; the memory is bounded in
turns, threads and idle time, and it forgets on restart, which the UI says. A
persistent store, an account system or cross-session memory is a separate
decision with its own privacy questions; none was started here.

## Evaluation

**Judge-free metrics first, a judge only on winners.** The retriever-only
runner and the figure check cost nothing and run in seconds; a judge pass
over 71 items costs about $1.10 (cost and latency section). Every retrieval
change is measured for free before anything is spent on it.

**Why faithfulness alone is not enough.** It scored the wrong-year answers
1.0 and the in-head arithmetic 1.0 (findings 2 and 20); the figure check
and the grounding check exist because of that, and the primary-figure rate
is reported only over items whose ground truth holds a figure other than a
year (figure check version 4).

**Contexts are scored per chunk with a provenance prefix.** Stripping the
`[META 10-K, chunk 412]` header cost 0.12 faithfulness on identical answers
(finding 15); the prefix is part of the hashed configuration.

**Thresholds sit two items under the committed value.** A CI gate is a
regression detector: one or two items of movement is within the measured
run-to-run noise, three is not (CI quality gate section).

**The judged gate runs strict and pins the contract version.** Calibrated
on a strict-mode run on the rebuilt index (finding 22), the gate scores what
production serves; a run under another verify mode or contract version fails
until it is recalibrated, so the contract cannot drift unmeasured.

**CI rebuilds the real index.** With the clean corpus embedding in about a
minute, CI builds the same index the docs measure from the committed filings
and caches it on their hash; an earlier committed slice was retired when the
rebuild became cheap.

**Tool labels live in a sidecar file, not a column of the benchmark.**
`benchmark_version` hashes the benchmark CSV and the chunk labels, and a results
file's identity includes it, so a new column would have orphaned every committed
results file. `eval/benchmark_tools.json` is outside that hash and each
tool-metrics output records its sha256 instead. It was written before any
results file's tool fields were opened, from the question, ground truth,
`question_type`, `section`, the prompt rules and the tool docstrings only,
audited by three independent labellers and widened where a defensible
alternative existed. It was not revised after the first results (R2), including
the four first-tool misses, two of which are arguable. The labels are blind but
not independent of the author: the labellers are language models reading the same
rules (the "Tool-call quality" section's limits).

**An experiment gets its own cache file, a distinct label and a recorded tool
schema fingerprint.** The prompt version hashes the system prompt only, so a
tool docstring or argument schema change leaves it, and every cache key built
from it, unchanged; the harness would serve answers generated before the change.
`tool_schema_version` is recorded and hashed into each results file's config
(not into the cache key), and every experiment names its own cache.

**A subset run is paired with a control that changes one thing.** The first
measurement of the batching rule changed two things at once (the Phase 2
wording and the rule) and moved everything; the same items with the rule off
moved almost as much, which showed the wording, not the rule, had done it. The
control cost $0.05 and is the reason the rule is not credited with the wording's
effect.

**Judge-free metrics do not see answer correctness on a question with no
figure, so a change to the agent's behaviour is checked by reading the answers
that changed.** The full run with the batching rule passed every judge-free
gate and one answer was wrong; reading the 30 answers whose text changed found
it (finding 24). A judge would have scored it too, at about $1.10; reading 30
answers cost nothing.

## Deployment

**The Space ships a prebuilt index and is synced by hand.** Hugging Face's
free CPU builder timed out re-embedding the old 67,521-chunk index at image
build, so the index went in through Git LFS. The rebuild is now cheap
enough to do at build time; that change is deliberate and separate.

**MIT license.** So the code can be reused and deployed without asking.
