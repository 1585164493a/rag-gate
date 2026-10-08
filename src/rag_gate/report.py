"""报告渲染：table（终端）/ markdown（贴 PR 评论）/ json（喂给别的工具）。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from rag_gate import color

EXIT_CODES = {
    0: "通过",
    1: "检索质量退化",
    2: "配置或取数错误",
}

_TABLE_HEAD = ("指标", "基线", "本次", "变化", "允许跌幅", "判定")

_ANSI = re.compile(r"\033\[[0-9;]*m")


def _fmt(value: Optional[float], digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _signed(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.4f}"


def _status_text(row: Dict[str, Any]) -> str:
    status = row.get("status")
    if status == "fail":
        return color.status_text("fail")
    if status == "new":
        return color.status_text("new")
    if status == "missing":
        return color.status_text("missing")
    return color.status_text("ok")


def _width(text: str, pad: int = 2) -> int:
    """中文按两个字符宽度算，终端表格才对得齐（颜色码不算宽度）。"""
    plain = _ANSI.sub("", text)
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in plain) + pad


def _pad(text: str, width: int) -> str:
    return text + " " * max(width - _width(text, 0), 0)


def render_table(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(color.bold("rag-gate · 检索质量门禁"))
    lines.append(color.dim("─" * 62))
    lines.append(
        f"配置：{report.get('config_path', '—')}    数据集：{report.get('dataset_path', '—')}"
    )
    lines.append(
        f"Golden set：{report.get('dataset_size', 0)} 条    取数方式：{report.get('adapter', '—')}"
    )
    baseline_at = report.get("baseline", {}).get("created_at") if report.get("baseline") else None
    lines.append(f"基线：{baseline_at or '（无基线，本次只记录）'}")
    lines.append("")

    rows = report.get("rows") or []
    if rows:
        matrix = [
            [
                color.bold(str(row.get("metric"))),
                _fmt(row.get("baseline")),
                color.bold(_fmt(row.get("current"))),
                _color_delta(row.get("delta")),
                "—" if row.get("max_drop") is None else f"{row['max_drop']:g}",
                _status_text(row),
            ]
            for row in rows
        ]
        widths = [
            max(_width(_TABLE_HEAD[i]), *(_width(r[i]) for r in matrix))
            for i in range(len(_TABLE_HEAD))
        ]
        head = "  ".join(_pad(h, widths[i]) for i, h in enumerate(_TABLE_HEAD)).rstrip()
        lines.append(color.dim(head))
        lines.append(color.dim("  ".join("─" * widths[i] for i in range(len(_TABLE_HEAD)))))
        for row in matrix:
            lines.append("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip())
    else:
        lines.append(color.dim("（只跑了 run，没有基线对比）"))
        for name, value in (report.get("metrics") or {}).items():
            lines.append(f"  {_pad(color.bold(name), 14)}{color.bold(_fmt(value))}")

    drift = report.get("drift") or {}
    if drift.get("before") or drift.get("after"):
        lines.append("")
        lines.append(color.bold("相似度分布（drift）"))
        before = drift.get("before") or {}
        after = drift.get("after") or {}
        for key in ("mean", "p50", "p90"):
            if key in before or key in after:
                delta = None
                if key in before and key in after:
                    delta = after[key] - before[key]
                lines.append(
                    f"  top1.{key:<5} {_fmt(before.get(key))} → {_fmt(after.get(key))}"
                    f"   {_color_delta(delta)}"
                )
        reason = drift.get("reason", "—")
        lines.append("  结论：" + (color.red(reason) if drift.get("drifted") else color.green(reason)))

    if report.get("warnings"):
        lines.append("")
        for item in report["warnings"]:
            lines.append(color.yellow("警告：") + item)
    if report.get("failures"):
        lines.append("")
        for item in report["failures"]:
            lines.append(color.red("失败：") + item)

    if report.get("comparison"):
        code = report.get("exit_code", 0)
        text = f"门禁结果：{EXIT_CODES.get(code, code)}（exit {code}）"
        lines.append("")
        lines.append(color.bold(color.green(text) if code == 0 else color.red(text)))
    return "\n".join(lines)


def _color_delta(delta: Optional[float]) -> str:
    text = _signed(delta)
    if delta is None:
        return text
    if delta < 0:
        return color.red(text)
    if delta > 0:
        return color.green(text)
    return color.dim(text)


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    code = report.get("exit_code", 0)
    badge = {0: "✅ 通过", 1: "❌ 退化", 2: "⚠️ 出错"}.get(code, str(code))
    lines.append(f"## rag-gate · 检索质量门禁 {badge}")
    lines.append("")
    if report.get("comparison"):
        rows = report.get("rows") or []
        if rows:
            lines.append("| 指标 | 基线 | 本次 | 变化 | 允许跌幅 | 判定 |")
            lines.append("| --- | ---: | ---: | ---: | ---: | :--- |")
            for row in rows:
                mark = {"fail": "❌ FAIL", "new": "🆕 new", "missing": "➖ 未跑"}.get(
                    row.get("status"), "✅ ok"
                )
                allow = "—" if row.get("max_drop") is None else f"{row['max_drop']:g}"
                lines.append(
                    f"| `{row.get('metric')}` | {_fmt(row.get('baseline'))} | {_fmt(row.get('current'))} "
                    f"| {_signed(row.get('delta'))} | {allow} | {mark} |"
                )
            lines.append("")
    else:
        lines.append("| 指标 | 本次 |")
        lines.append("| --- | ---: |")
        for name, value in (report.get("metrics") or {}).items():
            lines.append(f"| `{name}` | {_fmt(value)} |")
        lines.append("")

    drift = report.get("drift") or {}
    if drift.get("before") or drift.get("after"):
        before = drift.get("before") or {}
        after = drift.get("after") or {}
        lines.append(
            f"**相似度分布**：top1 均值 {_fmt(before.get('mean'))} → {_fmt(after.get('mean'))}，"
            f"p50 {_fmt(before.get('p50'))} → {_fmt(after.get('p50'))} —— {drift.get('reason', '—')}"
        )
        lines.append("")

    if report.get("failures"):
        lines.append("### 为什么红")
        for item in report["failures"]:
            lines.append(f"- {item}")
        lines.append("")
    if report.get("warnings"):
        lines.append("### 提醒")
        for item in report["warnings"]:
            lines.append(f"- {item}")
        lines.append("")
    lines.append(
        f"<sub>golden set {report.get('dataset_size', 0)} 条 · 基线 "
        f"{(report.get('baseline') or {}).get('created_at') or '无'} · "
        f"rag-gate {report.get('version', '')}</sub>"
    )
    return "\n".join(lines)


def write_reports(report: Dict[str, Any], output_dir: str, formats: List[str], stem: str = "latest") -> List[str]:
    """按配置落盘，返回写出的文件路径。"""
    written: List[str] = []
    if not formats:
        return written
    os.makedirs(output_dir, exist_ok=True)
    for fmt in formats:
        if fmt in ("markdown", "md"):
            path = os.path.join(output_dir, f"{stem}.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(render_markdown(report) + "\n")
        elif fmt == "json":
            path = os.path.join(output_dir, f"{stem}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        else:
            path = os.path.join(output_dir, f"{stem}.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(render_table(report) + "\n")
        written.append(path)
    return written


def render(fmt: str, report: Dict[str, Any]) -> str:
    if fmt in ("markdown", "md"):
        return render_markdown(report)
    if fmt == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    return render_table(report)
