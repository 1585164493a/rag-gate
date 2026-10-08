"""基线的读写与对比。

基线就是一坨 JSON（进版本控制），内容是「上一次被认可的检索结果快照」。
对比时**只信指标键**，所以以后加新指标不会让老基线失效。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from rag_gate import __version__

BASELINE_FORMAT = 1


class BaselineError(Exception):
    """基线文件读不了 / 版本不认识。"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def dataset_digest(rows: List[Dict[str, Any]]) -> str:
    """给 golden set 算指纹：它变了，历史对比就失去意义（只警告，不拦）。"""
    payload = json.dumps(
        [{"id": str(r.get("id")), "q": r.get("question"), "e": r.get("expected_doc_ids")} for r in rows],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_baseline(
    metrics: Dict[str, float],
    per_query: List[Dict[str, Any]],
    digest: str,
    notes: Optional[str] = None,
    drift: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "format": BASELINE_FORMAT,
        "rag_gate_version": __version__,
        "created_at": _now(),
        "dataset_digest": digest,
        "metrics": {k: round(float(v), 6) for k, v in sorted(metrics.items())},
        "drift": drift or {},
        "notes": notes or "",
        "per_query": per_query,
    }


def save_baseline(path: str, baseline: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(baseline, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_baseline(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise BaselineError(
            f"找不到基线文件：{path}\n"
            "  第一次用先跑一次：rag-gate baseline\n"
            "  然后把生成的 baseline.json 提交进仓库（这是历史对比的唯一依据）"
        )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"基线文件不是合法 JSON：{path}（{exc.msg}）") from exc
    if not isinstance(data, dict) or "metrics" not in data:
        raise BaselineError(f"基线文件结构不对（缺少 metrics 字段）：{path}")
    fmt = data.get("format", 0)
    if fmt > BASELINE_FORMAT:
        raise BaselineError(
            f"基线是更新版本（format {fmt}）写的，当前 rag-gate 只认到 {BASELINE_FORMAT}。\n"
            "  升级：pip install -U rag-gate"
        )
    return data


def environment() -> Dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


# ---------------------------------------------------------------------------
# 对比
# ---------------------------------------------------------------------------


def compare(
    metrics: Dict[str, float],
    baseline: Dict[str, Any],
    max_drop: Dict[str, float],
    min_absolute: Dict[str, float],
) -> Dict[str, Any]:
    """核心：和基线比，算跌幅，判定是否越线。

    返回 ``{"rows": [...], "violations": [...], "worst_drop": float}``。
    ``rows`` 的每一项都能直接进报告表。
    """
    base_metrics = {str(k): float(v) for k, v in (baseline.get("metrics") or {}).items()}
    rows: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []

    all_names = list(dict.fromkeys([*metrics.keys(), *base_metrics.keys()]))
    for name in all_names:
        current = metrics.get(name)
        base_value = base_metrics.get(name)
        row: Dict[str, Any] = {
            "metric": name,
            "baseline": base_value,
            "current": current,
            "delta": None,
            "drop": None,
            "max_drop": max_drop.get(name),
            "min_absolute": min_absolute.get(name),
            "status": "ok",
            "reasons": [],
        }
        if base_value is None:
            row["status"] = "new"
            row["reasons"].append("基线里没有这个指标，本次只记录不判定")
        elif current is None:
            row["status"] = "missing"
            row["reasons"].append("基线里有、本次没跑这个指标")
        else:
            delta = current - base_value
            drop = -delta
            row["delta"] = delta
            row["drop"] = drop
            threshold = max_drop.get(name)
            if threshold is not None and drop > threshold + 1e-12:
                row["status"] = "fail"
                row["reasons"].append(f"相对基线跌了 {drop:.4f}，超过允许的 {threshold:g}")
            floor_value = min_absolute.get(name)
            if floor_value is not None and current < floor_value - 1e-12:
                row["status"] = "fail"
                row["reasons"].append(f"低于绝对下限 {floor_value:g}")
        if row["status"] == "fail":
            violations.append(row)
        rows.append(row)

    drops = [r["drop"] for r in rows if isinstance(r.get("drop"), (int, float))]
    return {
        "rows": rows,
        "violations": violations,
        "worst_drop": max(drops) if drops else 0.0,
        "baseline_created_at": baseline.get("created_at"),
        "baseline_version": baseline.get("rag_gate_version"),
    }
