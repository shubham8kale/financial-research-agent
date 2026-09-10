# tests/test_ingestion.py
#
# Unit tests for the ingestion path: chunker, cleaner, embedder.
#
# These run without network access, without an API key, and without a built
# ChromaDB index. The embedder tests use a fake vectorstore rather than the real
# one so they exercise OUR batching and metadata logic rather than re-testing
# Chroma, and so CI never needs to download a 90 MB sentence-transformer model.
#
# The cleaner tests build minimal synthetic filings rather than reading the
# committed 10-Ks: a test that depends on a 34 MB fixture is slow, and one that
# asserts against real Apple text breaks whenever the corpus is refreshed.

from pathlib import Path

import pytest

from ingestion.chunker import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    build_splitter,
    chunk_file,
    chunk_text,
)
from ingestion.cleaner import _is_prose_line, clean_filing
from ingestion.embedder import embed_chunks, embed_ticker_chunks


# ── Chunker ──────────────────────────────────────────────────────────────────

def test_chunk_text_never_exceeds_chunk_size():
    # The size ceiling is the contract the embedding model depends on:
    # all-MiniLM-L6-v2 truncates at 256 word-piece tokens (~1000 chars), so a
    # chunk over CHUNK_SIZE risks being silently cut mid-passage at embed time.
    text = ("Apple Inc. reported total net sales of $416,161 million. " * 200)
    chunks = chunk_text(text)
    assert chunks, "expected a non-empty chunk list"
    assert all(len(c) <= CHUNK_SIZE for c in chunks), \
        f"longest chunk was {max(len(c) for c in chunks)} > {CHUNK_SIZE}"


def test_chunk_text_short_input_is_a_single_chunk():
    text = "Total net sales were $416,161 million."
    assert chunk_text(text) == [text]


def test_chunk_text_empty_and_whitespace_input():
    # An empty filing must not raise, and must not produce a phantom chunk that
    # would later be embedded as a meaningless vector.
    assert chunk_text("") == []
    assert all(c.strip() for c in chunk_text("   \n\n   \n  "))


def test_chunk_text_preserves_all_content():
    # Overlapping chunks may repeat text, but must never DROP it. A splitter
    # that silently discards characters loses filing content with no error.
    sentences = [f"Sentence number {i} about fiscal year 2025 revenue." for i in range(80)]
    text = "\n\n".join(sentences)
    joined = "".join(chunk_text(text))
    for s in sentences:
        assert s in joined, f"chunking dropped: {s!r}"


def test_chunk_text_overlaps_consecutive_chunks():
    # Overlap is what stops a fact that straddles a boundary from becoming
    # unretrievable. Assert the tail of one chunk reappears in the next.
    text = " ".join(f"token{i}" for i in range(400))
    chunks = chunk_text(text)
    assert len(chunks) > 1, "test needs a multi-chunk input"
    tail = chunks[0][-20:]
    assert tail in chunks[1], "expected trailing text of chunk 0 to recur in chunk 1"


def test_splitter_is_configured_as_documented():
    splitter = build_splitter()
    assert splitter._chunk_size == CHUNK_SIZE
    assert splitter._chunk_overlap == CHUNK_OVERLAP


def test_chunk_file_reads_and_chunks(tmp_path: Path):
    f = tmp_path / "filing.txt"
    f.write_text("Revenue grew.\n\n" * 100, encoding="utf-8")
    assert len(chunk_file(f)) >= 1


def test_chunk_file_tolerates_undecodable_bytes(tmp_path: Path):
    # SEC EDGAR files are Latin-1 and occasionally malformed. chunk_file reads
    # with errors="ignore"; a decode error here would abort a whole ingest run.
    f = tmp_path / "bad.txt"
    f.write_bytes(b"Net sales were \xff\xfe$416,161 million.")
    chunks = chunk_file(f)
    assert chunks and "416,161" in "".join(chunks)


# ── Cleaner: the prose heuristic ─────────────────────────────────────────────
#
# _is_prose_line decides where the ~760-line iXBRL preamble ends. If it is too
# permissive the first chunk of every filing fills with identifiers — and that
# first chunk is the one retrieved for cover-page questions.

@pytest.mark.parametrize("line", [
    "SECURITIES AND EXCHANGE COMMISSION",
    "Apple Inc. reported total net sales of $416,161 million.",
    "For the fiscal year ended September 27, 2025",
])
def test_is_prose_line_accepts_real_prose(line):
    assert _is_prose_line(line) is True


@pytest.mark.parametrize("line", [
    "0000320193",                        # CIK
    "2025-09-27",                        # ISO date
    "iso4217:USD",                       # unit of measure
    "aapl:A1.625NotesDue2026Member",     # XBRL dimension
    "http://fasb.org/us-gaap/2025#Rev",  # namespace URL
    "aapl-20250927.htm",                 # filename
    "10-K",                              # SGML envelope token
    "",                                  # blank
    "   ",                               # whitespace only
    "UNITED STATES",                     # short heading, deliberately rejected
])
def test_is_prose_line_rejects_xbrl_artefacts(line):
    assert _is_prose_line(line) is False


def test_is_prose_line_rejects_long_line_containing_a_url():
    # All four conditions must hold simultaneously; length alone is not enough.
    assert _is_prose_line(
        "This is a long line of text that nonetheless contains http://example.com"
    ) is False


# ── Cleaner: end-to-end on synthetic filings ─────────────────────────────────

def _write_filing(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "full-submission.txt"
    f.write_text(body, encoding="latin-1")
    return f


def test_clean_filing_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        clean_filing(tmp_path / "does-not-exist.txt")


def test_clean_filing_strips_sec_header(tmp_path: Path):
    f = _write_filing(tmp_path, (
        "<SEC-HEADER>\nCENTRAL INDEX KEY: 0000320193\nCONFORMED NAME: APPLE\n</SEC-HEADER>\n"
        "<html><body><p>Apple Inc. reported total net sales of $416,161 million.</p></body></html>"
    ))
    out = clean_filing(f)
    assert "CENTRAL INDEX KEY" not in out
    assert "CONFORMED NAME" not in out
    assert "416,161" in out


def test_clean_filing_drops_script_and_style_bodies(tmp_path: Path):
    # lxml strips the tags; the CSS/JS *text* inside them survives get_text()
    # unless the elements are decomposed first. This asserts the decompose step.
    f = _write_filing(tmp_path, (
        "<html><head>"
        "<style>.hidden-class { color: #fff; }</style>"
        "<script>var leakedVariable = 1;</script>"
        "</head><body><p>Total net sales were $416,161 million in fiscal 2025.</p></body></html>"
    ))
    out = clean_filing(f)
    assert "hidden-class" not in out
    assert "leakedVariable" not in out
    assert "416,161" in out


def test_clean_filing_skips_xbrl_preamble(tmp_path: Path):
    preamble = "\n".join([
        "10-K", "1", "aapl-20250927.htm", "0000320193", "2025-09-27",
        "iso4217:USD", "aapl:A1.625NotesDue2026Member",
        "http://fasb.org/us-gaap/2025#Revenue",
    ])
    f = _write_filing(tmp_path, (
        f"<html><body>{preamble}\n"
        "<p>SECURITIES AND EXCHANGE COMMISSION</p>"
        "<p>Apple Inc. reported total net sales of $416,161 million.</p>"
        "</body></html>"
    ))
    out = clean_filing(f)
    assert out.startswith("SECURITIES AND EXCHANGE COMMISSION"), \
        f"preamble was not skipped; output began: {out[:80]!r}"
    assert "iso4217:USD" not in out
    assert "0000320193" not in out


def test_clean_filing_collapses_blank_lines_and_leading_whitespace(tmp_path: Path):
    f = _write_filing(tmp_path, (
        "<html><body><p>SECURITIES AND EXCHANGE COMMISSION</p>"
        "<p>        Indented line that is long enough to be prose.</p>"
        "</body></html>\n\n\n\n\n"
    ))
    out = clean_filing(f)
    assert "\n\n\n" not in out, "runs of 3+ newlines should be collapsed"
    assert not any(line.startswith((" ", "\t")) for line in out.splitlines())
    assert out == out.strip()


def test_clean_filing_output_feeds_the_chunker(tmp_path: Path):
    # The two stages are only useful composed; assert the contract between them.
    body = "".join(
        f"<p>Fiscal 2025 segment {i} reported growth across all regions.</p>"
        for i in range(60)
    )
    f = _write_filing(tmp_path, f"<html><body><p>SECURITIES AND EXCHANGE COMMISSION</p>{body}</body></html>")
    chunks = chunk_text(clean_filing(f))
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_SIZE for c in chunks)


# ── Embedder ─────────────────────────────────────────────────────────────────

class _FakeVectorstore:
    """Records add_texts calls so batching and metadata can be asserted."""

    def __init__(self):
        self.calls: list[tuple[list[str], list[dict] | None]] = []

    def add_texts(self, texts, metadatas=None):
        self.calls.append((list(texts), list(metadatas) if metadatas else None))
        return [f"id-{i}" for i in range(len(texts))]

    @property
    def all_texts(self):
        return [t for texts, _ in self.calls for t in texts]

    @property
    def all_metas(self):
        return [m for _, metas in self.calls if metas for m in metas]


def test_embed_chunks_empty_list_is_a_noop():
    # An empty list must not reach add_texts: Chroma raises on empty input, and
    # a filing that cleaned to nothing should skip rather than abort the run.
    vs = _FakeVectorstore()
    assert embed_chunks([], vectorstore=vs) is vs
    assert vs.calls == []


def test_embed_chunks_batches_above_the_chroma_limit(monkeypatch):
    # Chroma rejects oversized upserts, so embed_chunks splits them. Patch the
    # batch size rather than generating 5,000 chunks.
    import ingestion.embedder as embedder
    monkeypatch.setattr(embedder, "CHROMA_BATCH_SIZE", 10)
    vs = _FakeVectorstore()
    chunks = [f"chunk {i}" for i in range(25)]
    embedder.embed_chunks(chunks, vectorstore=vs)

    assert [len(t) for t, _ in vs.calls] == [10, 10, 5]
    assert vs.all_texts == chunks, "batching must not drop or reorder chunks"


def test_embed_ticker_chunks_attaches_citation_metadata():
    # ticker and chunk_idx are what the agent cites and what per-company
    # filtered retrieval matches on; wrong indices produce wrong citations.
    vs = _FakeVectorstore()
    chunks = ["alpha", "beta", "gamma"]
    embed_ticker_chunks("AAPL", chunks, source_path="/tmp/aapl.txt", vectorstore=vs)

    metas = vs.all_metas
    assert len(metas) == len(chunks)
    assert [m["chunk_idx"] for m in metas] == [0, 1, 2]
    assert {m["ticker"] for m in metas} == {"AAPL"}
    assert {m["source"] for m in metas} == {"/tmp/aapl.txt"}


def test_embed_ticker_chunks_defaults_missing_source_to_empty_string():
    # Chroma rejects None-valued metadata, so the default must be "" not None.
    vs = _FakeVectorstore()
    embed_ticker_chunks("MSFT", ["only chunk"], vectorstore=vs)
    assert vs.all_metas[0]["source"] == ""
    assert all(v is not None for v in vs.all_metas[0].values())
