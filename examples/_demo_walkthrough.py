"""开发用：端到端跑一遍 demo（不属于发布内容）。

用法：python examples/_demo_walkthrough.py
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
CONFIG = os.path.join(ROOT, "examples", "rag-gate.yaml")

ENV = {**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8"}


def run(step, args, demo_mode="healthy", expect=None):
    env = {**ENV, "RAG_GATE_DEMO": demo_mode}
    print(f"\n{'=' * 70}\n[{step}] RAG_GATE_DEMO={demo_mode}  rag-gate {' '.join(args)}\n{'=' * 70}")
    proc = subprocess.run(
        [sys.executable, "-m", "rag_gate.cli", *args], env=env, cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print("--- stderr ---")
        print(proc.stderr.rstrip())
    print(f"-> exit {proc.returncode}" + (f"（期望 {expect}）" if expect is not None else ""))
    if expect is not None and proc.returncode != expect:
        raise SystemExit(f"!! 第 {step} 步退码不对：拿到 {proc.returncode}，期望 {expect}")


run("1 建基线", ["baseline", "--config", CONFIG, "--notes", "demo baseline"])
run("2 什么都没改", ["compare", "--config", CONFIG, "--no-write"], expect=0)
run("3 换 Embedding 没同步", ["compare", "--config", CONFIG, "--no-write"],
    demo_mode="broken-model", expect=1)
run("4 只看相似度漂移", ["drift", "--config", CONFIG, "--no-write"],
    demo_mode="broken-model", expect=1)
run("5 写入重试没幂等", ["compare", "--config", CONFIG, "--no-write"],
    demo_mode="dup-write", expect=1)
run("6 检索只返回 2 条", ["compare", "--config", CONFIG, "--no-write"],
    demo_mode="shrink", expect=1)
run("7 配置写错（不该是 1）", ["compare", "--config", os.path.join(ROOT, "examples", "nope.yaml")],
    expect=2)
print("\n全部步骤符合预期。")
