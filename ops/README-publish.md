# 一条命令发布（Windows PowerShell）

这个脚本把本地仓库推到 GitHub。在此之前先把要做的事说清楚：

- **本地这一侧已经完整**：7 个提交、`v0.1.0` 标签、118 个测试全过。
- **必须你来做的事**：GitHub 的写权限。agent 所在的沙盒**不能出网**（git 的 TLS 握手
  被拦、pip 装包会卡死），所以推送这一步只能你在自己的终端里跑这个脚本。
- **PyPI 另说**：名字 `rag-gate` 在 PyPI 上是空的（查过了），但发布要先在本机装好
  `setuptools`/`build` 并连上 PyPI。详见 `docs/PUBLISH.md`。

## 怎么用

1. 建一个 Personal Access Token（二选一）：
   - **Fine-grained**：权限给 `Administration: write`（建仓库）+ `Contents: write`（推代码）
   - **Classic**：勾 `repo` 就够了
2. 在一个**新的 PowerShell 窗口**里（不要在 agent 会话里）跑：

```powershell
# 方式 A：直接把 token 写在命令行（简单，但会进历史记录）
D:\pythoncode\rag-gate\ops\publish_github.ps1 -Token ghp_xxxxxxxx

# 方式 B：先放进环境变量，命令里就不出现 token
$env:GITHUB_TOKEN = "ghp_xxxxxxxx"
D:\pythoncode\rag-gate\ops\publish_github.ps1
```

脚本会做四件事（**已存在就跳过，可以重复跑**）：

1. `GET /user` 确认 token 有效，并核对账号；
2. 建仓库 `rag-gate`（Public，描述与 topics 一并设置好）；
3. `git push -u origin main`（连提交历史一起推）；
4. `git push origin v0.1.0`（推标签）。

跑完后 GitHub 页面上的 topics 应该是：
`rag` `llm` `eval` `testing` `ci` `retrieval` `regression-testing`

## 发布后（还没做，需要你那边网络）

```powershell
cd D:\pythoncode\rag-gate
python -m pip install --user build twine
python -m build                     # 产出 dist/rag_gate-0.1.0.tar.gz 与 .whl
python -m twine check dist/*
python -m twine upload dist/*       # 需要 PyPI API token
```

> 提醒：CI 模板（`.github/workflows/gate.yml`）里写的是 `pip install rag-gate`。
> **PyPI 还没发之前，这段在别人仓库里会装不到包**。真发 PyPI 之前，先用
> `pip install git+https://github.com/1585164493a/rag-gate` 顶上。
