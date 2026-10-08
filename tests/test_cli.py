"""端到端：CLI 的三种退码是给 CI 用的契约，必须钉死。"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")

GOLDEN = "\n".join(
    [
        '{"id": "q1", "question": "向量库为什么会有重复数据", "expected_doc_ids": ["kb-001"]}',
        '{"id": "q2", "question": "换了 Embedding 检索不对怎么办", "expected_doc_ids": ["kb-003"]}',
        '{"id": "q3", "question": "长文档怎么切分", "expected_doc_ids": ["kb-005", "kb-006"]}',
        '{"id": "q4", "question": "检索效果怎么回归测试", "expected_doc_ids": ["kb-011"]}',
    ]
)

# 一个会根据 DEMO_MODE 环境变量「变坏」的假检索器
RETRIEVER = '''
import os

# 关键词匹配，改问法不影响召回（免得测试互相干扰）
KEYWORDS = [
    (["重复", "幂等"], ["kb-001", "kb-002"]),
    (["embedding", "embedding不对", "换"], ["kb-003"]),
    (["切分", "长文档"], ["kb-005", "kb-006"]),
    (["回归", "评测"], ["kb-011"]),
    (["为啥", "为什么"], ["kb-001", "kb-002"]),
]


def _answer(question):
    low = question.lower()
    for keys, docs in KEYWORDS:
        if any(key in low for key in keys):
            return docs
    return []


def retrieve(question, k=5):
    mode = os.environ.get("DEMO_MODE", "healthy")
    ids = _answer(question)
    if mode == "broken":
        ids = ["noise-1"]
        top = 0.45
    elif mode == "shrink":
        ids = ids[:1]
        top = 0.9
    else:
        top = 0.9
    return [{"doc_id": doc, "score": round(top - 0.05 * i, 4)} for i, doc in enumerate(ids[:k])]
'''

CONFIG = """
dataset: golden.jsonl
retriever:
  type: python
  target: ./retriever.py:retrieve
  top_k: 5
metrics: [recall@5, hit_rate@5, mrr, ndcg@5]
gate:
  baseline: .rag-gate/baseline.json
  max_drop:
    recall@5: 0.05
    hit_rate@5: 0.05
    mrr: 0.05
    ndcg@5: 0.05
  drift:
    mean_drop: 0.02
report:
  formats: [markdown, json]
  output_dir: .rag-gate
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "golden.jsonl").write_text(GOLDEN, encoding="utf-8")
    (tmp_path / "retriever.py").write_text(RETRIEVER, encoding="utf-8")
    (tmp_path / "rag-gate.yaml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def cli(project, *args, mode="healthy", expect=None):
    env = {**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8", "DEMO_MODE": mode}
    proc = subprocess.run(
        [sys.executable, "-m", "rag_gate.cli", *args],
        cwd=str(project), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if expect is not None:
        assert proc.returncode == expect, f"exit {proc.returncode} != {expect}\n{proc.stdout}\n{proc.stderr}"
    return proc


def test_run_writes_reports_and_exits_zero(project):
    proc = cli(project, "run", "--config", "rag-gate.yaml", expect=0)
    assert "recall@5" in proc.stdout
    assert (project / ".rag-gate" / "run.md").exists()
    assert (project / ".rag-gate" / "run.json").exists()
    payload = json.loads((project / ".rag-gate" / "run.json").read_text(encoding="utf-8"))
    assert payload["metrics"]["hit_rate@5"] == 1.0
    assert payload["dataset_size"] == 4


def test_compare_without_baseline_is_exit_2(project):
    proc = cli(project, "compare", "--config", "rag-gate.yaml", expect=2)
    assert "rag-gate baseline" in proc.stderr


def test_baseline_then_compare_unchanged_is_zero(project):
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    assert (project / ".rag-gate" / "baseline.json").exists()

    proc = cli(project, "compare", "--config", "rag-gate.yaml", expect=0)
    assert "门禁通过" in proc.stdout + proc.stderr
    assert "ok" in proc.stdout

    report = json.loads((project / ".rag-gate" / "compare.json").read_text(encoding="utf-8"))
    assert report["exit_code"] == 0
    assert report["failures"] == []


def test_degraded_retriever_is_exit_1(project):
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    proc = cli(project, "compare", "--config", "rag-gate.yaml", mode="broken", expect=1)
    assert "门禁失败" in proc.stdout + proc.stderr
    report = json.loads((project / ".rag-gate" / "compare.json").read_text(encoding="utf-8"))
    assert report["exit_code"] == 1
    assert any("recall@5" in item for item in report["failures"])
    # 相似度也整体下移了，drift 应该被标记
    assert report["drift"]["drifted"] is True


def test_shrink_is_exit_1_but_drift_stays_stable(project):
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    proc = cli(project, "compare", "--config", "rag-gate.yaml", mode="shrink", expect=1)
    report = json.loads((project / ".rag-gate" / "compare.json").read_text(encoding="utf-8"))
    assert report["failures"]
    assert report["drift"]["drifted"] is False  # 分数没变，只是条数少了


def test_skip_flag_downgrades_to_pass(project):
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    proc = cli(
        project, "compare", "--config", "rag-gate.yaml",
        "--skip", "recall@5", "--skip", "hit_rate@5", "--skip", "ndcg@5", "--skip", "mrr",
        mode="broken", expect=0,
    )
    assert "放行" in proc.stdout or "警告" in proc.stdout


def test_baseline_command_prints_diff_when_refreshing(project):
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    proc = cli(project, "baseline", "--config", "rag-gate.yaml", "--notes", "v2", expect=0)
    assert "→" in proc.stdout  # 刷新时能看到新旧对照
    baseline = json.loads((project / ".rag-gate" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["notes"] == "v2"


def test_dataset_change_is_warned_not_failed(project):
    """golden set 改了要警告（新旧分数不可比），但默认不因此判失败。"""
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    golden = project / "golden.jsonl"
    golden.write_text(
        golden.read_text(encoding="utf-8").replace("为什么会有重复数据", "为啥会重复"),
        encoding="utf-8",
    )
    proc = cli(project, "compare", "--config", "rag-gate.yaml", expect=0)
    report = json.loads((project / ".rag-gate" / "compare.json").read_text(encoding="utf-8"))
    assert report["dataset_changed"] is True
    assert any("golden set 变了" in item for item in report["warnings"])
    assert report["failures"] == []


def test_dataset_change_can_be_made_fatal(project):
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    config = project / "rag-gate.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "  drift:", "  fail_on_dataset_change: true\n  drift:"
        ),
        encoding="utf-8",
    )
    golden = project / "golden.jsonl"
    golden.write_text(
        golden.read_text(encoding="utf-8").replace("为什么会有重复数据", "为啥会重复"),
        encoding="utf-8",
    )
    cli(project, "compare", "--config", "rag-gate.yaml", expect=1)


def test_drift_command_exit_codes(project):
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    cli(project, "drift", "--config", "rag-gate.yaml", expect=0)
    proc = cli(project, "drift", "--config", "rag-gate.yaml", mode="broken", expect=1)
    assert "漂移" in proc.stdout + proc.stderr


def test_json_output_format_is_machine_readable(project):
    """stdout 必须只有 JSON（提示都走 stderr），否则没法喂给 jq / 别的工具。"""
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    proc = cli(project, "compare", "--config", "rag-gate.yaml", "--format", "json", expect=0)
    payload = json.loads(proc.stdout.strip())
    assert payload["exit_code"] == 0
    assert payload["rows"][0]["metric"] in {"recall@5", "hit_rate@5", "mrr", "ndcg@5"}
    assert "报告已写入" in proc.stderr
    assert "门禁通过" in proc.stderr


def test_markdown_format_has_table(project):
    cli(project, "baseline", "--config", "rag-gate.yaml", expect=0)
    proc = cli(project, "compare", "--config", "rag-gate.yaml", "--format", "markdown", "--no-write", expect=0)
    assert "| 指标 | 基线 | 本次 |" in proc.stdout


def test_bad_config_is_exit_2(project):
    (project / "broken.yaml").write_text("dataset: golden.jsonl\n", encoding="utf-8")
    proc = cli(project, "compare", "--config", "broken.yaml", expect=2)
    assert "retriever" in proc.stderr


def test_adapter_failure_is_exit_2_not_1(project):
    """取不到数必须是 2，不能混成「检索退化」。"""
    (project / "retriever.py").write_text("def retrieve(q, k=5):\n    raise RuntimeError('服务挂了')\n", encoding="utf-8")
    proc = cli(project, "compare", "--config", "rag-gate.yaml", expect=2)
    assert "取数" in proc.stderr


def test_unknown_command_and_missing_config(project):
    proc = cli(project, "对比", expect=2)
    assert "不认识子命令" in proc.stderr
    proc = cli(project, "compare", "--config", "nope.yaml", expect=2)
    assert "找不到配置文件" in proc.stderr
