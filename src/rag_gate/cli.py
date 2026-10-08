"""命令行入口：run / baseline / compare / drift。

    rag-gate run        跑一次，出报告（不判定）
    rag-gate baseline   把当前结果存成基线（人工确认后提交）
    rag-gate compare    和基线比对并执行门禁（CI 用，退化则 exit 1）
    rag-gate drift      只看相似度分布漂移
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Sequence
from rag_gate import __version__
from rag_gate.adapters import AdapterError, retrieve_all
from rag_gate.baseline import (
    BaselineError,
    compare,
    dataset_digest,
    environment,
    load_baseline,
    make_baseline,
    save_baseline,
)
from rag_gate.config import Config, ConfigError, load_dataset
from rag_gate.gate import EXIT_ERROR, EXIT_MEANING, EXIT_OK, GateResult, verify
from rag_gate.metrics import (
    MetricError,
    collect_top_scores,
    compare_distributions,
    evaluate_all,
    score_distribution,
)
from rag_gate.report import render, write_reports

PROG = "rag-gate"


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="把 RAG 检索质量变成 CI 门禁：和基线比，跌太多就 fail。",
        epilog="退码：0 通过 / 1 指标退化 / 2 配置或取数错误",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<run|baseline|compare|drift>")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-c", "--config", default="rag-gate.yaml", help="配置文件（默认 rag-gate.yaml）")
        p.add_argument("--baseline", default=None, help="基线文件路径（默认读配置里的 gate.baseline）")
        p.add_argument("--format", default="table", help="输出格式：table | markdown | json（默认 table）")
        p.add_argument("-o", "--output-dir", default=None, help="报告输出目录（默认读配置）")

    p_run = sub.add_parser("run", help="跑一次并出报告，不做门禁判定")
    common(p_run)
    p_run.add_argument("--no-write", action="store_true", help="不往磁盘写报告")

    p_base = sub.add_parser("baseline", help="把当前结果存成基线")
    common(p_base)
    p_base.add_argument("--notes", default="", help="备注，会写进基线文件（例如「v1.2 索引重建后」）")
    p_base.add_argument("--write-reports", action="store_true", help="顺便把本次报告也写下来")

    p_cmp = sub.add_parser("compare", help="和基线比对并执行门禁（CI 用）")
    common(p_cmp)
    p_cmp.add_argument("--max-drop", type=float, default=None, help="全局最大跌幅，覆盖配置里的逐指标阈值")
    p_cmp.add_argument(
        "--skip", action="append", default=[], metavar="METRIC",
        help="本次放行某个指标（可重复），仍会打印在报告里",
    )
    p_cmp.add_argument("--fail-on-drift", action="store_true", help="相似度分布漂移也直接判失败")
    p_cmp.add_argument("--no-write", action="store_true", help="不往磁盘写报告")

    p_drift = sub.add_parser("drift", help="只看相似度分布漂移")
    common(p_drift)
    p_drift.add_argument("--no-write", action="store_true", help="不往磁盘写报告")
    return parser


# ---------------------------------------------------------------------------
# 公共流程
# ---------------------------------------------------------------------------


class RunOutcome:
    def __init__(self, config: Config, rows: List[Dict[str, Any]], results: Dict[str, Any]):
        self.config = config
        self.rows = rows
        self.results = results
        self.metrics, self.per_query = evaluate_all(rows, results, config.metrics)
        self.digest = dataset_digest(rows)
        self.top_scores = collect_top_scores(rows, results)
        self.similarity = score_distribution(self.top_scores)


def _load(args: argparse.Namespace) -> RunOutcome:
    config = Config(args.config)
    rows = load_dataset(config.dataset_path)
    results = retrieve_all(config.retriever, rows)
    return RunOutcome(config, rows, results)


def _base_report(outcome: RunOutcome, args: argparse.Namespace) -> Dict[str, Any]:
    config = outcome.config
    return {
        "tool": PROG,
        "version": __version__,
        "command": args.command,
        "config_path": config.path,
        "dataset_path": config.dataset_path,
        "dataset_digest": outcome.digest,
        "dataset_size": len(outcome.rows),
        "adapter": config.retriever.type,
        "top_k": config.retriever.top_k,
        "environment": environment(),
        "metrics": {k: round(v, 6) for k, v in outcome.metrics.items()},
        "similarity": outcome.similarity,
        "per_query": outcome.per_query,
    }


def _emit(report: Dict[str, Any], args: argparse.Namespace, stem: str,
          config: Optional[Config] = None) -> None:
    fmt = (args.format or "table").lower()
    print(render(fmt, report))
    if getattr(args, "no_write", False):
        return
    formats = config.formats if config else ["markdown", "json"]
    output_dir = getattr(args, "output_dir", None) or (config.output_dir if config else ".rag-gate")
    written = write_reports(report, output_dir, formats, stem=stem)
    # 写盘提示走 stderr：stdout 要留给报告本身（--format json 时要能被 jq 直接吃）
    for path in written:
        print(f"报告已写入：{path}", file=sys.stderr)


def _drift_block(current: Dict[str, float], baseline: Optional[Dict[str, Any]],
                 thresholds: Dict[str, float]) -> Dict[str, Any]:
    block: Dict[str, Any] = {"after": current, "before": {}, "drifted": False, "reason": ""}
    if not current:
        block["reason"] = "本次没有拿到相似度分数（adapter 没回 scores），drift 需要分数才能算"
        return block
    if not baseline or not (baseline.get("drift") or {}).get("after"):
        block["reason"] = "基线里没有相似度分布（旧基线或没开 drift），本次只记录"
        return block
    before = baseline["drift"]["after"]
    block["before"] = before
    comparison = compare_distributions(before, current, thresholds)
    block.update(comparison)
    block["after"] = current
    return block


# ---------------------------------------------------------------------------
# 四个子命令
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    outcome = _load(args)
    report = _base_report(outcome, args)
    report["baseline"] = None
    report["comparison"] = False
    report["exit_code"] = EXIT_OK
    _emit(report, args, stem="run", config=outcome.config)
    return EXIT_OK


def cmd_baseline(args: argparse.Namespace) -> int:
    outcome = _load(args)
    config = outcome.config
    path = config.resolve_baseline(args.baseline)
    previous = None
    if os.path.exists(path):
        try:
            previous = load_baseline(path)
        except BaselineError:
            previous = None
    baseline = make_baseline(
        outcome.metrics,
        outcome.per_query,
        outcome.digest,
        notes=args.notes,
        drift={"after": outcome.similarity} if outcome.similarity else {},
    )
    save_baseline(path, baseline)

    report = _base_report(outcome, args)
    report["baseline_path"] = path
    report["baseline"] = previous
    report["comparison"] = False
    report["exit_code"] = EXIT_OK

    print(f"基线已写入：{path}")
    print(f"  golden set {len(outcome.rows)} 条（digest {outcome.digest}）")
    for name, value in sorted(outcome.metrics.items()):
        old = (previous or {}).get("metrics", {}).get(name)
        if isinstance(old, (int, float)):
            print(f"  {name:<14}{old:.4f} → {value:.4f}  ({value - old:+.4f})")
        else:
            print(f"  {name:<14}{value:.4f}")
    print("\n记得把它提交进仓库，否则 CI 里没有历史可比。")
    if getattr(args, "write_reports", False):
        _emit(report, args, stem="baseline", config=config)
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    outcome = _load(args)
    config = outcome.config
    baseline_path = config.resolve_baseline(args.baseline)
    baseline = load_baseline(baseline_path)

    max_drop = dict(config.max_drop)
    if args.max_drop is not None:
        max_drop = {name: args.max_drop for name in config.metrics}
    comparison = compare(outcome.metrics, baseline, max_drop, config.min_absolute)

    dataset_changed = bool(baseline.get("dataset_digest")) and baseline["dataset_digest"] != outcome.digest
    drift = _drift_block(
        outcome.similarity,
        baseline,
        {**config.drift, "std_ratio": config.drift.get("std_ratio", 1.5)},
    )
    verdict: GateResult = verify(
        comparison,
        drift=drift,
        fail_on_drift=args.fail_on_drift,
        dataset_changed=dataset_changed,
        fail_on_dataset_change=bool(config.gate.get("fail_on_dataset_change")),
        only_metrics=args.skip,
    )

    report = _base_report(outcome, args)
    report["baseline_path"] = baseline_path
    report["baseline"] = baseline
    report["comparison"] = True
    report.update(comparison)
    report["drift"] = drift
    report["dataset_changed"] = dataset_changed
    report["failures"] = verdict.failures
    report["warnings"] = verdict.warnings
    report["exit_code"] = verdict.exit_code
    _emit(report, args, stem="compare", config=config)

    # 结论也走 stderr：stdout 只留报告本身
    print(file=sys.stderr)
    if verdict.exit_code == EXIT_OK:
        print("门禁通过（exit 0）：没有指标跌破阈值。", file=sys.stderr)
    else:
        print(f"门禁失败（exit {verdict.exit_code} · {EXIT_MEANING[verdict.exit_code]}）", file=sys.stderr)
        for item in verdict.failures:
            print(f"  · {item}", file=sys.stderr)
    return verdict.exit_code


def cmd_drift(args: argparse.Namespace) -> int:
    outcome = _load(args)
    config = outcome.config
    baseline_path = config.resolve_baseline(args.baseline)
    baseline = None
    if os.path.exists(baseline_path):
        try:
            baseline = load_baseline(baseline_path)
        except BaselineError as exc:
            print(f"提示：{exc}", file=sys.stderr)

    thresholds = {**config.drift, "std_ratio": config.drift.get("std_ratio", 1.5)}
    drift = _drift_block(outcome.similarity, baseline, thresholds)

    report = _base_report(outcome, args)
    report["baseline_path"] = baseline_path
    report["baseline"] = baseline
    report["comparison"] = False
    report["drift"] = drift
    report["drift_only"] = True

    if not outcome.similarity:
        report["exit_code"] = EXIT_OK
        report["warnings"] = [
            "这次没拿到相似度分数。让 adapter 一起回 scores（或 HTTP 响应的 score 路径填进 "
            "retriever.response.scores），drift 才有东西可比。"
        ]
        _emit(report, args, stem="drift", config=config)
        print("\ndrift 需要一个分数：当前 adapter 没提供，跳过判定（exit 0）", file=sys.stderr)
        return EXIT_OK

    exit_code = EXIT_OK
    if drift.get("drifted"):
        exit_code = 1
    report["exit_code"] = exit_code
    report["failures"] = [] if exit_code == EXIT_OK else [drift.get("reason", "相似度分布漂移")]
    _emit(report, args, stem="drift", config=config)

    print(file=sys.stderr)
    if exit_code == EXIT_OK:
        print("相似度分布稳定（exit 0）" if drift.get("before") else "已记录本次分布（没有基线可比，exit 0）",
              file=sys.stderr)
    else:
        print(f"检测到漂移（exit 1）：{drift.get('reason')}", file=sys.stderr)
    return exit_code


COMMANDS = {
    "run": cmd_run,
    "baseline": cmd_baseline,
    "compare": cmd_compare,
    "drift": cmd_drift,
}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        parser.print_help()
        return EXIT_ERROR
    if argv[0] in ("-h", "--help", "--version"):
        parser.parse_args(argv)
        return EXIT_OK
    if argv[0] not in COMMANDS:
        print(
            f"{PROG}: 不认识子命令 `{argv[0]}`。可用：run / baseline / compare / drift",
            file=sys.stderr,
        )
        return EXIT_ERROR
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (ConfigError, BaselineError, AdapterError, MetricError) as exc:
        print(f"\n{PROG}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print(f"\n{PROG}: 已中断", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
