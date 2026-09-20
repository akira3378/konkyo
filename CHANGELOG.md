# CHANGELOG

记录每次合并进 `main` 的内容。日常开发在 `dev`，一个阶段做完才合并。

一条至少要写清：**新增了什么能力**、**指标动了多少**（没测就写"未测"）、**做了什么判断**。
指标写不出数字的阶段，说明第 2 条完成标准没满足 —— 那就不该合并。

格式：

```
## [S3] 2026-XX-XX
### 新增
- ...
### 指标
- 路由正确率 78% → 84%（20 题）
### 判断
- 故意没用 Agent：...
```

---

## [S1] 2026-09-20

### 新增
- 仓库从 `Job/AI Agent方向/konkyo` 独立出来，成为自己的 git 仓库
- Python 项目骨架：`pyproject.toml`（uv / ruff）、`src/konkyo/{config,llm,cli}.py`
- 最小 LLM 调用链路：终端 → Python → OpenAI 兼容 API → 终端
- 防 key 泄露的 pre-commit 钩子（`.githooks/pre-commit`，需 `git config core.hooksPath .githooks`）
- `dev` / `main` 分支分离，`main` 只接受合并

### 指标
- 未测。S1 的目标数字是 token 数与单次成本，跑通后补。

### 判断
- 只有代码和工程配置进 git；指令、计划、复盘、评测记录留在本地 `_plan/`（已 ignore）
- 现阶段不写对外展示的 README，等要给招聘方看时再补
