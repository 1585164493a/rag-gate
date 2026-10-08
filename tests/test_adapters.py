"""三种取数方式：返回形状要能容错，报错要说人话。"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rag_gate.adapters import (  # noqa: E402
    AdapterError,
    json_path,
    normalize,
    retrieve_all,
    retrieve_command,
    retrieve_http,
    retrieve_python,
)
from rag_gate.config import RetrieverConfig  # noqa: E402


def cfg(**kwargs):
    return RetrieverConfig(kwargs, os.getcwd())


# ---------------------------------------------------------------------------
# JSONPath
# ---------------------------------------------------------------------------


def test_json_path_basics():
    data = {"data": [{"doc_id": "a", "score": 0.9}, {"doc_id": "b", "score": 0.8}]}
    assert json_path(data, "$.data[*].doc_id") == ["a", "b"]
    assert json_path(data, "$.data[*].score") == [0.9, 0.8]
    assert json_path(data, "$.data[0].doc_id") == ["a"]
    assert json_path(data, "$.data[*]") == data["data"]
    assert json_path({"hits": ["x"]}, "$.hits[*]") == ["x"]
    assert json_path(data, "$.nope[*]") == []


def test_json_path_rejects_weird_syntax():
    with pytest.raises(AdapterError) as excinfo:
        json_path({}, "$.a[?(@.b)]")
    assert "JSONPath" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


def test_normalize_string_list():
    assert normalize(["a", "b"]) == {"ids": ["a", "b"], "scores": []}


def test_normalize_dict_hits_with_scores():
    got = normalize([{"doc_id": "a", "score": 0.9}, {"id": "b", "similarity": 0.5}])
    assert got["ids"] == ["a", "b"]
    assert got["scores"] == [0.9, 0.5]


def test_normalize_tuples_and_already_normalized():
    assert normalize([("a", 0.7)]) == {"ids": ["a"], "scores": [0.7]}
    assert normalize({"ids": ["a"], "scores": [0.1]}) == {"ids": ["a"], "scores": [0.1]}


def test_normalize_common_http_shapes():
    assert normalize({"hits": [{"doc_id": "a"}]})["ids"] == ["a"]
    assert normalize({"documents": ["a", "b"]})["ids"] == ["a", "b"]
    assert normalize(None) == {"ids": [], "scores": []}


def test_normalize_rejects_unknown_shape():
    with pytest.raises(AdapterError) as excinfo:
        normalize(42)
    assert "文档 id 列表" in str(excinfo.value)
    with pytest.raises(AdapterError):
        normalize([{"title": "没有 id 字段"}])


# ---------------------------------------------------------------------------
# python adapter
# ---------------------------------------------------------------------------


def test_python_adapter_accepts_one_or_two_args(tmp_path):
    script = tmp_path / "retrievers.py"
    script.write_text(
        "def two(query, k):\n"
        "    return [{'doc_id': f'd{i}', 'score': 1.0 - i / 10} for i in range(k)]\n"
        "def one(query):\n"
        "    return ['only']\n",
        encoding="utf-8",
    )
    two = cfg(type="python", target=f"{script}:two", top_k=3)
    assert retrieve_python(two, "问")["ids"] == ["d0", "d1", "d2"]

    one = cfg(type="python", target=f"{script}:one", top_k=3)
    assert retrieve_python(one, "问")["ids"] == ["only"]


def test_python_adapter_resolves_script_relative_to_config(tmp_path):
    (tmp_path / "ret.py").write_text("def go(q, k):\n    return ['x']\n", encoding="utf-8")
    conf = RetrieverConfig({"type": "python", "target": "./ret.py:go", "top_k": 5}, str(tmp_path))
    assert retrieve_python(conf, "问")["ids"] == ["x"]


def test_python_adapter_reports_missing_script(tmp_path):
    conf = RetrieverConfig({"type": "python", "target": "./nope.py:go"}, str(tmp_path))
    with pytest.raises(AdapterError) as excinfo:
        retrieve_python(conf, "问")
    assert "找不到取数脚本" in str(excinfo.value)


def test_python_adapter_requires_module_dot_function(tmp_path):
    conf = RetrieverConfig({"type": "python", "target": "只写了个名字"}, str(tmp_path))
    with pytest.raises(AdapterError) as excinfo:
        retrieve_python(conf, "问")
    assert "模块.函数" in str(excinfo.value)


def test_python_adapter_reports_missing_function(tmp_path):
    script = tmp_path / "ret2.py"
    script.write_text("def other(q, k):\n    return []\n", encoding="utf-8")
    conf = RetrieverConfig({"type": "python", "target": f"{script}:go"}, str(tmp_path))
    with pytest.raises(AdapterError) as excinfo:
        retrieve_python(conf, "问")
    assert "没有 `go`" in str(excinfo.value)


# ---------------------------------------------------------------------------
# http adapter（起一个真服务器，不打桩）
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        payload = {
            "data": [
                {"doc_id": "kb-001", "score": 0.9, "echo": body},
                {"doc_id": "kb-002", "score": 0.7},
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        raw = json.dumps({"data": [{"doc_id": "kb-009", "score": 0.5}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # 测试里别刷屏
        pass


@pytest.fixture(scope="module")
def search_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_http_adapter_post_with_template(search_server):
    conf = cfg(
        type="http",
        url=f"{search_server}/search",
        request={"q": "{{question}}", "k": "{{k}}"},
        response={"hits": "$.data[*].doc_id", "scores": "$.data[*].score"},
        top_k=5,
    )
    got = retrieve_http(conf, "向量库重复数据")
    assert got["ids"] == ["kb-001", "kb-002"]
    assert got["scores"] == [0.9, 0.7]


def test_http_adapter_get(search_server):
    conf = cfg(type="http", url=f"{search_server}/search", method="GET",
               response={"hits": "$.data[*].doc_id"}, top_k=5)
    assert retrieve_http(conf, "问")["ids"] == ["kb-009"]


def test_http_adapter_bad_path_says_what_to_fix(search_server):
    conf = cfg(type="http", url=f"{search_server}/search", request={"q": "{{question}}"},
               response={"hits": "$.results[*].id"}, top_k=5)
    with pytest.raises(AdapterError) as excinfo:
        retrieve_http(conf, "问")
    message = str(excinfo.value)
    assert "response.hits" in message and "data" in message


def test_http_adapter_connection_error_is_readable():
    conf = cfg(type="http", url="http://127.0.0.1:1/search", request={"q": "{{question}}"},
               response={"hits": "$.data[*]"}, timeout=1)
    with pytest.raises(AdapterError) as excinfo:
        retrieve_http(conf, "问")
    assert "连不上" in str(excinfo.value)


# ---------------------------------------------------------------------------
# command adapter
# ---------------------------------------------------------------------------


def test_command_adapter_jsonl(tmp_path):
    script = tmp_path / "search.py"
    script.write_text(
        "import json, sys\n"
        "q = sys.argv[sys.argv.index('--q') + 1]\n"
        "print(json.dumps({'doc_id': 'doc-' + q, 'score': 0.8}, ensure_ascii=False))\n"
        "print(json.dumps({'doc_id': 'doc-two'}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    conf = cfg(type="command", command=f'"{sys.executable}" "{script}" --q {{{{question}}}}',
               parse="jsonl", top_k=5)
    got = retrieve_command(conf, "甲")
    assert got["ids"] == ["doc-甲", "doc-two"]
    assert got["scores"] == [0.8]


def test_command_adapter_lines(tmp_path):
    script = tmp_path / "lines.py"
    script.write_text("print('a')\nprint('b')\n", encoding="utf-8")
    conf = cfg(type="command", command=f'"{sys.executable}" "{script}"', parse="lines", top_k=5)
    assert retrieve_command(conf, "问")["ids"] == ["a", "b"]


def test_command_adapter_failure_includes_stderr(tmp_path):
    script = tmp_path / "boom.py"
    script.write_text("import sys\nprint('检索服务没启动', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8")
    conf = cfg(type="command", command=f'"{sys.executable}" "{script}"', parse="lines", top_k=5)
    with pytest.raises(AdapterError) as excinfo:
        retrieve_command(conf, "问")
    message = str(excinfo.value)
    assert "退出码 3" in message and "检索服务没启动" in message


def test_command_adapter_bad_jsonl_points_at_line(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("print('不是 json')\n", encoding="utf-8")
    conf = cfg(type="command", command=f'"{sys.executable}" "{script}"', parse="jsonl", top_k=5)
    with pytest.raises(AdapterError) as excinfo:
        retrieve_command(conf, "问")
    assert "第 1 行" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 跑整个数据集
# ---------------------------------------------------------------------------


def test_retrieve_all_collects_and_reports_failures(tmp_path):
    script = tmp_path / "mixed.py"
    script.write_text(
        "import sys\n"
        "q = sys.argv[1]\n"
        "if '坏' in q:\n"
        "    raise RuntimeError('故意炸一下')\n"
        "print(q)\n",
        encoding="utf-8",
    )
    conf = cfg(type="command", command=f'"{sys.executable}" "{script}" {{{{question}}}}',
               parse="lines", top_k=5, base_dir=str(tmp_path))
    rows = [{"id": "q1", "question": "好问题"}, {"id": "q2", "question": "坏问题"}]
    with pytest.raises(AdapterError) as excinfo:
        retrieve_all(conf, rows)
    message = str(excinfo.value)
    assert "1/2 条取数失败" in message and "RuntimeError" in message


def test_retrieve_all_ok(tmp_path):
    script = tmp_path / "ok.py"
    script.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")
    conf = cfg(type="command", command=f'"{sys.executable}" "{script}" {{{{question}}}}',
               parse="lines", top_k=5, base_dir=str(tmp_path))
    rows = [{"id": "q1", "question": "甲"}, {"id": "q2", "question": "乙"}]
    got = retrieve_all(conf, rows)
    assert got["q1"]["ids"] == ["甲"] and got["q2"]["ids"] == ["乙"]
