# Changelog

## 2026-09-22 · S2 修正

**Opus 5 审查时发现并独立修复：**
- bug：流式回答每次都丢掉最后一段正文。实测 Ark 把最后一段正文和 usage 放在同一个 chunk 里，代码先判断 usage 就结束了；短回答最明显（"こんにちは！"只显示"こ"）。改为先取正文再取 usage，加回归测试
- bug：token 用量在日/英界面显示中文（后端发的是拼好的中文句子）。改为后端只发数字，前端按语言显示
- bug：出错时错误文案被拼进回答内容，下一轮作为对话历史发给了模型。改为错误只在界面显示，出错的那一轮不再发给后端（和 CLI 一致）
- system prompt 从前端移到后端，CLI 与服务端共用一份；`/api/chat` 只接受 user/assistant，限制条数与长度，不合规返回 422
- 用户停止生成时显式关闭上游连接（此前依赖垃圾回收），加测试
- 配置：base_url/model 不再默认指向未实测的 DeepSeek 官方 API；`.env.example` 只保留代码实际读取的变量；删掉未使用的 `LLMConfig.alternative()`；开发用启动配置改为相对路径
- `EVALUATION.md`（评测记录）改为放在本仓库公开，并改写成只记已测内容：推移表只留已测的列，术语加说明；S2 行里未验证的归因改为"原因未查明"
- 测试：后端 17 → 32 条，前端 12 → 16 条
- `scripts/bench_latency.py` 同时记录 token 用量；09-22 重新测了一次：非流式 p50 6146ms、流式首 token p50 1150ms，out 中位数 685 / 670（N=15）

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
- 前后端补自动化测试：后端 pytest（mock LLM 客户端和 SSE 端点）、前端 Vitest + Testing Library（SSE 解析、IME 误触发送回归测试），接入 pre-commit 钩子，改动会自动跑
- 加 `scripts/bench_latency.py`：对比非流式（S1，等完整回复）与流式（S2，首 token）延迟，跑 p50/p95
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
