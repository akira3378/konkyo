# Changelog

## 2026-09-20 · S1：终端 LLM 调用链路

**Sonnet5 medium独立完成：**
- 项目骨架：`pyproject.toml`（uv + ruff）、`src/konkyo/{config,llm,cli}.py`
- 终端 → Python → OpenAI 兼容 API 的最小调用链路，支持多轮对话历史
- pre-commit 钩子防止 API key 被提交
- `dev` / `main` 分支分离

**在用户引导下完成：**
- 接入火山方舟（Ark）转发的 `deepseek-v4-1-flash`，按实测的返回格式解析 token 用量与缓存命中
- 实测 94%（reasoning_tokens=2071 / completion_tokens=2205）的输出 token 花在思考上，当前场景不需要 thinking mode，关闭 reasoning
- 金额计算引入不必要的复杂度，改为只记录 token 消耗
