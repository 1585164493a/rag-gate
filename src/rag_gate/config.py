"""配置解析 + 校验。报错必须是人话：告诉用户「哪一行、错在哪、怎么改」。

支持 ``rag-gate.yaml`` 里的全部字段（见 examples/rag-gate.yaml）。
优先用 PyYAML；没装的话用内置的极简 YAML 解析器，
这样 ``examples/`` 才能做到「零依赖、30 秒跑通」。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from rag_gate.metrics import MetricError, canonical, parse_metric


class ConfigError(Exception):
    """配置写错了 —— 对应退出码 2。"""


# ---------------------------------------------------------------------------
# 极简 YAML（够用就好）：嵌套 map、'- ' 列表、行内 [a, b]、注释、引号、数字/布尔
# ---------------------------------------------------------------------------


def _plain_scalar(text: str) -> Any:
    t = text.strip()
    if t == "":
        return None
    if t[0] in "\"'" and t.endswith(t[0]) and len(t) >= 2:
        return t[1:-1]
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", "none"):
        return None
    if t.startswith("[") or t.startswith("{"):
        try:
            return json.loads(t.replace("'", '"'))
        except json.JSONDecodeError:
            inner = t[1:-1].strip()
            if t.startswith("["):
                return [_plain_scalar(x) for x in _split_inline(inner)] if inner else []
            items = []
            for pair in _split_inline(inner):
                key, _, val = pair.partition(":")
                items.append((key.strip(), _plain_scalar(val)))
            return dict(items)
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _split_inline(text: str) -> List[str]:
    """按逗号切，但不管引号里的逗号（够用级别）。"""
    out, buf, quote = [], [], None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [x.strip() for x in out if x.strip() != ""]


def _is_empty_container(node: Any) -> bool:
    """空容器还没吃到自己的子项，不能被当成「已结束」弹出栈。"""
    return isinstance(node, (dict, list)) and len(node) == 0


def _mini_yaml(text: str) -> Dict[str, Any]:
    """缩进驱动的极简 YAML。不支持锚点/多行块/复杂嵌套，但覆盖本项目的配置。"""
    root: Dict[str, Any] = {}
    # stack 里的每层：(缩进量, 容器对象)
    stack: List[Any] = [(-1, root)]
    lines = text.splitlines()
    for lineno, raw in enumerate(lines, start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = _strip_comment(raw.strip())
        if not content:
            continue
        while len(stack) > 1 and indent <= stack[-1][0] and not _is_empty_container(stack[-1][1]):
            stack.pop()
        container = stack[-1][1]
        if content.startswith("- "):
            item = content[2:].strip()
            if not isinstance(container, list):
                raise ConfigError(f"第 {lineno} 行：这个位置不该出现列表项 `- {item}`")
            container.append(_plain_scalar(item))
            continue
        if ":" not in content:
            raise ConfigError(f"第 {lineno} 行：看不懂 `{content}`，YAML 一行要么是 `key: value`，要么是 `- item`")
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if not isinstance(container, dict):
            raise ConfigError(f"第 {lineno} 行：`{key}` 出现的位置不对（上一层是列表）")
        if value == "":
            # 空值：下一层可能是 map 或 list，先看下一行是不是 `- ` 再决定
            nxt = _next_content(lines, lineno)
            child: Any = [] if (nxt and nxt[1].startswith("- ")) else {}
            container[key] = child
            stack.append((indent, child))
        else:
            container[key] = _plain_scalar(value)
    return root


def _next_content(lines: List[str], lineno: int):
    for i in range(lineno, len(lines)):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        return indent, _strip_comment(raw.strip())
    return None


def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].strip()
    return line.strip()


def load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise ConfigError(f"找不到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        data = _mini_yaml(text)
    except Exception as exc:  # PyYAML 的报错直接透出，它本身带行号
        raise ConfigError(f"YAML 解析失败：{exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("配置文件最外层必须是一个 map（key: value）")
    return data


# ---------------------------------------------------------------------------
# 校验后的配置对象
# ---------------------------------------------------------------------------


class RetrieverConfig:
    def __init__(self, raw: Dict[str, Any], base_dir: str):
        self.raw = raw
        self.type = str(raw.get("type") or "").strip().lower()
        self.top_k = int(raw.get("top_k") or 10)
        self.target = raw.get("target")
        self.url = raw.get("url")
        self.request = raw.get("request") or {}
        self.response = raw.get("response") or {}
        self.headers = raw.get("headers") or {}
        self.command = raw.get("command")
        self.parse = (raw.get("parse") or "jsonl").lower()
        self.base_dir = base_dir
        self.timeout = float(raw.get("timeout") or 30)
        self._validate()

    def _validate(self) -> None:
        if self.type not in ("python", "http", "command"):
            raise ConfigError(
                "retriever.type 必须是 python / http / command 之一"
                f"（当前是 `{self.type or '空'}`）。\n"
                "  python  → target: my_app.retrieve\n"
                "  http    → url: http://localhost:8000/search\n"
                "  command → command: \"python search.py --q {{question}}\""
            )
        if self.top_k <= 0:
            raise ConfigError("retriever.top_k 必须是正整数")
        if self.type == "python" and not self.target:
            raise ConfigError("retriever.type 是 python，就必须写 target（例如 my_app.retrieve）")
        if self.type == "http" and not self.url:
            raise ConfigError("retriever.type 是 http，就必须写 url")
        if self.type == "command" and not self.command:
            raise ConfigError("retriever.type 是 command，就必须写 command")
        if self.type == "command" and self.parse not in ("jsonl", "json", "json-ids", "lines"):
            raise ConfigError(
                f"retriever.parse 不认 `{self.parse}`；支持 jsonl / json / json-ids / lines"
            )
        if self.type == "http" and self.response and "hits" not in self.response:
            raise ConfigError(
                "retriever.response 至少要有 hits，例如：\n"
                "  response: {hits: \"$.data[*].doc_id\", scores: \"$.data[*].score\"}"
            )


class Config:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.base_dir = os.path.dirname(self.path)
        raw = load_yaml(self.path)
        self.raw = raw

        dataset = raw.get("dataset")
        if not dataset:
            raise ConfigError("配置里缺少 dataset（指向 golden set 文件，例如 golden.jsonl）")
        self.dataset_path = self._resolve(dataset)
        if not os.path.exists(self.dataset_path):
            raise ConfigError(f"dataset 指向的文件不存在：{self.dataset_path}")

        retriever = raw.get("retriever")
        if not isinstance(retriever, dict):
            raise ConfigError("配置里缺少 retriever 段（type + target/url/command）")
        self.retriever = RetrieverConfig(retriever, self.base_dir)

        metrics = raw.get("metrics")
        if not metrics:
            raise ConfigError("配置里缺少 metrics（例如：metrics: [recall@5, mrr]）")
        if not isinstance(metrics, list):
            raise ConfigError("metrics 必须是列表，例如 metrics: [recall@5, mrr]")
        names = []
        for item in metrics:
            try:
                names.append(canonical(str(item)))
            except MetricError as exc:
                raise ConfigError(str(exc)) from exc
        if len(set(names)) != len(names):
            dup = [n for n in names if names.count(n) > 1]
            raise ConfigError(f"metrics 里有重复项：{', '.join(sorted(set(dup)))}")
        self.metrics: List[str] = names

        gate = raw.get("gate") or {}
        if not isinstance(gate, dict):
            raise ConfigError("gate 必须是一个 map（baseline / max_drop / min_absolute）")
        gate = dict(gate)
        # 这几个开关放顶层也认（YAML 缩进排错位置很常见）
        for loose_key in ("fail_on_dataset_change", "fail_on_drift"):
            if loose_key in raw and loose_key not in gate:
                gate[loose_key] = raw[loose_key]
        self.gate = gate
        self.max_drop = self._number_map(gate.get("max_drop"), "gate.max_drop")
        self.min_absolute = self._number_map(gate.get("min_absolute"), "gate.min_absolute")
        baseline = gate.get("baseline")
        self.baseline_path = self._resolve(baseline) if baseline else None
        drift = gate.get("drift") or {}
        if not isinstance(drift, dict):
            raise ConfigError("gate.drift 必须是一个 map（mean_drop / p50_drop / std_ratio）")
        self.drift = {k: float(v) for k, v in drift.items() if isinstance(v, (int, float))}

        report = raw.get("report") or {}
        if not isinstance(report, dict):
            raise ConfigError("report 必须是一个 map（formats / output_dir）")
        formats = report.get("formats") or ["table"]
        if not isinstance(formats, list):
            raise ConfigError("report.formats 必须是列表，例如 [table, markdown, json]")
        for fmt in formats:
            if str(fmt).lower() not in ("table", "markdown", "md", "json"):
                raise ConfigError(f"report.formats 不认 `{fmt}`；支持 table / markdown / json")
        self.formats = [str(f).lower() for f in formats]
        self.output_dir = self._resolve(report.get("output_dir") or ".rag-gate")

    # -- 小工具 ---------------------------------------------------------
    def _resolve(self, value: Any) -> str:
        text = str(value)
        if os.path.isabs(text):
            return text
        return os.path.normpath(os.path.join(self.base_dir, text))

    def _number_map(self, raw: Any, where: str) -> Dict[str, float]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} 必须是一个 map，例如 {where}: {{recall@5: 0.02}}")
        out: Dict[str, float] = {}
        for key, value in raw.items():
            try:
                name = canonical(str(key))
            except MetricError:
                # 允许写一个配置里没启用的指标阈值，只提示不报错
                name = str(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"{where} 的 `{key}` 必须是数字（跌幅），当前是 `{value}`")
            if float(value) < 0:
                raise ConfigError(f"{where} 的 `{key}` 不能是负数（它是「允许的跌幅」）")
            out[name] = float(value)
        return out

    def resolve_baseline(self, override: Optional[str] = None) -> str:
        if override:
            return os.path.abspath(override)
        if self.baseline_path:
            return self.baseline_path
        return os.path.join(self.output_dir, "baseline.json")


# ---------------------------------------------------------------------------
# Golden set
# ---------------------------------------------------------------------------


def load_dataset(path: str) -> List[Dict[str, Any]]:
    """读 golden set：.jsonl 每行一个对象，.json 直接是数组。"""
    if not os.path.exists(path):
        raise ConfigError(f"找不到 golden set：{path}")
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text:
        raise ConfigError(f"golden set 是空的：{path}")
    try:
        if path.lower().endswith(".json") and not path.lower().endswith(".jsonl"):
            data = json.loads(text)
            rows = data if isinstance(data, list) else [data]
        else:
            for lineno, line in enumerate(text.splitlines(), start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ConfigError(
                        f"{os.path.basename(path)} 第 {lineno} 行不是合法 JSON：{exc.msg}"
                    ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{os.path.basename(path)} 不是合法 JSON：{exc.msg}") from exc

    if not rows:
        raise ConfigError(f"golden set 里没有任何条目：{path}")

    seen = set()
    for index, row in enumerate(rows, start=1):
        where = f"{os.path.basename(path)} 第 {index} 条"
        if not isinstance(row, dict):
            raise ConfigError(f"{where} 必须是对象")
        if not row.get("id"):
            raise ConfigError(f"{where} 缺少 id")
        qid = str(row["id"])
        if qid in seen:
            raise ConfigError(f"{where} 的 id `{qid}` 和前面重复了（id 必须唯一）")
        seen.add(qid)
        if not row.get("question"):
            raise ConfigError(f"{where}（id={qid}）缺少 question")
        expected = row.get("expected_doc_ids")
        if expected is None:
            raise ConfigError(
                f"{where}（id={qid}）缺少 expected_doc_ids —— 没有它就没法算指标。\n"
                "  单文档场景写一个：\"expected_doc_ids\": [\"doc_12\"]"
            )
        if not isinstance(expected, list) or not expected:
            raise ConfigError(f"{where}（id={qid}）的 expected_doc_ids 必须是非空数组")
    return rows
