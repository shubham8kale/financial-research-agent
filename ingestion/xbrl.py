# ingestion/xbrl.py
#
# PURPOSE
# -------
# Every headline number in a 10-K is already machine-tagged.  The primary
# document of each full submission on disk is inline XBRL: each figure is an
# <ix:nonFraction> element carrying a US-GAAP concept name, a context (which
# period, which segment) and a unit.  This module parses those tags into a
# small SQLite table so the agent can look a figure up EXACTLY — the right
# concept, the right fiscal year, the right segment — instead of reading it
# off a chunk of prose and choosing a year by guesswork.
#
# WHY PARSE THE FILE ON DISK RATHER THAN CALL THE SEC'S JSON API
# --------------------------------------------------------------
# The submissions are already committed; parsing them is offline, repeatable
# and takes seconds.  The companyfacts API would give the same facts, but every
# eval number in this repository is meant to be reproducible from the repo
# without a network call at build time.  The API is the obvious fallback for
# filings this repo does not carry.
#
# WHAT IS STORED
# --------------
# One row per (concept, context, unit) with the parsed numeric value, the
# period (start/end or instant), a derived fiscal year, and the context's
# dimensions serialised as "axis=member;axis=member" so segment facts
# ("iPhone net sales", "Intelligent Cloud revenue") are addressable.  The
# fiscal year of a duration fact is the calendar year its period ends in; for
# the five companies here that equals the DEI DocumentFiscalYearFocus of the
# filing, which is checked at build time and recorded in the filings table.
#
# WHY SQLITE
# ----------
# ~6,000 facts across five filings.  SQLite ships with Python, costs nothing
# in the image, and answers every query the tools make in microseconds.  A
# columnar engine earns its dependency at hundreds of filings, not five.
# See docs/adr/0001-sqlite-for-xbrl-facts.md.

import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lxml import etree

from ingestion.downloader import DATA_DIR as FILINGS_ROOT
from ingestion.embedder import CHROMA_PERSIST_DIR
from ingestion.submission import primary_document  # noqa: F401 — re-exported

logger = logging.getLogger(__name__)

# The filings are the committed inputs (data/sec_filings/); the fact table is
# a derived artefact and lives next to the index it accompanies, so pointing
# CHROMA_PERSIST_DIR elsewhere moves the table too but never the inputs.
DATA_DIR = CHROMA_PERSIST_DIR.parent
FILINGS_DIR = FILINGS_ROOT / "sec-edgar-filings"
FACTS_DB = DATA_DIR / "facts.sqlite"

# The submission envelope is parsed by ingestion/submission.py, shared with the
# text cleaner so both read the same 10-K document.


@dataclass
class Fact:
    ticker: str
    cik: str
    concept: str
    value: float
    raw_text: str
    unit: str
    decimals: str | None
    scale: int
    period_type: str            # "duration" | "instant"
    period_start: str | None    # ISO date
    period_end: str             # ISO date (end date or instant)
    days: int | None
    fiscal_year: int
    dims: str                   # "axis=member;axis=member" sorted, "" when none
    dim_count: int
    context_id: str
    fact_id: str | None
    source_file: str

    def key(self) -> tuple:
        return (self.concept, self.context_id, self.unit, self.value)


@dataclass
class Filing:
    ticker: str
    cik: str
    fiscal_year_focus: int | None
    period_end: str | None
    document_type: str | None
    registrant: str | None
    source_file: str
    n_facts: int = 0
    notes: list[str] = field(default_factory=list)


# ── Raw submission handling ──────────────────────────────────────────────────

def _attr(el, name: str) -> str | None:
    """Attribute lookup tolerant of the HTML parser lower-casing names."""
    for k, v in el.attrib.items():
        if k.lower() == name.lower():
            return v
    return None


def _local(tag: str) -> str:
    return tag.rsplit(":", 1)[-1].lower()


def _parse_number(text: str, fmt: str | None, scale: int, sign: str | None) -> float | None:
    t = (text or "").strip().replace("\xa0", "").replace(" ", "")
    if fmt and fmt.endswith(("fixed-zero", "zerodash")):
        return 0.0
    if t in ("", "-", "—", "–"):
        return 0.0 if fmt and "zero" in fmt else None
    negative = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if fmt and fmt.endswith("num-comma-decimal"):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        value = float(t)
    except ValueError:
        return None
    value *= 10 ** scale
    if sign == "-" or negative:
        value = -value
    return value


def _days(start: str | None, end: str) -> int | None:
    if not start:
        return None
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None


def parse_inline_xbrl(doc_html: str, ticker: str, source_file: str) -> tuple[Filing, list[Fact]]:
    """Parse one inline-XBRL 10-K document into (Filing, facts)."""
    parser = etree.HTMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(doc_html.encode("utf-8", errors="replace"), parser)

    # contexts: id -> (period_type, start, end, dims)
    contexts: dict[str, tuple[str, str | None, str, str]] = {}
    for ctx in root.iter():
        if not isinstance(ctx.tag, str) or _local(ctx.tag) != "context":
            continue
        cid = _attr(ctx, "id")
        if not cid:
            continue
        start = end = instant = None
        dims: list[str] = []
        for el in ctx.iter():
            if not isinstance(el.tag, str):
                continue
            name = _local(el.tag)
            text = (el.text or "").strip()
            if name == "startdate":
                start = text
            elif name == "enddate":
                end = text
            elif name == "instant":
                instant = text
            elif name in ("explicitmember", "typedmember"):
                axis = _attr(el, "dimension") or "?"
                member = text if name == "explicitmember" else "".join(el.itertext()).strip()
                dims.append(f"{axis}={member}")
        if instant:
            contexts[cid] = ("instant", None, instant, ";".join(sorted(dims)))
        elif end:
            contexts[cid] = ("duration", start, end, ";".join(sorted(dims)))

    # units: id -> measure string
    units: dict[str, str] = {}
    for u in root.iter():
        if not isinstance(u.tag, str) or _local(u.tag) != "unit":
            continue
        uid = _attr(u, "id")
        measures = [(m.text or "").strip() for m in u.iter() if isinstance(m.tag, str) and _local(m.tag) == "measure"]
        if uid:
            units[uid] = "/".join(measures) if measures else uid

    # DEI (document-level) facts
    cik = None
    dei: dict[str, str] = {}
    for el in root.iter():
        if not isinstance(el.tag, str) or _local(el.tag) != "nonnumeric":
            continue
        name = _attr(el, "name") or ""
        if name.startswith("dei:"):
            dei.setdefault(name, "".join(el.itertext()).strip())
    for ctx in root.iter():
        if isinstance(ctx.tag, str) and _local(ctx.tag) == "identifier" and (ctx.text or "").strip():
            cik = (ctx.text or "").strip()
            break

    fy_focus = None
    m = re.search(r"\d{4}", dei.get("dei:DocumentFiscalYearFocus", ""))
    if m:
        fy_focus = int(m.group(0))
    filing = Filing(
        ticker=ticker, cik=cik or "", fiscal_year_focus=fy_focus,
        period_end=None, document_type=dei.get("dei:DocumentType"),
        registrant=dei.get("dei:EntityRegistrantName"), source_file=source_file,
    )

    facts: list[Fact] = []
    seen: set[tuple] = set()
    for el in root.iter():
        if not isinstance(el.tag, str) or _local(el.tag) != "nonfraction":
            continue
        concept = _attr(el, "name")
        cref = _attr(el, "contextRef")
        if not concept or not cref or cref not in contexts:
            continue
        scale = int(_attr(el, "scale") or 0)
        value = _parse_number("".join(el.itertext()), _attr(el, "format"), scale, _attr(el, "sign"))
        if value is None:
            continue
        ptype, start, end, dims = contexts[cref]
        unit_id = _attr(el, "unitRef") or ""
        fact = Fact(
            ticker=ticker, cik=cik or "", concept=concept, value=value,
            raw_text="".join(el.itertext()).strip(), unit=units.get(unit_id, unit_id),
            decimals=_attr(el, "decimals"), scale=scale, period_type=ptype,
            period_start=start, period_end=end, days=_days(start, end),
            fiscal_year=int(end[:4]), dims=dims, dim_count=dims.count("=") if dims else 0,
            context_id=cref, fact_id=_attr(el, "id"), source_file=source_file,
        )
        if fact.key() in seen:
            continue
        seen.add(fact.key())
        facts.append(fact)

    # The filing's own period end: the DEI context (no dimensions, longest annual duration ending latest).
    annual = [f for f in facts if f.period_type == "duration" and f.days and f.days > 300 and not f.dims]
    if annual:
        filing.period_end = max(f.period_end for f in annual)
        derived_fy = int(filing.period_end[:4])
        if fy_focus is not None and derived_fy != fy_focus:
            filing.notes.append(f"DocumentFiscalYearFocus {fy_focus} != period-end year {derived_fy}")
    filing.n_facts = len(facts)
    return filing, facts


# ── SQLite ───────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    ticker TEXT PRIMARY KEY, cik TEXT, fiscal_year_focus INTEGER, period_end TEXT,
    document_type TEXT, registrant TEXT, source_file TEXT, n_facts INTEGER, notes TEXT
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, cik TEXT, concept TEXT NOT NULL,
    value REAL NOT NULL, raw_text TEXT, unit TEXT, decimals TEXT, scale INTEGER,
    period_type TEXT NOT NULL, period_start TEXT, period_end TEXT NOT NULL, days INTEGER,
    fiscal_year INTEGER NOT NULL, dims TEXT NOT NULL DEFAULT '', dim_count INTEGER NOT NULL DEFAULT 0,
    context_id TEXT, fact_id TEXT, source_file TEXT
);
CREATE INDEX IF NOT EXISTS facts_lookup ON facts (ticker, concept, fiscal_year);
CREATE INDEX IF NOT EXISTS facts_dims ON facts (ticker, dims);
"""


def build_facts_db(filings_dir: Path = FILINGS_DIR, db_path: Path = FACTS_DB) -> list[Filing]:
    """Parse every 10-K submission under *filings_dir* into *db_path* (rebuilt from scratch)."""
    paths = sorted(filings_dir.glob("*/10-K/*/full-submission.txt"))
    if not paths:
        raise FileNotFoundError(f"no full-submission.txt under {filings_dir}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    summary: list[Filing] = []
    for path in paths:
        ticker = path.parts[-4].upper()
        raw = path.read_text(encoding="utf-8", errors="replace")
        filing, facts = parse_inline_xbrl(primary_document(raw), ticker, str(path))
        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?,?)",
            (filing.ticker, filing.cik, filing.fiscal_year_focus, filing.period_end, filing.document_type,
             filing.registrant, filing.source_file, filing.n_facts, json.dumps(filing.notes)),
        )
        con.executemany(
            "INSERT INTO facts (ticker, cik, concept, value, raw_text, unit, decimals, scale, period_type, "
            "period_start, period_end, days, fiscal_year, dims, dim_count, context_id, fact_id, source_file) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(f.ticker, f.cik, f.concept, f.value, f.raw_text, f.unit, f.decimals, f.scale, f.period_type,
              f.period_start, f.period_end, f.days, f.fiscal_year, f.dims, f.dim_count, f.context_id,
              f.fact_id, f.source_file) for f in facts],
        )
        con.commit()
        logger.info("%s: %d facts, FY focus %s, period end %s%s", ticker, len(facts), filing.fiscal_year_focus,
                    filing.period_end, f"  NOTES: {filing.notes}" if filing.notes else "")
        summary.append(filing)
    con.close()
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    filings = build_facts_db()
    print(f"Built {FACTS_DB} with {sum(f.n_facts for f in filings)} facts from {len(filings)} filings:")
    for f in filings:
        print(f"  {f.ticker:<6} {f.registrant or '?':<28} FY{f.fiscal_year_focus} ends {f.period_end}  facts={f.n_facts}"
              + (f"  notes={f.notes}" if f.notes else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
