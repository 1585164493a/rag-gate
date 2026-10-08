"""报告渲染 + 示例能否真的跑起来。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rag_gate.report import render, render_markdown, render_table, write_reports  # noqa: E402

REPORT = {
    "tool": "rag-gate",
    "version": "0.1.0",
    "command": "compare",
    "config_path": "rag-gate.yaml",
    "dataset_path": "golden.jsonl",
    "dataset_size": 16,
    "adapter": "python",
    "metrics": {"recall@5": 0.75, "mrr": 0.61},
    "similarity": {"mean": 0.62, "p50": 0.63},
    "baseline": {"created_at": "2026-10-08T10:00:00+08:00"},
    "comparison": True,
    "rows": [
        {
            "metric": "recall@5", "baseline": 0.90, "current": 0.75, "delta": -0.15,
            "drop": 0.15, "max_drop": 0.05, "min_absolute": 0.60, "status": "fail",
            "reasons": ["相对基线跌了 0.1500，超过允许的 0.05"],
        },
        {
            "metric": "mrr", "baseline": 0.61, "current": 0.61, "delta": 0.0,
            "drop": 0.0, "max_drop": 0.03, "min_absolute": None, "status": "ok", "reasons": [],
        },
        {
            "metric": "ndcg@5", "baseline": None, "current": 0.5, "delta": None,
            "drop": None, "max_drop": None, "min_absolute": None, "status": "new", "reasons": [],
        },
    ],
    "drift": {
        "before": {"mean": 0.90, "p50": 0.90, "p90": 0.95},
        "after": {"mean": 0.62, "p50": 0.63, "p90": 0.80},
        "drifted": True,
        "reason": "分布整体大幅下移：很像写入侧/读取侧的 Embedding 不一致",
    },
    "failures": ["`recall@5`：相对基线跌了 0.1500，超过允许的 0.05"],
    "warnings": ["golden set 变了（dataset_digest 对不上基线）"],
    "exit_code": 1,
}


def test_table_contains_every_row_and_verdict():
    out = render_table(REPORT)
    assert "recall@5" in out and "mrr" in out and "ndcg@5" in out
    assert "FAIL" in out
    assert "检索质量退化（exit 1）" in out
    assert "Embedding" in out


def test_table_without_baseline_lists_metrics():
    report = {**REPORT, "comparison": False, "rows": None, "drift": {}}
    out = render_table(report)
    assert "recall@5" in out and "0.7500" in out
    assert "没有基线" in out


def test_markdown_has_table_and_reasons():
    out = render_markdown(REPORT)
    assert out.startswith("## rag-gate")
    assert "❌ 退化" in out
    assert "| `recall@5` |" in out
    assert "### 为什么红" in out
    assert "### 提醒" in out


def test_markdown_passing_report():
    report = {**REPORT, "exit_code": 0, "failures": [], "warnings": []}
    out = render_markdown(report)
    assert "✅ 通过" in out
    assert "### 为什么红" not in out


def test_render_json_is_parseable():
    payload = json.loads(render("json", REPORT))
    assert payload["exit_code"] == 1
    assert payload["rows"][0]["metric"] == "recall@5"


def test_render_defaults_to_table():
    assert render("table", REPORT) == render_table(REPORT)
    assert render("md", REPORT) == render_markdown(REPORT)


def test_write_reports_all_formats(tmp_path):
    written = write_reports(REPORT, str(tmp_path), ["table", "markdown", "json"], stem="compare")
    names = sorted(os.path.basename(path) for path in written)
    assert names == ["compare.json", "compare.md", "compare.txt"]
    for path in written:
        assert os.path.getsize(path) > 0


def test_write_reports_no_formats(tmp_path):
    assert write_reports(REPORT, str(tmp_path), [], stem="x") == []


# ---------------------------------------------------------------------------
# 示例：README 承诺「零依赖 30 秒跑通」，这里守住这个承诺
# ---------------------------------------------------------------------------


def test_example_config_and_retriever_work():
    from rag_gate.adapters import retrieve_all
    from rag_gate.config import Config, load_dataset
    from rag_gate.metrics import evaluate_all

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "examples", "rag-gate.yaml"))
    rows = load_dataset(cfg.dataset_path)
    results = retrieve_all(cfg.retriever, rows)
    scores, _ = evaluate_all(rows, results, cfg.metrics)
    # 示例必须是「建得好」的状态，否则用户第一次跑就看到红
    assert scores["recall@5"] > 0.9
    assert scores["hit_rate@5"] > 0.9
    assert scores["mrr"] > 0.9


def test_example_broken_model_really_degrades():
    from rag_gate.adapters import retrieve_all
    from rag_gate.config import Config, load_dataset
    from rag_gate.metrics import collect_top_scores, evaluate_all, score_distribution

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "examples", "rag-gate.yaml"))
    rows = load_dataset(cfg.dataset_path)

    os.environ["RAG_GATE_DEMO"] = "broken-model"
    try:
        results = retrieve_all(cfg.retriever, rows)
    finally:
        os.environ.pop("RAG_GATE_DEMO", None)

    scores, _ = evaluate_all(rows, results, cfg.metrics)
    dist = score_distribution(collect_top_scores(rows, results))
    assert scores["recall@5"] < 0.7
    assert scores["mrr"] < 0.5
    assert dist["mean"] < 0.6  # 分数整体下移，drift 能抓到


def test_example_golden_set_is_valid():
    from rag_gate.config import load_dataset

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = load_dataset(os.path.join(root, "examples", "golden.example.jsonl"))
    assert len(rows) >= 10
    for row in rows:
        assert row["question"].strip()
        assert row["expected_doc_ids"]
