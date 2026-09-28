# retrieval/tickers.py
#
# PURPOSE
# -------
# Infer which indexed company a question is about, from the words in it.
# "What was Amazon's AWS revenue?" names one company; "Compare Apple and
# Microsoft" names two; "Which company had the highest net income?" names
# none.  A single inferred ticker becomes a metadata filter on retrieval,
# which keeps the 32,886 Microsoft chunks out of an Apple question.  Two or
# more, or none, means no filter — the retriever must not guess.
#
# This is deliberately a word list, not a model: five companies, a handful of
# aliases, and a result that is easy to read in a results file.

import re

# Ticker -> words that name that company in ordinary questions.  Bare ticker
# symbols are matched too (case-insensitively, as whole words).
ALIASES: dict[str, tuple[str, ...]] = {
    "AAPL": ("apple",),
    "MSFT": ("microsoft",),
    "GOOGL": ("alphabet", "google"),
    "AMZN": ("amazon", "aws"),
    "META": ("meta", "facebook", "instagram"),
}

_PATTERNS = {
    ticker: re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in (ticker.lower(), *words)) + r")\b",
        re.IGNORECASE,
    )
    for ticker, words in ALIASES.items()
}


def infer_tickers(text: str) -> list[str]:
    """Every indexed ticker the text names, in ALIASES order."""
    return [ticker for ticker, pattern in _PATTERNS.items() if pattern.search(text or "")]


def infer_single_ticker(text: str) -> str | None:
    """The one ticker the text names, or None when it names zero or several."""
    found = infer_tickers(text)
    return found[0] if len(found) == 1 else None
