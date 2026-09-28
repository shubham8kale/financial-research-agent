# eval/figure_match.py
#
# PURPOSE
# -------
# A deterministic check that the figures in the ground truth appear in the
# answer.  No judge model is involved, so it costs nothing and never varies
# between runs.
#
# WHY IT EXISTS
# -------------
# eval/EVALUATION.md finding 2: on the temporal stratum the agent quoted the
# PRIOR year's figure on four of five items and RAGAS faithfulness scored three
# of them a perfect 1.00, because the prior-year figure genuinely is in the
# retrieved context.  Faithfulness asks "is this grounded?"; nothing asked "is
# this the number the question was about?".  This metric asks exactly that, by
# comparing against the ground truth rather than against the retrieved context.
#
# WHAT COUNTS AS A MATCH
# ----------------------
# The figure grammar and the matcher live in agent/figures.py, shared with the
# output contract's verifier (agent/contract.py) so that a score here and a
# verification there agree on what a number is.  In short: same kind (percent
# vs plain number), scaled or unscaled values within 0.05%, or the answer's
# figure rounds to the ground truth's precision; bare integers (years) must
# match exactly.
#
# This is a "right figure" check, not a "verbatim" check: an answer that
# rounds $416,161 million to $416.2 billion passes here.  The verbatim check
# is the output contract's verifier, which compares against the cited
# observation rather than the ground truth.

from agent.figures import (  # noqa: F401 — re-exported for the tests and the harness
    FIGURE_MATCH_VERSION, RELATIVE_TOLERANCE, Figure, _close, _exact_only, _rounds_to,
    dedupe as _dedupe, extract_figures, matches as _matches, primary_figure,
)


def figure_match(ground_truth: str, answer: str) -> dict:
    """Score how many ground-truth figures the answer reproduces.

    Returns a dict with:
      applicable      False when the ground truth contains no figure — the
                      metric then says nothing about the item and every score
                      is None, never 1.0.
      n_expected      distinct figures in the ground truth
      n_found         of those, how many the answer contains
      figure_recall   n_found / n_expected
      figure_exact    True only if every expected figure was found (strict:
                      context figures in the ground truth count too)
      primary         the figure the question is about, as written
      figure_primary  True if the answer contains that figure
      missing         the expected figures the answer lacks, as written
    """
    expected = _dedupe(extract_figures(ground_truth))
    if not expected:
        return {
            "applicable": False, "n_expected": 0, "n_found": 0,
            "figure_recall": None, "figure_exact": None,
            "primary": None, "figure_primary": None, "missing": [],
        }
    found = extract_figures(answer)
    missing = [e for e in expected if not any(_matches(e, f) for f in found)]
    n_found = len(expected) - len(missing)
    primary = primary_figure(expected)
    return {
        "applicable": True,
        "n_expected": len(expected),
        "n_found": n_found,
        "figure_recall": round(n_found / len(expected), 4),
        "figure_exact": not missing,
        "primary": primary.raw if primary else None,
        "figure_primary": bool(primary) and any(_matches(primary, f) for f in found),
        "missing": [m.raw for m in missing],
    }
