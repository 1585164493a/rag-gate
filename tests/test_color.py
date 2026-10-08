"""终端配色：该有色的时候有色，该干净的时候干净（json/markdown 永远不带色）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rag_gate import color  # noqa: E402
from rag_gate.report import render_markdown, render_table  # noqa: E402

REPORT = {
    "tool": "rag-gate",
    "version": "0.1.0",
    "comparison": True,
    "dataset_size": 4,
    "adapter": "python",
    "config_path": "rag-gate.yaml",
    "dataset_path": "golden.jsonl",
    "baseline": {"created_at": "2026-10-08T10:00:00+08:00"},
    "rows": [
        {
            "metric": "recall@5", "baseline": 0.9, "current": 0.6, "delta": -0.3, "drop": 0.3,
            "max_drop": 0.05, "min_absolute": None, "status": "fail",
            "reasons": ["相对基线跌了 0.3000，超过允许的 0.05"],
        },
        {
            "metric": "mrr", "baseline": 0.5, "current": 0.5, "delta": 0.0, "drop": 0.0,
            "max_drop": 0.05, "min_absolute": None, "status": "ok", "reasons": [],
        },
    ],
    "drift": {
        "before": {"mean": 0.9, "p50": 0.9, "p90": 0.95},
        "after": {"mean": 0.6, "p50": 0.6, "p90": 0.7},
        "drifted": True,
        "reason": "分布下移",
    },
    "failures": ["`recall@5`：相对基线跌了 0.3000，超过允许的 0.05"],
    "warnings": [],
    "exit_code": 1,
}


def _restore():
    color.set_enabled(color.enabled(None))


def test_paint_respects_switch():
    color.set_enabled(True)
    try:
        assert color.red("x") == "\033[31mx\033[0m"
        assert color.bold("x").startswith("\033[1m")
        assert color.status_text("fail").endswith("\033[0m")
    finally:
        _restore()

    color.set_enabled(False)
    try:
        assert color.red("x") == "x"
        assert color.bold("x") == "x"
        assert color.status_text("ok") == "ok"
    finally:
        _restore()


def test_no_color_env_disables():
    os.environ["NO_COLOR"] = "1"
    try:
        assert color.enabled() is False
    finally:
        os.environ.pop("NO_COLOR", None)


def test_force_color_env_enables():
    os.environ["FORCE_COLOR"] = "1"
    try:
        assert color.enabled() is True
    finally:
        os.environ.pop("FORCE_COLOR", None)


def test_markdown_never_has_color():
    """markdown 要贴到 PR 评论里，带了颜色码就是噪声。"""
    color.set_enabled(True)
    try:
        assert "\033[" not in render_markdown(REPORT)
    finally:
        _restore()


def test_table_does_have_color_when_enabled():
    color.set_enabled(True)
    try:
        assert "\033[" in render_table(REPORT)
    finally:
        _restore()


def test_table_colored_output_still_contains_values():
    color.set_enabled(True)
    try:
        text = render_table(REPORT)
    finally:
        _restore()
    for token in ("recall@5", "0.9000", "0.6000", "-0.3000", "FAIL", "exit 1"):
        assert token in text
