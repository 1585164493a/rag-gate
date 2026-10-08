"""一个零依赖的假检索器 —— 用来 30 秒跑通 rag-gate。

它是**故意做成假的**：不联数据库、不装依赖、结果确定。
真实的接入方式见 README（把 target 换成你自己的函数即可）。

它同时提供四个「开关」，方便你在本地复现四种典型事故：

    $env:RAG_GATE_DEMO = "healthy"        # 默认：一切都好
    $env:RAG_GATE_DEMO = "broken-model"   # 换 Embedding 没同步：结果乱、分数整体下移
    $env:RAG_GATE_DEMO = "dup-write"      # 写入重试没幂等：重复副本挤掉正确答案
    $env:RAG_GATE_DEMO = "shrink"         # 只返回 2 条：给的数据变少

用法::

    from dummy_retriever import retrieve
    hits = retrieve("向量库重复数据怎么排查", k=5)
    # -> [{"doc_id": "kb-002", "score": 0.87}, ...]
"""

from __future__ import annotations

import math
import os

# doc_id -> 正文（模拟「一个已经建好的索引」）
_DOCS = {
    "kb-001": (
        "写入侧幂等：同一条数据被写了两次。上游任务重试时没有去重，"
        "同一条记录被写入两遍，索引里就多出了重复数据。"
    ),
    "kb-002": (
        "重复文档的定位与清理：用内容哈希分组找出重复，先定位来源任务，再决定删哪一份。"
    ),
    "kb-003": (
        "检索侧与写入侧模型不一致的典型症状：查询照样返回结果，但答案完全不对，"
        "因为查询用了旧模型的向量空间。"
    ),
    "kb-004": (
        "Embedding 升级要双写双读再灰度：新旧两个版本同时可查，确认无差异后再切读。"
    ),
    "kb-005": (
        "长文档切分策略：优先按语义边界切，块与块之间保留重叠，避免把一句话切断。"
    ),
    "kb-006": (
        "chunk 大小与重叠的取舍：块越小上下文越少，几百 token 配 10% 到 20% 重叠比较稳。"
    ),
    "kb-007": (
        "混合检索：BM25 负责关键词，向量负责语义，用 RRF 做排名融合，不必强行统一分数尺度。"
    ),
    "kb-008": (
        "答案质量评测：回答不准时先固定评测集，每次改动都跑一次，最好直接进 CI。"
    ),
    "kb-009": (
        "rerank 之后要不要保留原始分数：要保留，调参和线上排查都靠它。"
    ),
    "kb-010": (
        "重排特征不止相似度：时间、热度、业务权重都可以进模型，让排序更贴合业务。"
    ),
    "kb-011": (
        "检索回归测试：建固定评测集，和基线比，退化了就报错，不让它合进主干。"
    ),
    "kb-012": (
        "召回条数 k 与上下文预算：先多召回一些给重排，再用上下文预算决定最终喂几条。"
    ),
    "kb-013": (
        "检索延迟升高的排查顺序：先看分位延迟，再查索引分片与缓存命中。"
    ),
    "kb-014": (
        "缓存集中失效会打挂检索：同一时刻大量 key 过期，查询量突增直接压垮服务。"
    ),
    "kb-015": (
        "检索与写入的重试策略：要重试，但必须带退避和次数上限，写入侧要幂等。"
    ),
}

# 只在「索引坏了」的场景里冒出来的噪声文档
_NOISE = {
    "noise-1": "产品更新日志与版本发布说明，和检索质量无关。",
    "noise-2": "团队周会纪要：排期、人力、风险，和检索质量无关。",
    "noise-3": "运维手册：机器巡检、磁盘告警阈值，和检索质量无关。",
}

TOPK_BY_MODE = {"shrink": 2}


def _tokenize(text: str) -> set:
    """英文按词切，中文按单字切 —— 只为演示，真实项目里这层是 Embedding。"""
    tokens = set()
    word = []
    for ch in text.lower():
        if ch.isalnum() and ord(ch) < 0x2E80:
            word.append(ch)
        else:
            if word:
                tokens.add("".join(word))
                word = []
            if ch.strip() and ord(ch) >= 0x2E80:
                tokens.add(ch)
    if word:
        tokens.add("".join(word))
    return tokens


def _similarity(question: str, text: str) -> float:
    """重叠度 / sqrt(文档长度)，避免长文档天然占便宜。"""
    q = _tokenize(question)
    d = _tokenize(text)
    if not q or not d:
        return 0.0
    return len(q & d) / math.sqrt(len(d))


def _rank(question: str, docs: dict) -> list:
    scored = [
        {"doc_id": doc_id, "raw": _similarity(question, text)}
        for doc_id, text in docs.items()
    ]
    scored.sort(key=lambda item: (-item["raw"], item["doc_id"]))
    return scored


def retrieve(question: str, k: int = 5):
    """返回 ``[{"doc_id": ..., "score": ...}, ...]``，按相似度降序。"""
    mode = os.environ.get("RAG_GATE_DEMO", "healthy")
    scored = _rank(question, _DOCS)

    if mode == "broken-model":
        # 换模型没同步：向量空间对不上，一批「不相关的文档」拿到高分，
        # 真正相关的被挪到第 3 名、第 6 名，分数整体下移到 0.4 附近（分布漂移）。
        import random

        rng = random.Random(question)
        pool = _rank(question, {**_DOCS, **_NOISE})
        relevant = [item for item in pool if item["doc_id"].startswith("kb-")][:4]
        noise = [item for item in pool if item["doc_id"].startswith("noise-")][:3]
        rng.shuffle(relevant)
        interleaved = [noise[0], noise[1], relevant[0], noise[2], relevant[1]]
        return [
            {"doc_id": item["doc_id"], "score": round(0.46 - 0.03 * index, 4)}
            for index, item in enumerate(interleaved[:k])
        ]

    if mode == "dup-write":
        # 写入重试没幂等：同一批内容重复入库，重复副本把正确答案挤到后面
        duplicates = [
            {"doc_id": f"{item['doc_id']}-copy{index}", "raw": 0.8}
            for index, item in enumerate(scored[:2], start=1)
        ]
        merged = duplicates + scored
        top = max(1.0, merged[0]["raw"])
        return [
            {"doc_id": item["doc_id"], "score": round(min(0.95, 0.5 + 0.45 * (item["raw"] / top)), 4)}
            for item in merged[:k]
        ]

    limit = TOPK_BY_MODE.get(mode, k)
    top = max(1e-9, scored[0]["raw"])
    return [
        {"doc_id": item["doc_id"], "score": round(min(0.97, 0.45 + 0.52 * (item["raw"] / top)), 4)}
        for item in scored[:limit]
    ]


if __name__ == "__main__":  # 手动看一眼：python dummy_retriever.py "向量库重复数据"
    import json
    import sys

    query = " ".join(sys.argv[1:]) or "向量数据库里为什么会出现重复数据？"
    print(json.dumps(retrieve(query, 5), ensure_ascii=False, indent=2))
