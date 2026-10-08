# rag-gate · turn RAG retrieval quality into a CI gate

> **Run RAG evaluation like unit tests: change your chunking, swap the embedding model, retune weights — if retrieval regresses, the PR goes red.**

```bash
pip install rag-gate
rag-gate compare --config rag-gate.yaml     # regression -> exit 1 -> red PR
```

![demo](docs/demo.gif)

[中文说明](README.zh-CN.md)

---

## This is not a made-up story

> We swapped our embedding model last week. **Same dimensions, no errors, latency even improved.**
> Three days later a customer complained in the group chat: "the answers are completely wrong."
>
> Root cause: **the write path used the new model, the read path did not** — the vector spaces
> no longer matched, so retrieval returned garbage. And three things made it worse:
>
> - **No gate stopped it** (changing a model required no check at all)
> - **No monitoring saw it** (latency and error rate were green)
> - **A customer found it first**
>
> This project is what came out of that incident.

## What existing tools miss

| Tool | What it does | What it misses |
| --- | --- | --- |
| Ragas / DeepEval | **Scores** one run, prints a number | No comparison against history, **no CI gate** |
| promptfoo | A/B testing at the prompt level | Not about **retrieval** quality |
| Your own script | One-off comparison | Not reusable, no stored baseline |
| **rag-gate** | **Compares against a baseline, fails on regression** | — |

Scoring already exists. **Gating against a historical baseline does not.**

## 30-second quickstart (zero dependencies, no vector DB)

```bash
git clone https://github.com/your-org/rag-gate && cd rag-gate
pip install -e .            # or skip the install entirely: PYTHONPATH=src
cd examples

rag-gate baseline --config rag-gate.yaml    # 1) record the baseline
rag-gate compare  --config rag-gate.yaml    # 2) should pass (exit 0)

# 3) simulate "swapped the embedding model, forgot the read path"
export RAG_GATE_DEMO=broken-model           # Windows: $env:RAG_GATE_DEMO = "broken-model"
rag-gate compare --config rag-gate.yaml     # -> exit 1, with a diff table
```

`examples/adapters/dummy_retriever.py` is a **fake retriever**: no DB, no deps, deterministic
results, just so you can watch green turn red in 30 seconds. It ships with four incident modes:

| `RAG_GATE_DEMO` | Incident | What you see |
| --- | --- | --- |
| `healthy` (default) | all good | all green |
| `broken-model` | embedding swapped on one side | metrics drop + **similarity distribution shifts down** |
| `dup-write` | non-idempotent retry | mrr/ndcg drop (duplicates push answers out) |
| `shrink` | retriever returns only 2 hits | recall drops, **distribution stays stable** |

The last two matter: **the same metric drop can have completely different causes.**

A failing run looks like this (colors omitted):

```
指标          基线      本次      变化       允许跌幅    判定
────────────  ────────  ────────  ─────────  ──────────  ──────
recall@5      0.9688    0.4688    -0.5000    0.05        FAIL
hit_rate@5    1.0000    0.5625    -0.4375    0.05        FAIL
mrr           0.9583    0.1542    -0.8042    0.05        FAIL
ndcg@5        0.9396    0.2194    -0.7201    0.05        FAIL
相似度分布（drift）
  top1.mean  0.9700 → 0.4600   -0.5100
  结论：分布整体大幅下移：很像写入侧/读取侧的 Embedding 不一致
门禁结果：检索质量退化（exit 1）
```

> Piped output is never colored (`NO_COLOR` is honored), so `--format json | jq` is safe:
> the report goes to stdout, hints and the verdict go to stderr.

## Wiring it into your own project

Give it one function — `question -> doc ids`. **No vector-store imports, no framework integration.**

```yaml
# rag-gate.yaml
dataset: golden.jsonl          # each line: {id, question, expected_doc_ids, [expected_answer]}

retriever:
  type: python                 # python | http | command
  target: my_app.retrieve      # or ./scripts/retrieve.py:retrieve
  top_k: 10

metrics: [recall@5, hit_rate@5, mrr, ndcg@10]

gate:
  baseline: baseline.json
  max_drop:                    # ★ primary rule: max allowed drop vs baseline
    recall@5: 0.02
    mrr: 0.03
  min_absolute:                # optional floor, protects against a bad baseline
    recall@5: 0.60
  drift:
    mean_drop: 0.02            # max allowed drop of top1 similarity mean

report:
  formats: [table, markdown, json]
  output_dir: .rag-gate
```

Your function can return any of these:

```python
def retrieve(query: str, k: int) -> list:
    return ["doc_1", "doc_7"]                          # ids only
    # or with scores, which enables drift:
    # return [{"doc_id": "doc_1", "score": 0.91}, ...]
```

HTTP retrievers and CLI retrievers are supported too — see
[`examples/adapters.example.yaml`](examples/adapters.example.yaml).

## ★ Why it compares *relative* regression, not an absolute score

Absolute thresholds are nearly impossible to pick: they differ per dataset and per business.
**Pick the wrong one and nobody adopts your tool.**

So the primary rule is **`max_drop` (max allowed drop vs baseline)**; `min_absolute` is only an
optional floor. This single decision is what makes rag-gate adoptable in someone else's project.

## Four commands, three exit codes

| Command | What it does |
| --- | --- |
| `rag-gate run` | Run once, print a report (no gating) |
| `rag-gate baseline` | Store current results as the baseline (**review, then commit it**) |
| `rag-gate compare` | Compare against baseline and gate (use this in CI) |
| `rag-gate drift` | Only look at similarity-distribution drift |

| Exit code | Meaning | What to do |
| --- | --- | --- |
| `0` | pass | merge |
| `1` | retrieval quality regressed | read the diff table; if intended, refresh the baseline |
| `2` | config or retrieval error | it never ran — this is **not** a quality regression |

**They must stay separate**, otherwise users cannot tell "my retrieval broke" from "my config is wrong".

## Metric definitions

| Metric | Definition |
| --- | --- |
| `recall@k` | share of expected docs present in the top k (**multi-doc** main metric) |
| `hit_rate@k` | share of queries with **at least one** hit in the top k (**single-doc** friendly) |
| `mrr` | mean reciprocal rank of the first hit |
| `ndcg@k` | position-discounted ranking quality (binary relevance) |
| `similarity_drift` | change of the **top1 similarity distribution** (mean/median/quantiles) |

**Why drift exists**: the first four metrics answer "are the results right"; drift answers
"did the process change". In that embedding incident, **result metrics may not have crossed the
threshold yet while the similarity distribution had already shifted down** — the earliest signal.

> Conventions: extra irrelevant docs neither help nor hurt; duplicate ids count once;
> `mrr` only looks at the first hit within the top k.

## CI integration (with a PR comment)

[`.github/workflows/gate.yml`](.github/workflows/gate.yml) is a copy-paste template:

```yaml
- run: pip install rag-gate
- run: rag-gate compare --config rag-gate.yaml --format markdown > comment.md
# exit 1 on regression -> red PR; the template posts the report as a PR comment
```

Commit `baseline.json` — it is the only source of history. If the golden set itself changes,
the report **warns** that scores are not comparable (make it fatal with
`gate.fail_on_dataset_change: true`).

## Roadmap

- [x] v0.1.0 metrics + three adapters + baseline comparison + three exit codes + drift + markdown
- [ ] Optional Ragas answer-score ingestion (no hard dependency)
- [ ] `rag-gate diff --baseline a.json --current b.json` (compare two reports, no retriever)
- [ ] Official GitHub Action (`uses: rag-gate/action@v1`)
- [ ] Per-query regression ranking (which questions broke first)
- [ ] Bridge to online monitoring (feed sampled production queries into the gate)

## Contributing

```bash
pip install -e ".[dev]"
pytest -q                              # no external services needed (the HTTP adapter starts a local server)
python examples/_demo_walkthrough.py   # end-to-end: four incidents + three exit codes
```

Issues and PRs welcome. Especially useful feedback: **where did you get stuck wiring up your own
retriever?**

## License

MIT
