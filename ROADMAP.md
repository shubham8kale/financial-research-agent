# Roadmap

Three things worth doing next, each one prompted by something the current
evaluation actually found. Full evidence in [eval/EVALUATION.md](eval/EVALUATION.md).

---

## 1. Score contexts per chunk, not per tool observation

**Now.** The eval harness captures each tool observation as a single context
string, and an observation already concatenates all k=5 retrieved passages into
~2,100–2,600 characters. RAGAS therefore scores against blobs, not chunks.

**Why it matters.** A blob containing one relevant passage among five scores as
recalled, so `context_recall` cannot distinguish "retrieved the right passage"
from "retrieved something adjacent to it". That makes the metric too coarse to
compare retrieval strategies — a hybrid BM25 + dense retriever could measurably
beat pure dense and the current instrument would very likely score both at 1.0.

This is the blocking item. **No retrieval variant is worth building until the
measurement can tell two variants apart**, and right now it cannot. Splitting
contexts per chunk changes what every existing score means, so it needs its own
pass and a re-baseline rather than being folded into other work.

---

## 2. Measure query stability

**Now.** Nothing measures how the agent phrases its own retrieval queries.

**Why it matters.** Running the same 8 items on two different agent models —
identical k, identical embedding model, identical index, identical system
prompt — produced **different retrieved passages on 7 of 8 items**. The agent
composes its own search query inside the ReAct loop, so the query text is model
output, and different models ask the corpus different questions.

The consequence is concrete. On the multi-hop item, one model's query surfaced
the passage the ground truth depends on; the other's returned generic
boilerplate, which the agent then used to produce a fluent, wrong answer
(`context_recall` 0.00). The retriever was not at fault. Query composition was.

So "the retriever" is not really the retrieval system — the agent's query
generation is half of it, and it is currently unmeasured, untuned, and the
largest uncontrolled variable in every number reported. Worth measuring
directly: same item, repeated runs, how stable is the emitted query, and how
much does retrieval quality move with it.

---

## 3. Put the MCP path under evaluation

**Now.** The eval harness deliberately runs the in-process agent, to measure
answer quality without a network hop confounding it. That was the right call
for the eval, and it leaves the MCP server with **no test and no score against
it** — despite being a headline feature of the architecture, and the path the
deployed API tries first.

**Why it matters.** The two paths can diverge silently. `langchain-mcp-adapters`
returns tool results in a different shape from the in-process tools (a list of
content blocks rather than a plain string), so an MCP-backed run could produce
different retrieved contexts, different citations, or a different answer for the
same question, and nothing today would catch it. The API falls back from MCP to
the direct agent on failure, which means a user can silently get served by
whichever path happened to work.

The cheap first step is a contract test on the MCP tool schemas and result
shapes. The useful second step is running the existing benchmark through the MCP
agent and diffing per-item scores against the direct-agent baseline — the
harness already caches by agent configuration, so this costs one generation pass
and no new infrastructure.

---

## Not on this list, and why

**A larger benchmark.** The full 66-item set is already written and committed;
running it is a quota problem (20 Gemini requests/day/model), not an engineering
one. More items would sharpen the existing numbers without teaching anything new
about the system.

**Tuning retrieval — k, chunk size, a table-aware splitter.** All plausible
improvements, all currently unmeasurable for the reason in item 1. Tuning
against an instrument that cannot resolve the difference is how you convince
yourself of an improvement that isn't there.
