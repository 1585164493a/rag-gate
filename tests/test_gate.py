"""门禁判定：三种退码必须分得清清楚楚。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rag_gate.baseline import compare, dataset_digest, load_baseline, make_baseline, save_baseline  # noqa: E402
from rag_gate.gate import EXIT_OK, EXIT_REGRESSION, verify  # noqa: E402


def baseline_with(metrics, digest="abc"):
    return make_baseline(metrics, [], digest)


def test_compare_marks_drop_beyond_threshold():
    result = compare({"recall@5": 0.70}, baseline_with({"recall@5": 0.90}), {"recall@5": 0.05}, {})
    assert result["violations"][0]["metric"] == "recall@5"
    assert result["rows"][0]["drop"] == pytest.approx(0.20)


def test_compare_allows_small_drop_and_improvement():
    ok = compare({"recall@5": 0.86}, baseline_with({"recall@5": 0.90}), {"recall@5": 0.05}, {})
    assert ok["violations"] == []
    better = compare({"recall@5": 0.95}, baseline_with({"recall@5": 0.90}), {"recall@5": 0.05}, {})
    assert better["rows"][0]["delta"] == pytest.approx(0.05)


def test_compare_respects_min_absolute():
    result = compare({"recall@5": 0.55}, baseline_with({"recall@5": 0.56}), {}, {"recall@5": 0.60})
    assert result["violations"][0]["metric"] == "recall@5"
    assert "绝对下限" in result["violations"][0]["reasons"][0]


def test_compare_marks_new_and_missing_metrics():
    result = compare({"mrr": 0.5, "ndcg@5": 0.5}, baseline_with({"mrr": 0.5, "recall@5": 0.9}), {}, {})
    statuses = {row["metric"]: row["status"] for row in result["rows"]}
    assert statuses["ndcg@5"] == "new"
    assert statuses["recall@5"] == "missing"
    assert result["violations"] == []


def test_verify_passes_when_no_violation():
    result = verify(compare({"mrr": 0.9}, baseline_with({"mrr": 0.9}), {"mrr": 0.05}, {}))
    assert result.exit_code == EXIT_OK
    assert result.failures == []


def test_verify_fails_on_violation():
    result = verify(compare({"mrr": 0.5}, baseline_with({"mrr": 0.9}), {"mrr": 0.05}, {}))
    assert result.exit_code == EXIT_REGRESSION
    assert "mrr" in result.failures[0]


def test_verify_skip_downgrades_to_warning():
    result = verify(
        compare({"mrr": 0.5}, baseline_with({"mrr": 0.9}), {"mrr": 0.05}, {}),
        only_metrics=["mrr"],
    )
    assert result.exit_code == EXIT_OK
    assert result.warnings and "放行" in result.warnings[0]


def test_verify_dataset_change_is_warning_by_default():
    quiet = verify(compare({"mrr": 0.9}, baseline_with({"mrr": 0.9}), {}, {}), dataset_changed=True)
    assert quiet.exit_code == EXIT_OK
    assert "golden set 变了" in quiet.warnings[0]

    strict = verify(
        compare({"mrr": 0.9}, baseline_with({"mrr": 0.9}), {}, {}),
        dataset_changed=True,
        fail_on_dataset_change=True,
    )
    assert strict.exit_code == EXIT_REGRESSION


def test_verify_drift_is_warning_unless_strict():
    drifted = {"drifted": True, "reason": "分布下移"}
    relaxed = verify(compare({"mrr": 0.9}, baseline_with({"mrr": 0.9}), {}, {}), drift=drifted)
    assert relaxed.exit_code == EXIT_OK
    strict = verify(
        compare({"mrr": 0.9}, baseline_with({"mrr": 0.9}), {}, {}),
        drift=drifted,
        fail_on_drift=True,
    )
    assert strict.exit_code == EXIT_REGRESSION


# ---------------------------------------------------------------------------
# 基线文件本身
# ---------------------------------------------------------------------------


def test_baseline_roundtrip(tmp_path):
    path = str(tmp_path / "baseline.json")
    original = make_baseline({"recall@5": 0.9123456}, [{"id": "q1"}], "digest1", notes="v1")
    save_baseline(path, original)
    loaded = load_baseline(path)
    assert loaded["metrics"]["recall@5"] == 0.912346  # 落了 6 位小数
    assert loaded["notes"] == "v1"


def test_baseline_missing_file_is_actionable(tmp_path):
    from rag_gate.baseline import BaselineError

    try:
        load_baseline(str(tmp_path / "nope.json"))
    except BaselineError as exc:
        assert "rag-gate baseline" in str(exc)
    else:
        raise AssertionError("应该报错")


def test_baseline_rejects_future_format(tmp_path):
    from rag_gate.baseline import BaselineError

    path = tmp_path / "baseline.json"
    path.write_text('{"format": 99, "metrics": {}}', encoding="utf-8")
    try:
        load_baseline(str(path))
    except BaselineError as exc:
        assert "更新版本" in str(exc)
    else:
        raise AssertionError("应该报错")


def test_dataset_digest_changes_with_content():
    a = [{"id": "q1", "question": "问", "expected_doc_ids": ["a"]}]
    b = [{"id": "q1", "question": "问", "expected_doc_ids": ["b"]}]
    assert dataset_digest(a) != dataset_digest(b)
    assert dataset_digest(a) == dataset_digest(list(a))
