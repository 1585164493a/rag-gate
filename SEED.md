# SEED · rag-gate —— 项目设计种子

> 这份文档是给**新会话**用的交接说明。新会话没有本会话的上下文，
> 所以这里把"做什么、为什么、怎么设计、怎么发布"一次写全。
> 用法：新会话里说「按 D:\pythoncode\rag-gate\SEED.md 开始做」。

---

## 0. 一句话定位

**把「检索质量」变成 CI 里的一条门禁 —— 像跑单测一样跑 RAG 评测，退化了就不许合并。**

```
改了切分 / 换了 Embedding / 调了权重  →  rag-gate compare  →  掉太多就 fail
```

---

## 1. 为什么这个能火（README 的开篇故事，是真的）

> 我们上周换了个 Embedding 模型。**维度一样、代码没报错、延迟还降了。**
> 三天后客户在群里投诉「回答完全不对」。
>
> 查下来：**写入侧换了模型，读取侧不知道** —— 向量空间对不上，检索结果全乱。
> 更糟的是三件事：
> - **没有任何门禁拦住它**（换模型不用过任何检查）
> - **没有任何监控看见它**（延迟、错误率全绿）
> - **是客户先发现的**
>
> 这个项目就是那次事故的产物。

**这个故事的杀伤力在于**：它不是编的，而且**每个做 RAG 的人都可能踩**。

### 为什么现有工具没覆盖

| 工具 | 做什么 | 缺什么 |
|---|---|---|
| Ragas / DeepEval | 给一次结果**打分**，输出一个数字 | 不跟历史基线比，**不卡 CI** |
| promptfoo | 提示词层面的 A/B | 不针对**检索**质量 |
| 自己写脚本 | 一次性对比 | 不复用、不沉淀基线 |

**空位就在这里**：**"打分"已经有人做，"和历史基线的门禁"没人做。**

---

## 2. 名字

推荐 **`rag-gate`**（短、好搜、一眼知道是什么）。
备选：`retrieval-gate` / `ragbaseline` / `evalgate`。

**发布前先查**：GitHub 有没有同名仓库、PyPI 有没有同名包。

---

## 3. 明确不做什么（划边界，这决定项目会不会烂）

- ❌ **不做 RAG 框架**（不实现检索、不建索引）—— 我们只做**检验**
- ❌ **不绑定任何向量库 / 框架**（Milvus、Chroma、LangChain、LlamaIndex 都不依赖）
- ❌ **不做在线监控**（那是另一件事；这里只做**离线门禁**）
- ❌ **不要求跑起来一整套环境** —— 用户只需要给一个「问题 → 文档 id 列表」的函数

**第 2 条是最重要的设计约束**：适配靠**回调**，不靠集成。

---

## 4. 五个核心概念

| 概念 | 说明 |
|---|---|
| **Golden set** | 固定评测集：`{id, question, expected_doc_ids[], [expected_answer]}`。**一旦定下就不许随便改**（改了就没法和历史比） |
| **Adapter** | 用户提供一个取数方式：`python 函数` / `HTTP 端点` / `命令行`。**这是通用性的关键** |
| **Metrics** | `recall@k`、`hit_rate@k`、`mrr`、`ndcg@k`（可选接 Ragas 的答案分） |
| **Baseline** | 上一次通过的结果快照（`baseline.json`），**进版本控制** |
| **Gate** | 和 baseline 比，**任何指标跌幅超过阈值 → 非零退码 + 打印 diff 表** |

### ★ 最重要的设计决策：门禁比的是「相对退化」，不是「绝对分数」

绝对阈值极难定（不同数据集、不同业务要求差异巨大），**而且定错了项目就没人用**。
所以主判据是 **`max_drop`（相对基线允许的最大跌幅）**，`min_absolute` 只作为可选的兜底。

这个决策决定了这个工具能不能被别人的项目接进去。

---

## 5. 目录结构

```
rag-gate/
├── README.md                    # 第一屏：动图 + 一句话 + 一条命令
├── LICENSE                      # MIT
├── pyproject.toml               # pip install rag-gate
├── .gitignore
├── .github/workflows/gate.yml   # CI 示例（也是给用户抄的模板）
├── src/rag_gate/
│   ├── __init__.py
│   ├── cli.py                   # run / baseline / compare / drift
│   ├── config.py                # YAML 解析 + 校验（报错要人话）
│   ├── adapters.py              # python / http / command 三种取数
│   ├── metrics.py               # recall@k / hit_rate / mrr / ndcg
│   ├── baseline.py              # 读写基线、对比、生成 diff
│   ├── report.py                # table / markdown / json
│   └── gate.py                  # 阈值判定 + 退码
├── examples/
│   ├── rag-gate.yaml
│   ├── golden.example.jsonl
│   └── adapters/dummy_retriever.py   # 零依赖，30 秒能跑通
└── tests/
```

---

## 6. 配置格式（用户体验的核心，要反复打磨）

```yaml
# rag-gate.yaml
dataset: golden.jsonl            # 每行 {id, question, expected_doc_ids, [expected_answer]}

retriever:
  type: python                   # python | http | command
  target: my_app.retrieve        # python: import 路径；入参 query, k；出参 [doc_id, ...]
  top_k: 10
  # --- 另两种写法（注释给用户抄）---
  # type: http
  # url: http://localhost:8000/search
  # request:  {q: "{{question}}", k: 10}
  # response: {hits: "$.data[*].doc_id"}      # 极简 JSONPath
  # type: command
  # command: "python search.py --q {{question}} --k 10"
  # parse: jsonl

metrics:
  - recall@5
  - hit_rate@5
  - mrr
  - ndcg@10

gate:
  baseline: baseline.json
  max_drop:                      # 相对基线允许的最大跌幅（主判据）
    recall@5: 0.02
    mrr: 0.03
  min_absolute:                  # 可选兜底，防基线本身就很差
    recall@5: 0.60

report:
  formats: [table, markdown, json]
  output_dir: .rag-gate
```

---

## 7. CLI

```bash
rag-gate run        # 跑一次，出报告（不发门禁）
rag-gate baseline   # 把当前结果存成基线（人工确认后提交进仓库）
rag-gate compare    # 和基线比对并执行门禁（CI 用，退化则 exit 1）
rag-gate drift      # 只看相似度分布漂移（本次事故的信号）
```

**退码约定**（CI 靠这个）：`0` 通过 / `1` 指标退化 / `2` 配置或取数错误。
**三种退码要分开** —— 否则用户分不清"是我检索坏了"还是"我配置写错了"。

---

## 8. 指标定义（写进 README，避免歧义）

| 指标 | 定义 |
|---|---|
| `recall@k` | 期望文档中，出现在前 k 的比例（**多文档**场景的主指标） |
| `hit_rate@k` | 前 k 里**至少命中一个**的比例（**单文档**场景更直观） |
| `mrr` | 第一个命中位置的倒数均值 |
| `ndcg@k` | 带位置折扣的排序质量 |
| `similarity_drift`（可选） | top1 相似度的**分布变化**（均值/分位数），专门抓"向量空间变了" |

**为什么要有 `drift`**：前四个指标是"**结果对不对**"，drift 是"**过程变没变**"。
Embedding 那次事故里，**结果指标可能还没崩到阈值以下，但相似度分布已经整体下移了** —— 这是最早期的信号。

---

## 9. CI 集成（README 里的"杀手示例"）

```yaml
# .github/workflows/rag-gate.yml
name: RAG quality gate
on: [pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install rag-gate
      - run: rag-gate compare --config rag-gate.yaml
      # 退化时 exit 1 -> PR 直接红
```

**并把 markdown 报告贴成 PR 评论**（这个细节最能打动使用者）。

---

## 10. README 骨架（照这个顺序写）

1. **标题 + 一句话**：`rag-gate · 把 RAG 检索质量变成 CI 门禁`
2. **动图**：一次 `compare` 跑出退化、CI 变红（**没有动图的项目没人 star**）
3. **那个事故故事**（第 1 节，3-5 行）
4. **一条命令跑起来**（用 `examples/` 里的 dummy retriever，30 秒见效果）
5. **它和 Ragas / promptfoo 的区别**（表格）
6. **配置说明**
7. **指标定义**
8. **CI 示例**
9. **Roadmap / 贡献指南**

---

## 11. 发布 checklist

- [ ] `LICENSE`（MIT）+ `pyproject.toml` 元信息齐全
- [ ] **topics 打满**：`rag` `llm` `eval` `testing` `ci` `retrieval` `regression-testing`
- [ ] 仓库 **Public**，README 第一屏就有动图
- [ ] `examples/` 能**零依赖**跑通（这是转化率的关键）
- [ ] 前 20 个 issue 的响应速度 —— 决定口碑
- [ ] 发布当天**同步铺渠道**：V2EX / 掘金 / 即刻 / 少数派 / 小红书 + X / Reddit(r/LocalLLaMA) / HN
- [ ] 标题就写**结果**，别写技术：例如
      「换了个 Embedding，三天后客户才发现 —— 我做了个 CI 门禁防这个」

---

## 12. 可以从现有项目复用什么

| 现有资产 | 怎么用 |
|---|---|
| `Multimodal_RAG/evaluate/`、`evaluate_self.py` | Ragas 接入的参考（**不要直接搬，会绑死依赖**） |
| `eval_fusion_ab.py`、`ab_*.csv` | **A/B 对比的做法**可以借鉴成 `compare` 的逻辑 |
| `selfcheck_gate.py` | 阈值门禁的雏形 |
| `Multimodal_RAG` 的评测集 | 做成 `examples/` 的**真实示例**（脱敏后） |
| 那次 Embedding 事故 | README 开篇故事 + `drift` 功能的由来 |

**⚠️ 注意**：新仓库是**独立的**，不要 import 现有项目；只借鉴思路和脱敏数据。

---

## 13. 一周里程碑（建议节奏）

| 天 | 目标 |
|---|---|
| D1 | 骨架 + `metrics.py` + `compare` 能跑通 `examples/dummy` |
| D2 | 三种 adapter（python/http/command）+ 配置校验 |
| D3 | baseline 读写 + diff 表 + 三种退码 |
| D4 | `drift` 指标 + markdown 报告 + PR 评论 |
| D5 | README（含动图）+ CI 示例 + 测试 |
| D6 | 打磨 `examples/` 到"30 秒跑通"，发 **v0.1.0** |
| D7 | **铺渠道发布**（第一波） |

**第二周**：根据 issue 快速迭代，做第二波分发（换个角度讲同一个项目）。

---

## 14. 最容易做砸的三个点（提前警告）

1. **绑定了具体框架** → 没人用。**适配必须是回调，不是集成。**
2. **门禁用绝对阈值** → 每个数据集都要重新调，最后被弃用。**主判据必须是相对基线。**
3. **`examples/` 需要先起一整套环境** → 转化率归零。**示例必须零依赖、30 秒跑通。**
