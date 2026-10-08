# 发布计划（v0.1.0）

这份文档只给发布用，对外不宣传。

## 一、现在的状态

- 代码、测试、README、CI 模板、动图都已就绪（本地 git，7 个提交 + `v0.1.0` 标签）
- 测试 **118 个全过**（本机没有 pytest，用 `_dev/run_tests.py` 这个最小兼容跑测器跑的）
- README 那条命令链另外冒烟过 11 项：`baseline → compare(0) → broken-model(1) → drift(1) → --skip(0) → 配置错(2)`

## 二、名字占用情况（2026-10-08 查实）

| 位置 | 结果 |
| --- | --- |
| PyPI 包名 `rag-gate` | **空着**（`https://pypi.org/pypi/rag-gate/json` 返回 404） |
| GitHub 仓库 `1585164493a/rag-gate` | **空着**（API 返回 404；同账号下已有 AutoChain 等仓库） |
| PyPI 包名 `retrieval-gate` | 也空着（备选） |

## 三、赛道现状（重要，影响文案）

`rag-gate` 这个名字没人用，但**这个点已经有人做了**，别再写"没人做门禁"：

| 项目 | 它强调什么 | 我们和它的差别 |
| --- | --- | --- |
| [sivananda1995/Rag-regression-gate](https://github.com/sivananda1995/Rag-regression-gate) | 配对 bootstrap + 显著性，防小评测集噪声误报 | 我们在 **0.1.0 里没有统计显著性**；但我们有 drift（分布漂移）和三种退码 |
| [jinwovo/rag-eval-gate](https://github.com/jinwovo/rag-eval-gate) | 置信区间、任意时间有效的 CI 门禁，已发 PyPI | 同上；它的统计更严谨，我们的接入更简单（零依赖、30 秒） |
| [metawake/langgraph-rag-quality-gate](https://github.com/metawake/langgraph-rag-quality-gate) | LangGraph 编排的审计/模拟/解释 | 我们不绑框架、不绑向量库 |

**结论**：我们的差异化是「**零依赖 + 30 秒跑通 + 相对基线 + 分布漂移信号**」，
而不是「第一个做门禁的」。README 里那句"空位就在这里"需要按这个事实改写（见第五节）。

## 四、还缺的能力（按优先级）

1. **显著性 / 噪声保护**（对手已经有）：小评测集上 ±0.02 抖动不该让 PR 变红。
   做法：按 query 配对 bootstrap，只有"跌幅显著"才 fail；先做成 `--strict` 可选开关。
2. `rag-gate diff --baseline a.json --current b.json`：纯比两份报告，不跑检索器。
3. 官方 Action（`uses: rag-gate/action@v1`），把 PR 评论那段封装掉。
4. 按 query 的退化排名：最先坏的是哪几条问题（排查时最有用）。
5. 从 Ragas 结果里取答案分（可选依赖）。

## 五、发布当天要做的事

1. **改写 README 的差异化段落**（把"没人做门禁"改成"我们更轻、更快、带 drift"），
   并把三个对手放进对比表；顺便把标题按结果重写：
   「换了个 Embedding，三天后客户才发现 —— 我做了个 CI 门禁防这个」。
2. 推代码（`ops/publish_github.ps1`），确认 topics 七个都在、仓库 Public。
3. （可选，等本机能联网）`python -m build` + `twine upload` 发 PyPI；
   **PyPI 没发之前，CI 模板里的 `pip install rag-gate` 会装不到包**，
   先在 README 顶部用 `pip install git+https://github.com/...` 顶上。
4. 铺渠道，标题统一用第 1 条那句：
   - 中文：V2EX（分享创造）、掘金、即刻、少数派、小红书
   - 英文：Reddit r/LocalLLaMA、Hacker News（Show HN）、X
5. 前 20 个 issue 的响应速度决定口碑 —— 发布当天留出时间盯。

## 六、别做的事

- 不要为了"占位"先把 PyPI 发一个空包：一旦发了 0.1.0 就退不回来（PyPI 不允许重传同名版本）。
- 不要在 README 里写没法验证的数字（星标、性能倍数）。当前所有数字都是本仓库可复现的。
