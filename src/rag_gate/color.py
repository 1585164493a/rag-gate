"""终端配色。遵守 NO_COLOR 约定，管道/重定向时自动关掉。

markdown 和 json 报告永远不带颜色（贴 PR 评论、喂别的工具时颜色是噪声）。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"


def enabled(force: Optional[bool] = None) -> bool:
    if force is not None:
        return force
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
        return True
    return sys.stdout.isatty()


_ENABLED = enabled()


def set_enabled(value: bool) -> None:
    global _ENABLED
    _ENABLED = value


def paint(text: str, *codes: str) -> str:
    if not _ENABLED or not codes:
        return text
    return "".join(codes) + text + RESET


def bold(text: str) -> str:
    return paint(text, BOLD)


def dim(text: str) -> str:
    return paint(text, DIM)


def green(text: str) -> str:
    return paint(text, GREEN)


def red(text: str) -> str:
    return paint(text, RED)


def yellow(text: str) -> str:
    return paint(text, YELLOW)


def cyan(text: str) -> str:
    return paint(text, CYAN)


def blue(text: str) -> str:
    return paint(text, BLUE)


STATUS_STYLE = {
    "ok": ("ok", GREEN),
    "fail": ("FAIL", RED),
    "new": ("new", CYAN),
    "missing": ("未跑", YELLOW),
}


def status_text(status: str) -> str:
    label, color = STATUS_STYLE.get(status, (status, DIM))
    return paint(label, BOLD, color)
