# tests/test_ablation.py
#
# eval/ablation.py turns retrieval results files into the ablation matrix the
# docs cite. Pinned here: the one-phrase description of a configuration (so a
# row is readable without opening the file), that only retrieval runs with
# aggregates are included, and that sorting is by the requested metric.

import json

from eval import ablation


def _run(label, rc, hit5, table_hit5=0.4, p50=20.0):
    return {
        "result_kind": "retrieval", "label": label,
        "config": {"retrieval": rc, "config_hash": "abc"},
        "aggregates": {
            "overall": {"hit@5": hit5, "recall@5": hit5, "mrr": 0.3, "ndcg@5": 0.3, "recall@10": 0.5, "recall@25": 0.7},
            "by_requires_table": {"True": {"hit@5": table_hit5}},
            "latency_ms": {"p50": p50, "p95": 30.0},
        },
        "results": [],
    }


def test_describe_configurations():
    assert ablation.describe({"mode": "dense"}) == "dense"
    assert ablation.describe({"mode": "hybrid", "rrf_k": 20}) == "hybrid, rrf_k=20"
    assert ablation.describe({"mode": "hybrid", "sparse_weight": 2.0}) == "hybrid, w=1.0/2.0"
    assert ablation.describe({"mode": "hybrid", "rerank": True, "fetch_k": 50}) == "hybrid, rerank fetch=50"
    assert ablation.describe({"mode": "dense", "ticker_filter": "inferred"}) == "dense, ticker=inferred"
    assert ablation.describe({"mode": "bm25", "fetch_k": 50}) == "bm25, fetch=50"


def test_load_and_render_sorted(tmp_path):
    (tmp_path / "retrieval-a.json").write_text(json.dumps(_run("a", {"mode": "dense"}, 0.5)), encoding="utf-8")
    (tmp_path / "retrieval-b.json").write_text(json.dumps(_run("b", {"mode": "hybrid", "rerank": True, "fetch_k": 50}, 0.7)), encoding="utf-8")
    (tmp_path / "retrieval-partial.json").write_text(json.dumps({"result_kind": "retrieval", "aggregates": None}), encoding="utf-8")
    (tmp_path / "baseline-x.json").write_text(json.dumps({"result_kind": "generation", "aggregates": {}}), encoding="utf-8")
    runs = ablation.load_retrieval_runs(tmp_path)
    assert [r["label"] for r in runs] == ["a", "b"]
    text = ablation.render(runs, sort_key="hit@5")
    lines = text.splitlines()
    assert lines[0].startswith("| configuration | hit@5 |")
    assert "hybrid, rerank fetch=50" in lines[2] and "0.700" in lines[2]   # best first
    assert "| dense |" in lines[3] and "[a](retrieval-a.json)" in lines[3]
