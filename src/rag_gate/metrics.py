"""检索指标：recall@k / hit_rate@k / mrr / ndcg@k，以及 similarity_drift。

一次检索结果（单个 query）统一成这个形状::

    {
        "ids":    ["doc_1", "doc_7", ...],   # 按相关性降序，必填
        "scores": [0.91, 0.72, ...],         # 相似度分数，可选；drift 需要它
    }

约定：指标只关心「期望文档有没有出现在前 k」和「排在第几」，
不关心返回了多少个（不会因为多返回而扣分，但也没有加分）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 对外错误
# ---------------------------------------------------------------------------


class MetricError(ValueError):
    """指标名写错、或传参不合法。"""


# ---------------------------------------------------------------------------
# 指标名解析
# ---------------------------------------------------------------------------

_BARE_METRICS = {"mrr", "hit_rate", "recall", "ndcg", "map", "precision"}


def parse_metric(name: str) -> Tuple[str, Optional[int]]:
    """把 ``recall@5`` 解析成 ``("recall", 5)``；``mrr`` → ``("mrr", None)``。

    别名会归一化：``hit``→``hit_rate``，``rr``→``mrr``。
    """
    if not isinstance(name, str) or not name.strip():
        raise MetricError("指标名必须是非空字符串，例如 recall@5 / hit_rate@5 / mrr")
    raw = name.strip().lower().replace(" ", "")
    body, _, at = raw.partition("@")
    alias = {
        "hit": "hit_rate",
        "hitrate": "hit_rate",
        "rr": "mrr",
        "mrr@k": "mrr",
        "ndcg@k": "ndcg",
        "recall@k": "recall",
        "hit_rate@k": "hit_rate",
        "precision@k": "precision",
        "map@k": "map",
    }
    if body in alias:
        body = alias[body]
    if body not in _BARE_METRICS:
        raise MetricError(
            f"不认识的指标 `{name}`。支持：recall@k / hit_rate@k / mrr / ndcg@k / map@k / precision@k"
        )
    if not at:
        if body in {"recall", "hit_rate", "ndcg", "precision", "map"}:
            raise MetricError(f"`{name}` 需要带 k，例如 `{body}@5`")
        return body, None
    if at == "k":
        raise MetricError(f"`{name}` 里的 k 要写具体数字，例如 `{body}@5`")
    if not at.isdigit() or int(at) <= 0:
        raise MetricError(f"`{name}` 的 k 必须是正整数，例如 `{body}@5`")
    k = int(at)
    if body in {"mrr"}:
        # mrr@5 合法：只看前 5 名里的第一个命中
        return body, k
    return body, k


def canonical(name: str) -> str:
    """规范化指标名，用于报告和基线的键。``recall@5`` 原样返回，``RR`` → ``mrr``。"""
    body, k = parse_metric(name)
    return body if k is None else f"{body}@{k}"


# ---------------------------------------------------------------------------
# 单条 query 的打分
# ---------------------------------------------------------------------------


def _dedup(ids: Sequence[str]) -> List[str]:
    seen = set()
    out = []
    for d in ids:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def score_one(
    retrieved: Sequence[str], expected: Sequence[str], k: Optional[int] = None
) -> Dict[str, float]:
    """算一条 query 的所有指标（不带 @k 的用整段结果）。"""
    got = _dedup([str(x) for x in retrieved])
    want = [str(x) for x in expected]
    top = got[:k] if k else got
    want_set = set(want)

    hits = [d for d in top if d in want_set]
    hit_set = set(hits)

    recall = (len(hit_set) / len(want)) if want else 0.0
    hit_rate = 1.0 if hit_set else 0.0

    rr = 0.0
    for rank, d in enumerate(top, start=1):
        if d in want_set:
            rr = 1.0 / rank
            break

    return {"recall": recall, "hit_rate": hit_rate, "mrr": rr, "ndcg": _ndcg(top, want_set)}


def _ndcg(top: Sequence[str], want_set: set) -> float:
    """二值相关性的 nDCG@k（每个期望文档相关度 1）。"""
    dcg = 0.0
    for rank, d in enumerate(top, start=1):
        if d in want_set:
            dcg += 1.0 / math.log2(rank + 1)
    ideal = 0.0
    for rank in range(1, min(len(want_set), len(top)) + 1):
        ideal += 1.0 / math.log2(rank + 1)
    if ideal == 0.0:
        return 0.0
    return min(dcg / ideal, 1.0)


# ---------------------------------------------------------------------------
# 整个数据集
# ---------------------------------------------------------------------------


def evaluate_all(
    rows: Iterable[Dict[str, Any]],
    results: Dict[str, Any],
    metric_names: Sequence[str],
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    """对整个数据集求均值。

    Args:
        rows: Golden set，每项含 ``id`` / ``expected_doc_ids``。
        results: ``{query_id: {"ids": [...], "scores": [...]}}``。
        metric_names: 例如 ``["recall@5", "mrr"]``。

    Returns:
        ``(scores, per_query)``；``scores`` 的键是规范化后的指标名。
    """
    parsed = [(m, *parse_metric(m)) for m in metric_names]
    acc: Dict[str, List[float]] = {canonical(m): [] for m in metric_names}
    per_query: List[Dict[str, Any]] = []

    for row in rows:
        qid = str(row.get("id"))
        expected = row.get("expected_doc_ids") or []
        result = results.get(qid) or {}
        ids = result.get("ids") or []
        if not ids and result.get("error"):
            raise MetricError(f"query `{qid}` 取数失败：{result['error']}")

        per_query.append(
            {
                "id": qid,
                "question": row.get("question", ""),
                "expected_doc_ids": [str(x) for x in expected],
                "retrieved": [str(x) for x in _dedup(ids)],
                "error": result.get("error"),
            }
        )
        for raw_name, body, k in parsed:
            value = score_one(ids, expected, k)[body]
            acc[canonical(raw_name)].append(value)

    scores = {
        name: (sum(vals) / len(vals) if vals else 0.0) for name, vals in acc.items()
    }
    return scores, per_query


# ---------------------------------------------------------------------------
# similarity_drift：抓「向量空间变了」
# ---------------------------------------------------------------------------


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def score_distribution(top_scores: Sequence[float]) -> Dict[str, float]:
    """top1 相似度的分布摘要（均值/中位数/分位数）。"""
    vals = sorted(float(s) for s in top_scores)
    if not vals:
        return {}
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return {
        "n": len(vals),
        "mean": mean,
        "std": math.sqrt(var),
        "min": vals[0],
        "p10": _percentile(vals, 0.10),
        "p50": _percentile(vals, 0.50),
        "p90": _percentile(vals, 0.90),
        "max": vals[-1],
    }


def collect_top_scores(rows: Iterable[Dict[str, Any]], results: Dict[str, Any]) -> List[float]:
    """取每个 query 的 top1 分数；没有分数的 query 直接跳过。"""
    out: List[float] = []
    for row in rows:
        result = results.get(str(row.get("id"))) or {}
        scores = result.get("scores") or []
        if scores:
            try:
                out.append(float(scores[0]))
            except (TypeError, ValueError):
                continue
    return out


DEFAULT_DRIFT_THRESHOLDS = {"mean_drop": 0.02, "p50_drop": 0.02}


def compare_distributions(
    before: Dict[str, float],
    after: Dict[str, float],
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """比两次的 top1 分布，判断有没有漂移。

    ``mean_drop`` / ``p50_drop`` 是允许的下移幅度；``std`` 变化只看倍数
    （``std_ratio`` 默认 1.5，即波动放大 50% 就报警）。
    """
    th = dict(DEFAULT_DRIFT_THRESHOLDS)
    th.update(thresholds or {})
    if not before or not after:
        return {"drifted": False, "reason": "缺少相似度分数（adapter 没回 scores），跳过 drift", "deltas": {}}

    deltas = {
        key: float(after.get(key, 0.0)) - float(before.get(key, 0.0))
        for key in ("mean", "std", "p10", "p50", "p90")
        if key in before and key in after
    }
    drop_mean = -deltas.get("mean", 0.0)
    drop_p50 = -deltas.get("p50", 0.0)
    std_ratio = (
        float(after["std"]) / float(before["std"])
        if before.get("std") and float(before["std"]) > 0
        else 1.0
    )

    worst = max(drop_mean, drop_p50)
    drifted = drop_mean > th["mean_drop"] or drop_p50 > th["p50_drop"] or std_ratio > th.get(
        "std_ratio", 1.5
    )
    if not drifted:
        reason = "分布稳定"
    elif max(drop_mean, drop_p50) >= max(th["mean_drop"], th["p50_drop"]) * 3:
        reason = "分布整体大幅下移：很像写入侧/读取侧的 Embedding 不一致"
    else:
        reason = "top1 相似度分布下移，检索质量正在悄悄变化"

    return {
        "drifted": drifted,
        "reason": reason,
        "worst_drop": worst,
        "std_ratio": std_ratio,
        "thresholds": th,
        "deltas": deltas,
    }
