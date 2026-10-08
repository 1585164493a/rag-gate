"""rag-gate · 把 RAG 检索质量变成 CI 门禁。

核心思想：门禁比的是「相对基线允许的最大跌幅」（max_drop），
而不是一个凭感觉定的绝对分数。
"""

__version__ = "0.1.0"

from rag_gate.metrics import MetricError, evaluate_all, parse_metric

__all__ = ["__version__", "MetricError", "evaluate_all", "parse_metric"]
