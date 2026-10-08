"""配置校验：报错必须是人话，而且要明确指出问题在哪。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rag_gate.config import (  # noqa: E402
    Config,
    ConfigError,
    _mini_yaml,
    load_dataset,
)

GOLDEN = "\n".join(
    [
        '{"id": "q1", "question": "问题一", "expected_doc_ids": ["a"]}',
        '{"id": "q2", "question": "问题二", "expected_doc_ids": ["b", "c"]}',
    ]
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "golden.jsonl").write_text(GOLDEN, encoding="utf-8")
    return tmp_path


def write_config(project, body: str) -> str:
    path = project / "rag-gate.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


MINIMAL = """
dataset: golden.jsonl
retriever:
  type: python
  target: my_app.retrieve
metrics:
  - recall@5
  - mrr
"""


def test_minimal_config_ok(project):
    cfg = Config(write_config(project, MINIMAL))
    assert cfg.metrics == ["recall@5", "mrr"]
    assert cfg.retriever.type == "python"
    assert cfg.retriever.top_k == 10
    assert cfg.dataset_path == os.path.join(str(project), "golden.jsonl")
    # 没写 gate.baseline 时，默认落在输出目录里
    assert cfg.resolve_baseline().endswith(os.path.join(".rag-gate", "baseline.json"))


def test_full_config_ok(project):
    cfg = Config(
        write_config(
            project,
            """
dataset: golden.jsonl
retriever:
  type: http
  url: http://localhost:8000/search
  top_k: 8
  response:
    hits: "$.data[*].doc_id"
    scores: "$.data[*].score"
metrics: [recall@5, ndcg@5]
gate:
  baseline: ci/baseline.json
  max_drop:
    recall@5: 0.02
  min_absolute:
    recall@5: 0.6
  drift:
    mean_drop: 0.01
report:
  formats: [table, markdown]
  output_dir: out
""",
        )
    )
    assert cfg.retriever.top_k == 8
    assert cfg.retriever.response["hits"] == "$.data[*].doc_id"
    assert cfg.max_drop == {"recall@5": 0.02}
    assert cfg.min_absolute == {"recall@5": 0.6}
    assert cfg.drift == {"mean_drop": 0.01}
    assert cfg.resolve_baseline() == os.path.join(str(project), "ci", "baseline.json")
    assert cfg.output_dir == os.path.join(str(project), "out")


def test_dataset_path_is_relative_to_config_not_cwd(project, tmp_path):
    """CI 里经常从别的目录调起，路径必须相对配置文件解析。"""
    cfg = Config(write_config(project, MINIMAL))
    assert cfg.dataset_path.startswith(str(project))
    assert os.path.exists(cfg.dataset_path)


@pytest.mark.parametrize(
    "body,expect",
    [
        ("retriever:\n  type: python\n  target: a.b\nmetrics: [mrr]\n", "dataset"),
        (
            "dataset: golden.jsonl\nretriever:\n  type: milvus\n  target: a.b\nmetrics: [mrr]\n",
            "python / http / command",
        ),
        ("dataset: golden.jsonl\nretriever:\n  type: python\nmetrics: [mrr]\n", "target"),
        ("dataset: golden.jsonl\nretriever:\n  type: http\nmetrics: [mrr]\n", "url"),
        ("dataset: golden.jsonl\nretriever:\n  type: command\nmetrics: [mrr]\n", "command"),
        (
            "dataset: golden.jsonl\nretriever:\n  type: python\n  target: a.b\nmetrics: [f1@5]\n",
            "不认识的指标",
        ),
        (
            "dataset: golden.jsonl\nretriever:\n  type: python\n  target: a.b\nmetrics: [mrr, mrr]\n",
            "重复",
        ),
        (
            "dataset: golden.jsonl\nretriever:\n  type: python\n  target: a.b\nmetrics: [mrr]\n"
            "gate:\n  max_drop:\n    mrr: 很多\n",
            "必须是数字",
        ),
        (
            "dataset: golden.jsonl\nretriever:\n  type: python\n  target: a.b\nmetrics: [mrr]\n"
            "gate:\n  max_drop:\n    mrr: -1\n",
            "不能是负数",
        ),
        (
            "dataset: golden.jsonl\nretriever:\n  type: python\n  target: a.b\nmetrics: [mrr]\n"
            "report:\n  formats: [csv]\n",
            "不认",
        ),
    ],
)
def test_config_errors_are_helpful(project, body, expect):
    with pytest.raises(ConfigError) as excinfo:
        Config(write_config(project, body))
    assert expect in str(excinfo.value)


def test_config_missing_file(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        Config(str(tmp_path / "nope.yaml"))
    assert "找不到配置文件" in str(excinfo.value)


def test_http_response_must_have_hits(project):
    body = (
        "dataset: golden.jsonl\nretriever:\n  type: http\n  url: http://x/search\n"
        "  response:\n    items: \"$.data\"\nmetrics: [mrr]\n"
    )
    with pytest.raises(ConfigError) as excinfo:
        Config(write_config(project, body))
    assert "hits" in str(excinfo.value)


# ---------------------------------------------------------------------------
# golden set
# ---------------------------------------------------------------------------


def test_load_dataset_ok(project):
    rows = load_dataset(os.path.join(str(project), "golden.jsonl"))
    assert [row["id"] for row in rows] == ["q1", "q2"]


def test_load_dataset_json_array(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text('[{"id": "q1", "question": "问", "expected_doc_ids": ["a"]}]', encoding="utf-8")
    assert len(load_dataset(str(path))) == 1


@pytest.mark.parametrize(
    "line,expect",
    [
        ('{"question": "问", "expected_doc_ids": ["a"]}', "id"),
        ('{"id": "q1", "expected_doc_ids": ["a"]}', "question"),
        ('{"id": "q1", "question": "问"}', "expected_doc_ids"),
        ('{"id": "q1", "question": "问", "expected_doc_ids": []}', "非空数组"),
        ('{"id": "q1", "question": "问", "expected_doc_ids": ["a"]}\n{"id": "q1", "question": "问2", "expected_doc_ids": ["b"]}', "重复"),
        ("{不是 json}", "不是合法 JSON"),
    ],
)
def test_load_dataset_errors(tmp_path, line, expect):
    path = tmp_path / "golden.jsonl"
    path.write_text(line, encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_dataset(str(path))
    assert expect in str(excinfo.value)


# ---------------------------------------------------------------------------
# 内置极简 YAML（没装 PyYAML 时的兜底）
# ---------------------------------------------------------------------------


def test_mini_yaml_shapes():
    data = _mini_yaml(
        """
# 注释
dataset: golden.jsonl
retriever:
  type: python
  top_k: 5
metrics:
  - recall@5
  - mrr
gate:
  max_drop:
    recall@5: 0.02
  baseline: b.json
report:
  formats: [table, json]
  output_dir: out
"""
    )
    assert data["dataset"] == "golden.jsonl"
    assert data["retriever"] == {"type": "python", "top_k": 5}
    assert data["metrics"] == ["recall@5", "mrr"]
    assert data["gate"]["max_drop"] == {"recall@5": 0.02}
    assert data["report"]["formats"] == ["table", "json"]


def test_mini_yaml_strips_inline_comments_and_quotes():
    data = _mini_yaml('url: "http://x/y"  # 备注\nflag: true\nnothing: ~\n')
    assert data["url"] == "http://x/y"
    assert data["flag"] is True
    assert data["nothing"] is None


def test_mini_yaml_reports_position():
    with pytest.raises(ConfigError) as excinfo:
        _mini_yaml("a: 1\n  - b\n")
    assert "第 2 行" in str(excinfo.value)


def test_example_config_parses():
    """仓库里的示例配置必须永远能跑。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "examples", "rag-gate.yaml"))
    assert cfg.metrics == ["recall@5", "hit_rate@5", "mrr", "ndcg@5"]
    assert cfg.retriever.type == "python"
    assert os.path.exists(cfg.dataset_path)
