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

## [S1] 2026-09-20 · dev 中间记录 1/2（AI 自主实现）

> 例外记录：这两条 dev 中间记录是应用户要求临时加的，目的是让 CHANGELOG 本身能看出
> 哪些改动是 AI 自主做的、哪些是用户看完代码后引导调整的。合并进 `main` 时会再汇总成一条正式的 `[S1]`。

### 新增
- 接入火山方舟（Ark）转发的 `deepseek-v4-1-flash`：`llm.py` 按 `base_url` 切换定价表，DeepSeek 官方美元价目表保留未删（以后可能切回去）
- Ark 定价按官方文档实现峰谷两档（元/百万 token），成本按调用时刻自动选价
- usage 解析同时兼容两种缓存字段命名：DeepSeek 扁平字段 / Ark 嵌套字段 `prompt_tokens_details.cached_tokens`
- 默认对 Ark 关闭 reasoning（`extra_body={"thinking": {"type": "disabled"}}`），避免隐藏思维链计入输出 token

### 指标
- 关闭 reasoning 后单次调用：`in≈60 out≈105 ≈¥0.000480`（空闲时段）；关闭前同一类问题 `out` 常见 2000–6000+，成本高一个数量级
- 实测缓存命中门槛：前缀 ≥256 token 才可能命中（`in=450` 时命中 `256`，57%）；门槛以下始终 0，非 bug

### 判断
- 价目表做了峰谷两档、货币单位区分，追求成本数字"准确"——用户看完代码后认为这引入了不必要的复杂度（时段判断本身就是易过时的外部状态），见下一条中间记录的调整

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
