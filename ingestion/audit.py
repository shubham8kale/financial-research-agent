# ingestion/audit.py
#
# PURPOSE
# -------
# Say what is actually in the index.  Every chunk is classified by the kind
# of text it holds — prose, escaped markup, XBRL identifiers or numeric
# fragments, JSON, or a short fragment — and the shares are printed overall
# and per ticker.  Cheap (no model), and the check that would have caught
# finding 22 on day one: the shipped index was 18% prose because the cleaner
# read every document in the EDGAR envelope, not just the 10-K.
#
#   python -m ingestion.audit                 # the live index at CHROMA_PERSIST_DIR
#   python -m ingestion.audit --from-filings  # clean + chunk the committed filings, no index
#   python -m ingestion.audit --json          # machine-readable

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CLASSES = ("prose", "numeric table", "escaped markup", "identifiers", "json / metadata", "short fragment")

_MARKUP_RE = re.compile(r"<\s*/?\s*(td|tr|div|span|p|table|font|br|html|body)\b|&lt;|&gt;|style=\"", re.I)
_JSON_RE = re.compile(r"MetaLinks|\"instance\"\s*:|\"dts\"|\"report\"\s*:|\"nsuri\"|\"localname\"|\"presentation\"|"
                      r"\.xsd\b|_cal\.xml|_def\.xml|_lab\.xml|_pre\.xml", re.I)
_IDENT_RE = re.compile(r"\b(us-gaap|dei|xbrli|srt|ecd|iso4217|xbrldi|xlink)[:_][A-Za-z]|\b[a-z]+:[A-Z][A-Za-z]+Member\b|"
                       r"\bC_[0-9a-f]{8}|\bF_[0-9a-f]{8}|http://fasb\.org|http://xbrl\.", re.I)
SHORT_CHARS = 120


def classify(text: str) -> str:
    """One of CLASSES for a chunk.

    A financial table is content — numbers with line-item names — and is
    counted as "numeric table", never as junk; "identifiers" is XBRL names,
    context ids, namespace URLs and the like, which say nothing to a reader.
    """
    t = (text or "").strip()
    if len(t) < SHORT_CHARS:
        return "short fragment"
    if _MARKUP_RE.search(t):
        return "escaped markup"
    if _JSON_RE.search(t):
        return "json / metadata"
    words = t.split()
    long_tokens = sum(1 for w in words if len(w) > 30)
    idents = len(_IDENT_RE.findall(t))
    if idents >= 3 or long_tokens > 2:
        return "identifiers"
    letters = sum(c.isalpha() for c in t)
    if letters / max(len(t), 1) < 0.4:
        return "numeric table"
    return "prose"


def audit(chunks) -> dict:
    """chunks: iterable of (ticker, text).  Returns counts overall and per ticker."""
    overall: Counter = Counter()
    per_ticker: dict[str, Counter] = {}
    n = 0
    for ticker, text in chunks:
        c = classify(text)
        overall[c] += 1
        per_ticker.setdefault(ticker, Counter())[c] += 1
        n += 1
    return {
        "n_chunks": n,
        "overall": {c: overall.get(c, 0) for c in CLASSES},
        "shares": {c: round(overall.get(c, 0) / n, 4) if n else 0.0 for c in CLASSES},
        "per_ticker": {t: {"n_chunks": sum(cnt.values()), **{c: cnt.get(c, 0) for c in CLASSES}}
                       for t, cnt in sorted(per_ticker.items())},
    }


def iter_index_chunks(persist_dir: Path, collection: str, page: int = 5000):
    import chromadb

    col = chromadb.PersistentClient(path=str(persist_dir)).get_collection(collection)
    n = col.count()
    offset = 0
    while offset < n:
        res = col.get(include=["documents", "metadatas"], limit=page, offset=offset)
        for doc, meta in zip(res["documents"], res["metadatas"]):
            yield str(meta.get("ticker", "?")), doc
        offset += page


def iter_filing_chunks(tickers=None):
    """Clean and chunk the committed filings exactly as the pipeline would, without touching an index."""
    from ingestion.chunker import chunk_text
    from ingestion.cleaner import clean_filing
    from ingestion.downloader import TARGET_TICKERS
    from ingestion.pipeline import find_filing

    for ticker in tickers or TARGET_TICKERS:
        path = find_filing(ticker)
        if path is None:
            continue
        for chunk in chunk_text(clean_filing(path)):
            yield ticker, chunk


def render(report: dict) -> str:
    lines = [f"chunks: {report['n_chunks']}", "", "| class | chunks | share |", "|---|---|---|"]
    for c in CLASSES:
        lines.append(f"| {c} | {report['overall'][c]:,} | {report['shares'][c]:.1%} |")
    lines += ["", "| ticker | chunks | " + " | ".join(CLASSES) + " |", "|---|---|" + "---|" * len(CLASSES)]
    for t, row in report["per_ticker"].items():
        n = row["n_chunks"]
        lines.append(f"| {t} | {n:,} | " + " | ".join(f"{row[c] / n:.0%}" for c in CLASSES) + " |")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Classify what the index (or the cleaner's output) holds.")
    p.add_argument("--from-filings", action="store_true", help="Clean and chunk the committed filings instead of reading the index.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.from_filings:
        report = audit(iter_filing_chunks())
        report["source"] = "cleaner output over data/sec_filings"
    else:
        from ingestion.embedder import CHROMA_PERSIST_DIR, COLLECTION_NAME
        report = audit(iter_index_chunks(CHROMA_PERSIST_DIR, COLLECTION_NAME))
        report["source"] = str(CHROMA_PERSIST_DIR)
    print(json.dumps(report, indent=1) if args.json else render(report))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
