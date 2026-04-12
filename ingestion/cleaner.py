# ingestion/cleaner.py
#
# PURPOSE
# -------
# Convert a raw SEC full-submission.txt file into clean plain text that is
# ready to be chunked and embedded for retrieval-augmented generation (RAG).
#
# WHY DO WE NEED A CLEANING STEP?
# --------------------------------
# SEC EDGAR full-submission files are composite SGML/HTML documents assembled
# by the EDGAR filing system.  They contain several categories of noise that
# actively harm RAG quality:
#
#   1. SEC-HEADER block – machine-readable metadata (filer CIK, accession
#      number, SIC code, mailing address, …).  This data is structured for
#      programmatic consumption, not semantic search.  Leaving it in causes the
#      embedder to waste vector dimensions on boilerplate that appears verbatim
#      in every filing, diluting the signal from the actual business narrative.
#
#   2. Inline XBRL / HTML markup – 10-K filings are submitted as iXBRL
#      (inline eXtensible Business Reporting Language) documents.  Every piece
#      of financial data is wrapped in tags like:
#        <ix:nonFraction unitRef="USD" decimals="-6" ...>94930</ix:nonFraction>
#      The tags carry zero semantic value for a language model; keeping them
#      inflates token counts, confuses sentence boundaries, and introduces
#      gibberish tokens into embeddings.
#
#   2.5 XBRL context / SGML preamble – even after BeautifulSoup removes every
#      tag, the *text content* of the iXBRL document's reference section bleeds
#      through as hundreds of bare lines before the cover page.  Empirically,
#      Apple's 10-K contains ~760 such lines:
#        • SGML envelope tokens:  "10-K", "1", "aapl-20250927.htm"
#        • CIK numbers:           "0000320193"
#        • ISO 8601 dates:        "2024-09-29", "2025-09-27"
#        • ISO currency codes:    "iso4217:USD", "xbrli:shares"
#        • FASB namespace URLs:   "http://fasb.org/us-gaap/2025#LongTermDebt…"
#        • XBRL QNames:           "aapl:A1.625NotesDue2026Member"
#        • Duration codes:        "P1Y"
#      None of these are natural language.  Leaving them in means the first
#      chunk of every filing is filled with identifiers rather than business
#      content, which badly skews the retrieval ranking for queries about the
#      cover page (company name, fiscal year, auditor).
#
#   3. CSS / JavaScript – HTML <style> and <script> blocks appear inline and
#      contribute no financial content whatsoever.  They can easily exceed the
#      size of the text they style.
#
#   4. Excessive whitespace – after stripping markup, BeautifulSoup leaves
#      runs of blank lines and leading/trailing spaces that waste context-window
#      tokens and fragment what should be contiguous paragraphs when the chunker
#      splits on line boundaries.
#
# PARSER CHOICE: lxml
# -------------------
# BeautifulSoup supports several parsers.  We use `lxml` because:
#   - It is the fastest available parser (~5-10× faster than html.parser on
#     large documents).  Apple's 10-K is ~500 K tokens of raw HTML; speed
#     matters during batch ingestion.
#   - It is lenient with malformed markup, which SEC filings frequently contain
#     (unclosed tags, mixed HTML/SGML conventions, legacy character encodings).
#   - It correctly handles the XML namespace declarations present in iXBRL.

import re
import logging
from pathlib import Path

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Regex patterns compiled once at module load ───────────────────────────────
# Compiling here (rather than inside the function) avoids re-compiling the same
# pattern on every call, which matters when cleaning hundreds of filings.

# Matches the SEC-HEADER block inclusive of its delimiters.
# re.DOTALL makes `.` match newlines so the entire multi-line block is captured.
# SEC filing convention: the header always opens with <SEC-HEADER> on its own
# line and closes with </SEC-HEADER> on its own line; there are no nested
# SEC-HEADER tags, so a non-greedy match is safe here.
_SEC_HEADER_RE = re.compile(r"<SEC-HEADER>.*?</SEC-HEADER>", re.DOTALL | re.IGNORECASE)

# Collapses runs of three or more consecutive blank lines to a single blank
# line.  Two blank lines (one empty line between paragraphs) are intentionally
# preserved because they signal section boundaries that can help the chunker
# produce more coherent splits.
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")

# Strips leading whitespace from every line.  SEC HTML is indented for human
# readability; after tag removal this indentation becomes meaningless leading
# spaces that misalign text when it is later displayed or split by the chunker.
_LEADING_WHITESPACE_RE = re.compile(r"^[ \t]+", re.MULTILINE)


def _is_prose_line(line: str) -> bool:
    """Return True if *line* looks like the start of natural-language prose.

    Used by Step 2.5 to find where the XBRL / SGML preamble ends and the
    actual document content begins.

    A line is considered prose when it satisfies **all** of:

    1. ``len > 20``   – eliminates isolated codes, CIK numbers, dates, ticker
                        symbols, and XBRL QNames (``aapl:Customer``, ``P1Y``, …)
                        which are all short strings.
    2. contains a space – requires at least two words; single-token identifiers
                          and namespace-prefixed names never contain spaces.
    3. no ``:`` character – rules out key:value metadata pairs
                            (``us-gaap:CommonStockMember``), ISO currency codes
                            (``iso4217:USD``), and XML namespace declarations.
    4. no ``/`` character – rules out all URLs (``http://fasb.org/…``), file
                            paths (``aapl-20250927.htm``), and slash-separated
                            date formats.

    Requiring *all four* conditions simultaneously keeps the test conservative.
    It is far safer to discard one legitimate short heading (e.g. "UNITED
    STATES", 13 chars) than to retain hundreds of XBRL artefact lines.  The
    first line that passes — empirically "SECURITIES AND EXCHANGE COMMISSION" —
    is still squarely on the cover page, so no business content is lost.
    """
    s = line.strip()
    return len(s) > 20 and " " in s and ":" not in s and "/" not in s


def clean_filing(file_path: str | Path) -> str:
    """Return clean plain text extracted from a raw SEC full-submission.txt.

    Cleaning pipeline
    -----------------
    1.   Read the raw file (SEC EDGAR files are ASCII / Latin-1 encoded).
    2.   Strip the <SEC-HEADER> … </SEC-HEADER> block.
    3.   Parse the remaining HTML/XBRL with BeautifulSoup + lxml and extract
         human-readable text only (tags, scripts, and styles are discarded).
    2.5. Skip the XBRL context / SGML preamble: discard every leading line
         that does not look like natural-language prose (see ``_is_prose_line``
         for the exact heuristic).  This removes ~760 lines of identifiers,
         namespace URLs, and dates that precede the cover page in iXBRL filings.
    4.   Normalise whitespace: collapse blank lines, strip line-leading spaces,
         strip leading/trailing whitespace from the document as a whole.

    Parameters
    ----------
    file_path:
        Absolute or relative path to the full-submission.txt file downloaded
        by ``ingestion.downloader``.

    Returns
    -------
    str
        Clean plain text, suitable for passing directly to the chunker.

    Raises
    ------
    FileNotFoundError
        If ``file_path`` does not exist.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Filing not found: {file_path}")

    logger.info("Cleaning filing: %s", file_path)

    # ── Step 1: Read raw content ───────────────────────────────────────────────
    # SEC EDGAR encodes full-submission files in Latin-1 (ISO-8859-1).  Using
    # errors="replace" rather than "strict" prevents a UnicodeDecodeError from
    # aborting the entire ingestion run if a single character is malformed.
    raw_text = file_path.read_text(encoding="latin-1", errors="replace")

    # ── Step 2: Strip the SEC-HEADER block ────────────────────────────────────
    # The header block contains structured metadata (CIK, accession number, SIC
    # classification, mailing address) that:
    #   a) repeats identically in every filing from the same company, giving it
    #      near-zero information content relative to the filing narrative, and
    #   b) is *not* natural-language text, so sentence-transformer embeddings
    #      trained on prose will not represent it meaningfully.
    # Removing it ensures the embedding space is used entirely for the business
    # and financial content that users actually query.
    cleaned = _SEC_HEADER_RE.sub("", raw_text)

    # ── Step 3: Strip all HTML/XML/XBRL tags with BeautifulSoup ──────────────
    # `get_text(separator="\n")` tells BeautifulSoup to insert a newline
    # wherever a block-level tag boundary existed in the source.  This preserves
    # paragraph and table-row separations in the extracted text, which would
    # otherwise be lost and cause sentences from different sections to run
    # together — a known source of hallucination in dense-retrieval RAG systems.
    #
    # BeautifulSoup automatically handles:
    #   - <style> and <script> tags (lxml marks them as non-text nodes)
    #   - HTML entities (&amp;, &nbsp;, &#160;, …) — converted to Unicode chars
    #   - Inline XBRL namespace tags (ix:nonFraction, ix:nonNumeric, …)
    soup = BeautifulSoup(cleaned, "lxml")

    # Remove <style> and <script> blocks explicitly before calling get_text().
    # Although lxml's get_text() skips the *tag* markup, it does include the
    # raw CSS/JS text content sitting inside those elements.  Decomposing them
    # first ensures that class names, selectors, and JS variable names never
    # appear in the output that the chunker will process.
    for element in soup(["style", "script"]):
        element.decompose()

    plain_text = soup.get_text(separator="\n")

    # ── Step 2.5: Skip XBRL / SGML preamble ──────────────────────────────────
    # Even after BeautifulSoup removes every tag, the iXBRL document's
    # <ix:references> block leaves its *text content* intact as hundreds of
    # bare lines before the cover page.  These are not tag bodies — they are
    # genuine text nodes that BeautifulSoup faithfully preserves:
    #
    #   • The SGML envelope (TYPE, SEQUENCE, FILENAME) contributes lines like
    #     "10-K", "1", "aapl-20250927.htm" — single tokens with no context.
    #
    #   • The iXBRL context block declares every reporting entity, period, and
    #     dimension used in the tagged financial data.  Each declaration emits
    #     one text node per element, producing hundreds of lines such as:
    #       "0000320193"                      ← CIK (10-digit filer identifier)
    #       "2024-09-29"                      ← period start date
    #       "2025-09-27"                      ← period end date
    #       "iso4217:USD"                     ← unit of measure
    #       "aapl:A1.625NotesDue2026Member"   ← bond series XBRL dimension
    #       "http://fasb.org/us-gaap/2025#…"  ← FASB concept namespace URL
    #
    # If these lines reach the chunker, the *first chunk* of every filing will
    # be dominated by identifiers rather than business content.  That first
    # chunk is the one most likely to be retrieved for cover-page queries
    # ("Who filed this?", "What fiscal year?"), so contaminating it directly
    # harms answer quality.
    #
    # Strategy: scan forward line-by-line and discard every line until we find
    # the first one that passes the _is_prose_line() test.  On Apple's FY 2025
    # 10-K this skips exactly 760 lines, landing on "SECURITIES AND EXCHANGE
    # COMMISSION" — still on the cover page, so no business content is lost.
    #
    # Edge case: if no prose line is found (e.g. a purely numeric exhibit),
    # prose_start stays at 0 and we return the full text unchanged.
    lines = plain_text.splitlines()
    prose_start = 0
    for i, line in enumerate(lines):
        if _is_prose_line(line):
            prose_start = i
            break

    if prose_start > 0:
        logger.debug(
            "Skipped %d XBRL preamble lines; document starts at: %r",
            prose_start,
            lines[prose_start][:80],
        )
        plain_text = "\n".join(lines[prose_start:])

    # ── Step 4: Normalise whitespace ──────────────────────────────────────────
    # After tag removal the text contains two whitespace artefacts:
    #
    #   a) Line-leading spaces / tabs – inherited from the HTML indentation.
    #      They look like code blocks to many downstream tools and cause
    #      sentence-transformers to tokenise "    Revenue" as a different token
    #      sequence than "Revenue".
    #
    #   b) Runs of 3+ consecutive blank lines – each removed tag can leave an
    #      empty line; a table with 50 rows can therefore leave 50 blank lines
    #      in a row.  Collapsing these keeps the document compact so that fixed-
    #      size chunks contain more actual content and fewer empty tokens.
    plain_text = _LEADING_WHITESPACE_RE.sub("", plain_text)
    plain_text = _EXCESS_BLANK_LINES_RE.sub("\n\n", plain_text)

    # Final strip removes any leading/trailing whitespace from the full document
    # (e.g. blank lines before the first paragraph or after the last one).
    plain_text = plain_text.strip()

    logger.info(
        "Cleaned filing: %d raw chars → %d clean chars (%.1f%% reduction)",
        len(raw_text),
        len(plain_text),
        100.0 * (1 - len(plain_text) / len(raw_text)) if raw_text else 0,
    )

    return plain_text


if __name__ == "__main__":
    # Quick smoke-test: clean the first AAPL filing found under data/ and print
    # the first 2 000 characters so you can visually verify the output.
    #   python -m ingestion.cleaner
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    data_root = Path(__file__).resolve().parent.parent / "data" / "sec_filings"
    filings = sorted(data_root.glob("sec-edgar-filings/*/10-K/*/full-submission.txt"))

    if not filings:
        print("No filings found under data/sec_filings — run ingestion.downloader first.")
        sys.exit(1)

    sample = filings[0]
    print(f"Cleaning: {sample}\n{'─' * 60}")
    text = clean_filing(sample)
    print(text[:2000])
    print(f"\n{'─' * 60}\nTotal characters: {len(text):,}")
