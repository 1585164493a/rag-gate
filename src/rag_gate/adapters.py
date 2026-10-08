"""三种取数方式：python / http / command。

设计约束（SEED 第 3 节）：**适配靠回调，不靠集成。**
所以这里不 import 任何向量库、不假设返回结构，只要求用户告诉我们
「问题 → 文档 id 列表」怎么取。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from rag_gate.config import RetrieverConfig


class AdapterError(Exception):
    """取数失败 —— 对应退出码 2（是「取不到数」，不是「检索变差」）。"""


# ---------------------------------------------------------------------------
# 返回值归一化
# ---------------------------------------------------------------------------


def _to_hit(item: Any) -> Optional[Dict[str, Any]]:
    if item is None:
        return None
    if isinstance(item, (str, int)):
        return {"id": str(item)}
    if isinstance(item, dict):
        for key in ("doc_id", "id", "docid", "_id", "chunk_id"):
            if item.get(key) is not None:
                hit: Dict[str, Any] = {"id": str(item[key])}
                for score_key in ("score", "similarity", "distance"):
                    if isinstance(item.get(score_key), (int, float)):
                        hit["score"] = float(item[score_key])
                        break
                return hit
        raise AdapterError(f"取数结果里有不认识的对象（缺 doc_id/id 字段）：{item!r}")
    if isinstance(item, (list, tuple)) and item:
        hit = {"id": str(item[0])}
        if len(item) > 1 and isinstance(item[1], (int, float)):
            hit["score"] = float(item[1])
        return hit
    raise AdapterError(f"取数结果里有不认识的元素类型：{type(item).__name__}")


def normalize(raw: Any) -> Dict[str, Any]:
    """把各种可能的返回形状统一成 ``{"ids": [...], "scores": [...]}``。"""
    if raw is None:
        return {"ids": [], "scores": []}
    if isinstance(raw, dict) and "ids" in raw:
        ids = [str(x) for x in (raw.get("ids") or [])]
        scores = [float(s) for s in (raw.get("scores") or []) if isinstance(s, (int, float))]
        return {"ids": ids, "scores": scores}
    if isinstance(raw, dict) and "hits" in raw:  # 常见 HTTP 形状
        raw = raw["hits"]
    if isinstance(raw, dict) and "documents" in raw:
        raw = raw["documents"]
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise AdapterError(
            "取数函数要返回文档 id 列表，或 [{\"id\": ..., \"score\": ...}] "
            f"（当前拿到的是 {type(raw).__name__}）"
        )
    ids: List[str] = []
    scores: List[float] = []
    for item in raw:
        hit = _to_hit(item)
        if hit is None:
            continue
        ids.append(hit["id"])
        if "score" in hit:
            scores.append(hit["score"])
    return {"ids": ids, "scores": scores}


# ---------------------------------------------------------------------------
# 极简 JSONPath（只支持 README 里承诺的那几种写法）
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\.([A-Za-z_][\w\-]*)|\[\s*(\d+|\*)\s*\]|\[\s*'([^']*)'\s*\]|\[\s*\"([^\"]*)\"\s*\]")


def json_path(data: Any, path: str) -> List[Any]:
    """支持 ``$.data[*].doc_id`` / ``$[0].id`` / ``$.hits[*]``。

    查不到就返回空列表（不报错），报错交给上层拼接人话。
    """
    if not isinstance(path, str) or not path.strip():
        return []
    expr = path.strip()
    if expr.startswith("$"):
        expr = expr[1:]
    current: List[Any] = [data]
    pos = 0
    while pos < len(expr):
        match = _TOKEN.match(expr, pos)
        if not match:
            raise AdapterError(
                f"看不懂的 JSONPath `{path}`（第 {pos + 1} 个字符附近）。"
                "本工具只支持 $.a.b / $.a[*].b / $[0] 这类写法"
            )
        pos = match.end()
        key, index, key_s, key_d = match.groups()
        nxt: List[Any] = []
        for node in current:
            if key or key_s or key_d:
                name = key or key_s or key_d
                if isinstance(node, dict) and name in node:
                    nxt.append(node[name])
            elif index == "*":
                if isinstance(node, list):
                    nxt.extend(node)
                elif isinstance(node, dict):
                    nxt.extend(node.values())
            elif index is not None:
                if isinstance(node, list):
                    i = int(index)
                    if -len(node) <= i < len(node):
                        nxt.append(node[i])
        current = nxt
    return current


# ---------------------------------------------------------------------------
# python adapter
# ---------------------------------------------------------------------------


def _split_target(target: str):
    """把 ``路径:函数名`` 拆开。

    注意 Windows 盘符里有冒号（``D:\\code\\ret.py:go``），所以按**最后一个**冒号拆。
    """
    index = target.rfind(":")
    if index <= 0:
        return target, ""
    head, attr = target[:index], target[index + 1:]
    for test in ("./", "../", "/", "\\"):
        if test in attr:
            return target, ""
    return head, attr


def _load_callable(target: str, base_dir: Optional[str] = None):
    """支持两种写法：``my_app.retrieve`` 和 ``./scripts/retrieve.py:retrieve``。

    脚本路径相对于**配置文件所在目录**解析（CI 里很常见从别的目录调起）。
    """
    module_name, attr = _split_target(target)
    if attr:
        path = module_name
        if not os.path.isabs(path):
            base = base_dir or os.getcwd()
            candidate = os.path.normpath(os.path.join(base, path))
            if os.path.exists(candidate):
                path = candidate
            else:
                path = os.path.abspath(path)
        if not os.path.exists(path):
            raise AdapterError(
                f"找不到取数脚本：{path}\n"
                "  路径是相对于配置文件所在目录解析的，也可以写绝对路径"
            )
        spec = importlib.util.spec_from_file_location("_rag_gate_target", path)
        if spec is None or spec.loader is None:
            raise AdapterError(f"无法加载取数脚本：{path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["_rag_gate_target"] = module
        spec.loader.exec_module(module)
    else:
        if "." not in module_name:
            raise AdapterError(
                f"python adapter 的 target `{target}` 应该是「模块.函数」"
                "（例如 my_app.retrieve），或者是「脚本路径:函数名」"
            )
        module_name, _, attr = module_name.rpartition(".")
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"import 不到 `{module_name}`：{exc}\n"
                "  提示：在项目根目录执行，或把 PYTHONPATH 指到你的代码目录"
            ) from exc
    func = getattr(module, attr, None)
    if func is None:
        raise AdapterError(f"`{target}` 里没有 `{attr}` 这个函数")
    if not callable(func):
        raise AdapterError(f"`{target}` 里的 `{attr}` 不是函数")
    return func


def _call_python(func, question: str, k: int) -> Any:
    """优先按 (query, k) 调；用户函数只接受一个参数也能跑。"""
    import inspect

    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(question)
    params = [
        p
        for p in signature.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    has_var = any(p.kind == p.VAR_POSITIONAL for p in signature.parameters.values())
    if len(params) >= 2 or has_var:
        return func(question, k)
    return func(question)


# ---------------------------------------------------------------------------
# 三个 adapter 实现
# ---------------------------------------------------------------------------


def retrieve_python(cfg: RetrieverConfig, question: str) -> Dict[str, Any]:
    func = _load_callable(str(cfg.target), cfg.base_dir)
    return normalize(_call_python(func, question, cfg.top_k))


def _render(template: str, question: str, k: int) -> str:
    return template.replace("{{question}}", question).replace("{{k}}", str(k))


def _render_deep(node: Any, question: str, k: int) -> Any:
    if isinstance(node, str):
        return _render(node, question, k)
    if isinstance(node, dict):
        return {key: _render_deep(val, question, k) for key, val in node.items()}
    if isinstance(node, list):
        return [_render_deep(x, question, k) for x in node]
    return node


def retrieve_http(cfg: RetrieverConfig, question: str) -> Dict[str, Any]:
    url = _render(str(cfg.url), question, cfg.top_k)
    method = str(cfg.raw.get("method") or ("POST" if cfg.request else "GET")).upper()
    data = None
    headers = {"Accept": "application/json", **{str(k): str(v) for k, v in cfg.headers.items()}}
    if method == "GET":
        if cfg.request:
            from urllib.parse import urlencode

            query = urlencode({k: _render_deep(v, question, cfg.top_k) for k, v in cfg.request.items()})
            url = f"{url}{'&' if '?' in url else '?'}{query}"
    else:
        body = _render_deep(cfg.request or {"q": "{{question}}", "k": "{{k}}"}, question, cfg.top_k)
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=cfg.timeout) as resp:
            payload = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise AdapterError(f"HTTP {exc.code}（{url}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise AdapterError(f"连不上 {url}：{exc.reason}\n  提示：确认检索服务已经起来") from exc
    except OSError as exc:
        raise AdapterError(f"请求 {url} 失败：{exc}") from exc

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AdapterError(
            f"{url} 返回的不是 JSON（{exc.msg}）：{payload[:120]}…"
        ) from exc

    response_map = cfg.response or {"hits": "$[*]"}
    hits_path = response_map.get("hits") or "$[*]"
    raw_hits = json_path(parsed, str(hits_path))
    if not raw_hits:
        shape = list(parsed)[:6] if isinstance(parsed, dict) else f"list[{len(parsed)}]"
        raise AdapterError(
            f"response.hits 从响应里取不到东西。\n"
            f"  hits 路径：{hits_path}\n"
            f"  实际响应的顶层结构：{shape}\n"
            "  改法：把 response.hits 改成真实路径，例如 $.data[*].doc_id"
        )
    scores: List[float] = []
    if response_map.get("scores"):
        for value in json_path(parsed, str(response_map["scores"])):
            if isinstance(value, (int, float)):
                scores.append(float(value))
    if raw_hits and isinstance(raw_hits[0], (dict, list, tuple)):
        # 路径取到的是整个 hit 对象，交给 normalize 去认 doc_id 字段
        return normalize({"hits": raw_hits, "scores": scores})
    ids = [str(x) for x in raw_hits]
    return normalize({"ids": ids, "scores": scores})


def _parse_command_output(text: str, mode: str) -> Any:
    text = text.strip()
    if not text:
        return []
    if mode == "lines":
        return [line.strip() for line in text.splitlines() if line.strip()]
    if mode == "json":
        try:
            data = json.loads(text)
            return data
        except json.JSONDecodeError as exc:
            raise AdapterError(f"parse: json 但输出不是合法 JSON：{exc.msg}") from exc
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise AdapterError(f"parse: jsonl 但第 {lineno} 行不是合法 JSON：{exc.msg}") from exc
    if mode == "json-ids":
        return rows
    # jsonl：每行可能是 "doc_id"、{"doc_id": ...}
    out = []
    for row in rows:
        if isinstance(row, dict):
            for key in ("doc_id", "id", "docid", "chunk_id"):
                if row.get(key) is not None:
                    out.append(row)
                    break
            else:
                out.append(row)
        else:
            out.append(row)
    return out


def retrieve_command(cfg: RetrieverConfig, question: str) -> Dict[str, Any]:
    command = _render(str(cfg.command), question, cfg.top_k)
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        if os.name == "nt":
            completed = subprocess.run(
                command, shell=True, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=cfg.timeout, cwd=cfg.base_dir, env=env,
            )
        else:
            completed = subprocess.run(
                shlex.split(command) if not any(ch in command for ch in "|&;<>$") else command,
                shell=any(ch in command for ch in "|&;<>$"),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=cfg.timeout, cwd=cfg.base_dir, env=env,
            )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"命令超时（>{cfg.timeout:g}s）：{command}") from exc
    if completed.returncode != 0:
        err = (completed.stderr or "").strip().splitlines()
        tail = "\n  ".join(err[-5:]) if err else "(没有 stderr)"
        raise AdapterError(
            f"命令退出码 {completed.returncode}：{command}\n  {tail}"
        )
    return normalize(_parse_command_output(completed.stdout or "", cfg.parse))


# ---------------------------------------------------------------------------
# 入口：跑完整个 golden set
# ---------------------------------------------------------------------------


def retrieve_all(cfg: RetrieverConfig, rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    dispatch = {"python": retrieve_python, "http": retrieve_http, "command": retrieve_command}
    func = dispatch[cfg.type]
    failures: List[str] = []
    total = len(rows)
    show_progress = total >= 20 or sys.stderr.isatty()
    if show_progress:
        print(f"正在取数：0/{total}", end="\r", file=sys.stderr, flush=True)
    for index, row in enumerate(rows, start=1):
        qid = str(row["id"])
        question = str(row["question"])
        try:
            results[qid] = func(cfg, question)
        except AdapterError as exc:
            failures.append(f"  · [{qid}] {exc}")
            results[qid] = {"ids": [], "scores": [], "error": str(exc)}
        except Exception as exc:  # 用户代码抛的异常也要变成人话
            failures.append(f"  · [{qid}] 取数时报错（{type(exc).__name__}）：{exc}")
            results[qid] = {"ids": [], "scores": [], "error": f"{type(exc).__name__}: {exc}"}
        if show_progress:
            print(f"正在取数：{index}/{total}", end="\r", file=sys.stderr, flush=True)
    if show_progress:
        print(" " * 24, end="\r", file=sys.stderr, flush=True)
    if failures:
        raise AdapterError(
            f"{len(failures)}/{total} 条取数失败，先修取数再谈门禁：\n" + "\n".join(failures[:8])
            + ("\n  …" if len(failures) > 8 else "")
        )
    return results
