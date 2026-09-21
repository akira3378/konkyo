# Changelog

## 2026-09-21 · S2：流式聊天

**Sonnet5 独立完成：**
- 后端：`LLM.chat_stream()` 异步生成器 + FastAPI `/api/chat` SSE 端点，客户端断开时经 `request.is_disconnected()` 停止继续向上游取流
- 前端：Next.js + TypeScript + Tailwind，`web/src/lib/chat.ts` 手写 `fetch()` + `ReadableStream` 解析 SSE（未用 `EventSource`/Vercel AI SDK），`AbortController` 实现停止
- 首 token 延迟在服务端打点（终端日志），前端界面同步展示
- bug：长对话不自动滚到底部，实测自查发现并修复
- bug：中途取消且未收到任何 token 时会留下一个空 assistant 气泡，实测自查发现并修复

**在用户引导下完成：**
- 界面加了中/日/英三语切换，同时通过eslint-plugin-i18next检测被遗漏的html标签文本和属性里的文本
- 各种环境配置的地址，从环境变量读，而非硬编码
- bug：日语输入法按 Enter 选词时会被误判成"发送"，监听浏览器isComposing状态
- bug：服务器返回的内容未接入国际化，改为后端只抛错误类型，具体文案由前端按类型查多语言表

## 2026-09-20 · S1：终端 LLM 调用链路

**Sonnet5 独立完成：**
- 项目骨架：`pyproject.toml`（uv + ruff）、`src/konkyo/{config,llm,cli}.py`
- 终端 → Python → OpenAI 兼容 API 的最小调用链路，支持多轮对话历史
- pre-commit 钩子防止 API key 被提交
- `dev` / `main` 分支分离

**在用户引导下完成：**
- 接入火山方舟（Ark）转发的 `deepseek-v4-1-flash`，按实测的返回格式解析 token 用量与缓存命中
- 实测 94%（reasoning_tokens=2071 / completion_tokens=2205）的输出 token 花在思考上，当前场景不需要 thinking mode，关闭 reasoning
- 金额计算引入不必要的复杂度，改为只记录 token 消耗
