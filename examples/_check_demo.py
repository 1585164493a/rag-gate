"""开发用：检查示例检索器在四种模式下的指标（不属于发布内容）。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapters"))
import dummy_retriever as d  # noqa: E402

golden = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.example.jsonl")
rows = [json.loads(line) for line in open(golden, encoding="utf-8") if line.strip()]
total_expected = sum(len(r["expected_doc_ids"]) for r in rows)

for mode in ["healthy", "broken-model", "dup-write", "shrink"]:
    os.environ["RAG_GATE_DEMO"] = mode
    hit = 0
    rr = 0.0
    found = 0
    for row in rows:
        ids = [h["doc_id"] for h in d.retrieve(row["question"], 5)]
        expected = set(row["expected_doc_ids"])
        found += len(expected & set(ids))
        if expected & set(ids):
            hit += 1
        for rank, doc_id in enumerate(ids, start=1):
            if doc_id in expected:
                rr += 1.0 / rank
                break
    n = len(rows)
    print(
        f"{mode:14} hit_rate@5={hit / n:.3f}  mrr={rr / n:.3f}  "
        f"recall@5={found / total_expected:.3f}"
    )
