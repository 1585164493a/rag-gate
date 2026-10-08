"""门禁判定 + 退码。

退码约定（CI 靠这个，三种必须分开）：

===== ==========================================
退码    含义
===== ==========================================
0     通过
1     检索质量退化（本次结果比基线差太多）
2     配置或取数错误（压根没跑起来）
===== ==========================================
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_ERROR = 2

EXIT_MEANING = {
    EXIT_OK: "通过",
    EXIT_REGRESSION: "检索质量退化",
    EXIT_ERROR: "配置或取数错误",
}


class GateResult:
    def __init__(self, exit_code: int, failures: Optional[List[str]] = None,
                 warnings: Optional[List[str]] = None):
        self.exit_code = exit_code
        self.failures = failures or []
        self.warnings = warnings or []

    @property
    def passed(self) -> bool:
        return self.exit_code == EXIT_OK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "meaning": EXIT_MEANING.get(self.exit_code, ""),
            "failures": self.failures,
            "warnings": self.warnings,
        }


def verify(
    comparison: Dict[str, Any],
    *,
    drift: Optional[Dict[str, Any]] = None,
    fail_on_drift: bool = False,
    dataset_changed: bool = False,
    fail_on_dataset_change: bool = False,
    only_metrics: Optional[List[str]] = None,
) -> GateResult:
    """把对比结果 + drift 判成一条结论。

    ``only_metrics`` 用来在 PR 里临时放行某些指标（对应 CLI 的 --skip）。
    """
    failures: List[str] = []
    warnings: List[str] = []
    skipped = {m.strip() for m in (only_metrics or []) if m.strip()}

    for row in comparison.get("violations") or []:
        if row.get("metric") in skipped:
            warnings.append(f"`{row['metric']}` 已按 --skip 放行（本次跌幅 {row.get('drop', 0):.4f}）")
            continue
        for reason in row.get("reasons") or []:
            failures.append(f"`{row['metric']}`：{reason}")

    for row in comparison.get("rows") or []:
        if row.get("status") == "missing":
            warnings.append(f"`{row['metric']}` 基线里有、本次没跑，没法判定")

    if dataset_changed:
        message = (
            "golden set 变了（dataset_digest 对不上基线）—— 新旧分数不可直接比较。\n"
            "  确认评测集确实该改，就跑一次 `rag-gate baseline` 把基线刷新，并一起提交。"
        )
        if fail_on_dataset_change:
            failures.append(message)
        else:
            warnings.append(message)

    if drift and drift.get("drifted"):
        message = f"相似度分布漂移：{drift.get('reason')}"
        if fail_on_drift:
            failures.append(message)
        else:
            warnings.append(message)

    if failures:
        return GateResult(EXIT_REGRESSION, failures, warnings)
    return GateResult(EXIT_OK, failures, warnings)
