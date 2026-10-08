"""指标算得对不对 —— 这是整个工具的地基。"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rag_gate.metrics import (  # noqa: E402
    MetricError,
    canonical,
    collect_top_scores,
    compare_distributions,
    evaluate_all,
    parse_metric,
    score_distribution,
    score_one,
)


def test_parse_metric_ok():
    assert parse_metric("recall@5") == ("recall", 5)
    assert parse_metric("hit_rate@10") == ("hit_rate", 10)
    assert parse_metric("mrr") == ("mrr", None)
    assert parse_metric("NDCG@3") == ("ndcg", 3)
    assert parse_metric("hit@5") == ("hit_rate", 5)
    assert canonical("RR") == "mrr"
    assert canonical("Recall@5") == "recall@5"


@pytest.mark.parametrize("bad", ["", "recall", "hit_rate", "recall@k", "recall@0", "f1@5", "recall@-1"])
def test_parse_metric_bad(bad):
    with pytest.raises(MetricError):
        parse_metric(bad)


def test_recall_counts_only_expected_docs_in_top_k():
    # 期望 2 个，前 5 里命中 1 个 -> recall=0.5
    assert score_one(["a", "x", "y", "z", "w"], ["a", "b"], k=5)["recall"] == 0.5
    # 命中 2 个 -> 1.0
    assert score_one(["a", "b", "x"], ["a", "b"], k=5)["recall"] == 1.0
    # 命中但在第 6 名之外 -> 0
    assert score_one(["x", "y", "z", "w", "v", "a"], ["a"], k=5)["recall"] == 0.0


def test_hit_rate_is_binary_per_query():
    assert score_one(["a"], ["a"], k=5)["hit_rate"] == 1.0
    assert score_one(["b"], ["a"], k=5)["hit_rate"] == 0.0


def test_mrr_uses_first_hit_rank():
    assert score_one(["a"], ["a"], k=5)["mrr"] == 1.0
    assert score_one(["x", "a"], ["a"], k=5)["mrr"] == 0.5
    assert score_one(["x", "y", "z", "a"], ["a"], k=5)["mrr"] == pytest.approx(0.25)
    # k 之外的不算
    assert score_one(["x", "y", "z", "w", "v", "a"], ["a"], k=5)["mrr"] == 0.0


def test_ndcg_perfect_ranking_is_one():
    value = score_one(["a", "b"], ["a", "b"], k=5)["ndcg"]
    assert value == pytest.approx(1.0)


def test_ndcg_penalises_missing_hits():
    perfect = score_one(["a", "b"], ["a", "b"], k=5)["ndcg"]
    partial = score_one(["a", "x"], ["a", "b"], k=5)["ndcg"]
    assert partial < perfect
    ideal = 1.0 + 1.0 / math.log2(3)
    assert partial == pytest.approx((1.0 / math.log2(2)) / ideal)


def test_ndcg_penalises_hits_pushed_below_k():
    """命中被挤到 k 之外，nDCG 必须掉。"""
    early = score_one(["a", "b"], ["a", "b"], k=5)["ndcg"]
    late = score_one(["x", "y", "z", "w", "v", "a", "b"], ["a", "b"], k=5)["ndcg"]
    assert late == 0.0
    assert late < early


def test_ndcg_ignores_hits_beyond_expected_count():
    """多返回不相关文档不该让 nDCG 超过 1。"""
    value = score_one(["a", "x", "y", "z"], ["a"], k=5)["ndcg"]
    assert value == pytest.approx(1.0)


def test_duplicate_retrieved_ids_do_not_double_count():
    assert score_one(["a", "a", "b"], ["a", "b"], k=5)["recall"] == 1.0


def test_evaluate_all_averages_and_keeps_per_query():
    rows = [
        {"id": "q1", "question": "一", "expected_doc_ids": ["a"]},
        {"id": "q2", "question": "二", "expected_doc_ids": ["b"]},
    ]
    results = {"q1": {"ids": ["a", "x"]}, "q2": {"ids": ["x", "y"]}}
    scores, per_query = evaluate_all(rows, results, ["recall@5", "hit_rate@5", "mrr"])
    assert scores["recall@5"] == pytest.approx(0.5)
    assert scores["hit_rate@5"] == pytest.approx(0.5)
    assert scores["mrr"] == pytest.approx(0.5)
    assert [row["id"] for row in per_query] == ["q1", "q2"]
    assert per_query[0]["retrieved"] == ["a", "x"]


def test_evaluate_all_reports_adapter_error():
    rows = [{"id": "q1", "question": "一", "expected_doc_ids": ["a"]}]
    with pytest.raises(MetricError):
        evaluate_all(rows, {"q1": {"ids": [], "error": "连不上"}}, ["recall@5"])


def test_score_distribution_summary():
    dist = score_distribution([0.1, 0.2, 0.3, 0.4, 0.5])
    assert dist["n"] == 5
    assert dist["mean"] == pytest.approx(0.3)
    assert dist["p50"] == pytest.approx(0.3)
    assert dist["p10"] == pytest.approx(0.14)
    assert score_distribution([]) == {}


def test_collect_top_scores_skips_missing():
    rows = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
    results = {"q1": {"scores": [0.9]}, "q2": {"scores": []}, "q3": {"scores": [0.5, 0.4]}}
    assert collect_top_scores(rows, results) == [0.9, 0.5]


def test_compare_distributions_detects_drop():
    before = {"mean": 0.90, "p50": 0.90, "std": 0.01}
    after = {"mean": 0.60, "p50": 0.60, "std": 0.01}
    verdict = compare_distributions(before, after)
    assert verdict["drifted"] is True
    assert verdict["worst_drop"] == pytest.approx(0.30)
    assert "Embedding" in verdict["reason"]


def test_compare_distributions_stable():
    before = {"mean": 0.90, "p50": 0.90, "std": 0.05}
    after = {"mean": 0.895, "p50": 0.895, "std": 0.05}
    assert compare_distributions(before, after)["drifted"] is False


def test_compare_distributions_flags_volatility():
    before = {"mean": 0.90, "p50": 0.90, "std": 0.01}
    after = {"mean": 0.90, "p50": 0.90, "std": 0.05}
    assert compare_distributions(before, after)["drifted"] is True


def test_compare_distributions_without_scores():
    verdict = compare_distributions({}, {"mean": 0.5})
    assert verdict["drifted"] is False
    assert "缺少相似度分数" in verdict["reason"]
