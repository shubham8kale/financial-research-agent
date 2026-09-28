# agent/figures.py
#
# PURPOSE
# -------
# The one definition of "a figure" and of "the same figure" that the whole
# repository uses: the evaluation's figure check (eval/figure_match.py) scores
# an answer against the ground truth with it, and the output contract
# (agent/contract.py) verifies an answer against the passages it cites with
# it.  Two matchers would drift; a score and a verification must agree on
# what a number is.
#
# WHAT COUNTS AS A MATCH
# ----------------------
# Every numeric token is extracted with its unit context: a scale word
# (million, billion, ...) and a percent marker.  Two figures match if they
# are the same kind (percent vs plain number) and EITHER the scaled values
# agree ("$1.2 billion" == "$1,200 million") OR the unscaled digits agree
# ("$128,725 million" == "128,725" with the unit omitted), to within 0.05%
# relative — enough to absorb billions-to-millions rounding, far too tight
# to let a different fiscal year's figure through — OR the candidate,
# rounded to the precision the expected figure was written in, equals it
# ("$26.4 billion" expected, "$26,448 million" candidate).  Bare integers
# (years, counts) must match exactly: a relative tolerance applied to a year
# would let 2024 pass for 2025, precisely the error eval/EVALUATION.md
# finding 2 is about.
#
# The direction matters.  matches(expected, candidate) lets the CANDIDATE be
# more precise than the EXPECTED figure.  The figure check calls it with the
# ground truth as expected (an answer may be more exact than the label); the
# verifier calls it with the claim as expected (a claim may round its source
# but may not be more precise than it).

import math
import re
from dataclasses import dataclass

_SCALE = {
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
    "mm": 1e6,
    "bn": 1e9,
}

# One numeric token with optional currency, thousands separators, decimals,
# percent marker and scale word.  Thousands groups require exactly three digits
# with no whitespace after the comma, so "September 27, 2025" yields 27 and
# 2025 rather than a single bogus number.
_FIGURE_RE = re.compile(
    r"\$?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(?P<pct>%|percent(?:age points?)?))?"
    r"(?:\s*(?P<scale>thousand|million|billion|trillion|mm|bn)\b)?",
    re.IGNORECASE,
)

RELATIVE_TOLERANCE = 5e-4

# Bumped whenever extraction or matching changes; recorded in results files so a
# figure score can always be traced to the rule that produced it.
#   1  first version; read the "10" in "10-K" as a figure
#   2  form/item numbers and month days are not figures
#   3  primary figure (the first non-year figure) and precision-aware rounding
#   4  a ground truth whose only figures are years is NOT applicable: a year is
#      not an answer a figure check can credit, and treating it as the primary
#      figure padded the primary-figure rate with five items and let a
#      question whose ground truth held no figure at all count as a pass
FIGURE_MATCH_VERSION = "4"

# A number that names a form or a section is not a figure: "10-K", "8-K",
# "Item 7A", "Form 10-Q", "Section 13".  Without this rule a ground truth that
# says "the 10-K does not disclose ..." demands the figure 10 of the answer.
_FORM_AFTER_RE = re.compile(r"^(?:-[A-Za-z]|[A-Z]\b)")
_FORM_BEFORE_RE = re.compile(r"(?:\bitem|\bform|\bsection|\bnote)\s*$", re.IGNORECASE)

# A number directly after a month name is a day of the month ("September 27,
# 2025" -> 27), not a figure.  Skipped when it is small enough to be a day and
# carries no scale or percent marker; the year that follows is kept.
_MONTH_BEFORE_RE = re.compile(
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*$", re.IGNORECASE,
)


@dataclass(frozen=True)
class Figure:
    raw: str          # the token as written, e.g. "$128,725 million"
    unscaled: float   # 128725.0
    scaled: float     # 1.28725e11
    percent: bool     # True for "22%"
    precision: float = 1.0  # the unit the figure was written in: 1e8 for "$26.4 billion", 1.0 for "14%"

    @property
    def kind(self) -> str:
        return "percent" if self.percent else "number"

    @property
    def is_year(self) -> bool:
        return (not self.percent and self.scaled == self.unscaled
                and float(self.unscaled).is_integer() and 1900 <= self.unscaled <= 2100)


def extract_figures(text: str) -> list[Figure]:
    """Every numeric token in *text*, in order of appearance."""
    figures: list[Figure] = []
    for m in _FIGURE_RE.finditer(text or ""):
        unscaled = float(m.group("num").replace(",", ""))
        scale = (m.group("scale") or "").lower()
        if (
            not scale and not m.group("pct") and unscaled <= 31
            and _MONTH_BEFORE_RE.search(text[max(0, m.start() - 12):m.start()])
        ):
            continue
        if not scale and not m.group("pct") and (
            _FORM_AFTER_RE.match(text[m.end():m.end() + 2])
            or _FORM_BEFORE_RE.search(text[max(0, m.start() - 10):m.start()])
        ):
            continue
        scaled = unscaled * _SCALE.get(scale, 1.0)
        decimals = len(m.group("num").split(".")[1]) if "." in m.group("num") else 0
        figures.append(Figure(
            raw=m.group(0).strip(),
            unscaled=unscaled,
            scaled=scaled,
            percent=bool(m.group("pct")),
            precision=_SCALE.get(scale, 1.0) / (10 ** decimals),
        ))
    return figures


def content_figures(text: str) -> list[Figure]:
    """The figures a claim must support: every figure except a bare year."""
    return [f for f in extract_figures(text) if not f.is_year]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= RELATIVE_TOLERANCE * max(abs(a), abs(b), 1e-12)


def _exact_only(f: Figure) -> bool:
    """A bare integer with no scale word: a year, a count, a rank.

    Such figures must match exactly.  The relative tolerance exists for scaled
    money ($128.7 billion vs $128,725 million); applied to a year it is about
    1.0, which would let 2024 pass for 2025 — precisely the error finding 2 is
    about.
    """
    return f.scaled == f.unscaled and float(f.unscaled).is_integer()


def _rounds_to(expected: Figure, candidate: Figure) -> bool:
    """The candidate, rounded to the precision the expected figure was written in, equals it.

    A ground truth says "14%" or "$26.4 billion" because the filing's prose
    rounds; an answer that computes 13.51% from the tagged figures, or reads
    $26,448 million off the table, is more precise, not wrong.  Years never
    get here (they are exact-only), so 2024 cannot round to 2025.
    """
    p = expected.precision
    return math.isclose(round(candidate.scaled / p) * p, expected.scaled, rel_tol=1e-9, abs_tol=p * 1e-9)


def matches(expected: Figure, candidate: Figure) -> bool:
    """True if *candidate* states the figure *expected* does, possibly more precisely."""
    if expected.kind != candidate.kind:
        return False
    if _exact_only(expected) and _exact_only(candidate):
        return expected.unscaled == candidate.unscaled
    return (_close(expected.scaled, candidate.scaled) or _close(expected.unscaled, candidate.unscaled)
            or _rounds_to(expected, candidate))


def dedupe(figures: list[Figure]) -> list[Figure]:
    out: list[Figure] = []
    for f in figures:
        if not any(matches(f, g) for g in out):
            out.append(f)
    return out


def primary_figure(figures: list[Figure]) -> Figure | None:
    """The figure the question is about: the first non-year figure, or None when there is none.

    Ground truths in this benchmark lead with the answer and add context
    after it — "$106,265 million for fiscal year 2025 (fiscal 2024: $87,464
    million)".  The strict check demands every figure, including the context;
    this picks out the one that answers the question.  A year alone is never
    a primary figure: an answer that names the right year has not produced a
    number, so such an item is not applicable to the figure check (version 4).
    """
    for f in figures:
        if not f.is_year:
            return f
    return None
