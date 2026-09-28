# eval/retrieval_metrics.py
#
# PURPOSE
# -------
# Rank-based retrieval metrics computed from chunk ids alone.  No model, no
# judge, no network: given the ranked list a retriever returned and the labelled
# relevant chunks for the question, every function here is pure arithmetic.
# This is the instrument that lets two retrieval configurations be told apart,
# which ROADMAP.md item 1 identified as the blocking gap.
#
# RELEVANCE IS GROUPED, NOT FLAT
# ------------------------------
# A benchmark item's reference passage may sit in more than one chunk — the
# chunker's 50-character overlap copies text into a neighbour, and a table row
# such as "AWS 90,757 107,556 128,725" can appear in several tables.  Any one of
# those chunks satisfies the reference.  So the labels for an item are a list
# of GROUPS, one per reference passage, and a group counts as retrieved when any
# of its members is.  Recall is measured over groups: an item with two
# reference passages has recall 0.5 when the retriever found one of them.
# Scoring flat ids instead would punish a retriever for returning one of two
# identical-overlap neighbours instead of both, which is not a retrieval error.

import math
from collections.abc import Iterable, Sequence

DEFAULT_KS = (5, 10, 25)


def _first_hit_rank(ranked: Sequence[str], group: Iterable[str]) -> int | None:
    """1-based rank at which any member of *group* first appears, else None."""
    members = set(group)
    for rank, cid in enumerate(ranked, start=1):
        if cid in members:
            return rank
    return None


def group_hit_ranks(ranked: Sequence[str], relevant_groups: Sequence[Iterable[str]]) -> list[int | None]:
    """First-hit rank for every group, in label order."""
    return [_first_hit_rank(ranked, g) for g in relevant_groups]


def hit_at_k(ranks: Sequence[int | None], k: int) -> float:
    """1.0 if ANY reference group is satisfied within the top *k*."""
    return 1.0 if any(r is not None and r <= k for r in ranks) else 0.0


def recall_at_k(ranks: Sequence[int | None], k: int) -> float:
    """Fraction of reference groups satisfied within the top *k*."""
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def reciprocal_rank(ranks: Sequence[int | None]) -> float:
    """1 / rank of the first satisfied group; 0 when none is retrieved."""
    hits = [r for r in ranks if r is not None]
    return 1.0 / min(hits) if hits else 0.0


def ndcg_at_k(ranks: Sequence[int | None], k: int) -> float:
    """Binary-gain nDCG@k over reference groups.

    Each group contributes a gain of 1 at the rank where it is first satisfied.
    The ideal ordering places all groups at ranks 1..min(n_groups, k).
    """
    if not ranks:
        return 0.0
    dcg = sum(1.0 / math.log2(r + 1) for r in ranks if r is not None and r <= k)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(ranks), k) + 1))
    return dcg / ideal if ideal else 0.0


def item_metrics(
    ranked: Sequence[str],
    relevant_groups: Sequence[Iterable[str]],
    ks: Sequence[int] = DEFAULT_KS,
) -> dict:
    """All metrics for one item.  Keys are stable strings so they aggregate."""
    ranks = group_hit_ranks(ranked, relevant_groups)
    out: dict = {
        "n_groups": len(relevant_groups),
        "n_retrieved": len(ranked),
        "first_hit_rank": min((r for r in ranks if r is not None), default=None),
        "mrr": round(reciprocal_rank(ranks), 4),
        "ndcg@5": round(ndcg_at_k(ranks, 5), 4),
    }
    for k in ks:
        out[f"hit@{k}"] = hit_at_k(ranks, k)
        out[f"recall@{k}"] = round(recall_at_k(ranks, k), 4)
    return out


METRIC_KEYS = ("mrr", "ndcg@5") + tuple(
    f"{name}@{k}" for k in DEFAULT_KS for name in ("hit", "recall")
)


def aggregate(items: Sequence[dict], keys: Sequence[str] = METRIC_KEYS) -> dict:
    """Mean of each metric over *items*, with n.  Empty input yields n=0 and no means."""
    block: dict = {"n_items": len(items)}
    for key in keys:
        vals = [float(it[key]) for it in items if it.get(key) is not None]
        block[key] = round(sum(vals) / len(vals), 4) if vals else None
    return block
