# agent/observations.py
#
# PURPOSE
# -------
# One parser for the text the retrieval tools return (their "observations"),
# shared by the API, which turns observations into source citations, and the
# eval harness, which turns them into per-chunk contexts and retrieved-chunk
# ids.  Before this module existed the API had its own line walker and the
# harness kept each observation as one opaque blob, so the two layers could not
# agree on what had been retrieved.
#
# Both tool sources — the in-process @tool functions in agent/financial_agent.py
# and the FastMCP tools in mcp_server/server.py — emit the same two layouts:
#
#   search_filings:     "[1] ticker=AAPL  chunk_idx=395\n    <snippet>"
#   compare_companies:  "=== AAPL ===\n  [1] chunk_idx=395\n      <snippet>"
#
# A chunk is identified everywhere by "<TICKER>_10K_chunk_<idx>" — the string
# the API already returns as `source_file` — so a citation in the UI, a context
# in a results file and a label in eval/benchmark_chunks.json all name the same
# thing.  There is deliberately no other chunk identifier.

import re
from dataclasses import dataclass

# "=== AAPL ===" section header emitted by compare_companies.
SECTION_RE = re.compile(r"^===\s*(\S+)\s*===")
# "[1] ticker=AAPL  chunk_idx=395"  or  "  [1] chunk_idx=395" (ticker from section).
CHUNK_HEADER_RE = re.compile(r"^\s*\[\d+\]\s*(?:ticker=(\S+)\s+)?chunk_idx=(\S+)")
# Last-resort pattern for an observation whose layout has drifted.
FALLBACK_RE = re.compile(r"ticker=(\S+)\s+chunk_idx=(\S+)")

_CHUNK_ID_RE = re.compile(r"^(?P<ticker>[A-Z0-9.\-]+)_10K_chunk_(?P<idx>\d+)$")


def chunk_id(ticker: str, chunk_idx) -> str:
    """Canonical id for one indexed chunk: ``AAPL_10K_chunk_395``."""
    return f"{str(ticker).upper()}_10K_chunk_{chunk_idx}"


def parse_chunk_id(cid: str) -> tuple[str, int] | None:
    """Inverse of :func:`chunk_id`; None if *cid* is not a canonical chunk id."""
    m = _CHUNK_ID_RE.match(cid or "")
    if not m:
        return None
    return m.group("ticker"), int(m.group("idx"))


@dataclass(frozen=True)
class ObservedChunk:
    """One retrieved passage as it appeared in a tool observation."""
    ticker: str
    chunk_idx: str
    text: str

    @property
    def chunk_id(self) -> str:
        return chunk_id(self.ticker, self.chunk_idx)


def observation_text(content) -> str:
    """Flatten a ToolMessage.content into one string.

    In-process tools return ``str``; MCP tools arrive through
    langchain-mcp-adapters as ``list[dict]`` of ``{"type": "text", "text": ...}``
    blocks.  Both shapes are accepted so a caller never has to know which tool
    source produced the message.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(block, str) and block:
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def has_chunk_markers(content: str) -> bool:
    """True if *content* carries at least one ``chunk_idx=`` header."""
    return bool(FALLBACK_RE.search(content)) or any(
        CHUNK_HEADER_RE.match(line) for line in content.split("\n")
    )


def parse_observation(content: str) -> list[ObservedChunk]:
    """Split one tool observation into its individual passages, in rank order.

    Walks the text line by line: a section header sets the current ticker for
    compare_companies output, a chunk header opens a passage, and the following
    non-blank lines up to the next header are that passage's snippet.  Chunks
    whose snippet is empty are dropped — there is nothing to cite or score.
    """
    chunks: list[ObservedChunk] = []
    current_ticker: str | None = None
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        section = SECTION_RE.match(line)
        if section:
            current_ticker = section.group(1).upper()
            i += 1
            continue

        header = CHUNK_HEADER_RE.match(line)
        if header:
            ticker = (header.group(1) or current_ticker or "UNKNOWN").upper()
            chunk_idx = header.group(2).strip().rstrip(",")
            i += 1
            snippet_lines: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    break
                if SECTION_RE.match(nxt) or CHUNK_HEADER_RE.match(nxt):
                    break
                snippet_lines.append(nxt.strip())
                i += 1
            text = " ".join(snippet_lines).strip()
            if text:
                chunks.append(ObservedChunk(ticker=ticker, chunk_idx=chunk_idx, text=text))
            continue
        i += 1
    return chunks


def retrieved_chunk_ids(observations: list[str]) -> list[str]:
    """Unique chunk ids across *observations*, in order of first appearance.

    Order of first appearance is the agent-level rank: the position at which the
    agent first saw the chunk across its whole tool-calling trajectory.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for obs in observations:
        for c in parse_observation(obs):
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                ordered.append(c.chunk_id)
    return ordered
