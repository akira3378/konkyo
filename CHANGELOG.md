# Changelog

## 2026-09-22 · S3：分类 + 结构化输出 + 评测骨架（进行中）

**Opus 5 独立完成：**
- 每轮对话先分类再回答（`src/konkyo/router.py`、`workflow.py`）：一般手续问题 / 个案判断请求 / 文书草稿 / 范围外，四类各用自己的 system prompt；范围外不调用模型回答，界面显示固定文案（中/日/英）
- 分类结果用 `response_format=json_schema` 输出、Pydantic 校验；不合格时把错误原文喂回去重试一次，仍不合格按一般手续问题回答并标出 fallback。API 本身出错不走 fallback，照常报错
- "不对个案下判断"写进所有回答用 prompt 的开头，分类分错也不会绕开
- SSE 多了 `route` 事件，界面在每个回答上方显示分到的类别；`usage` 改为一轮的合计（分类 + 回答）
- CLI 改为和服务端走同一个 workflow（流式输出）
- 实测 Ark：`json_object` 只保证是合法 JSON，prompt 里要求别的字段名时照着 prompt 输出；`json_schema` 仍按 schema 输出
- bug（写的时候发现）：Pydantic 会把类的 docstring 放进 JSON Schema 的 description，每次分类都会把开发用的中文注释发给模型。改成普通注释，加回归测试
- 评测骨架：`eval/questions.yaml`（20 题，Opus 5 起草、人工逐条确认）、`eval/run_routing.py`（路由正确率、结构化失败率、分类延迟、混淆矩阵，原始输出存 `eval/results/`）
- `scripts/bench_latency.py --compare-routing`：交替跑 S2/S3 量首 token
- 实测：三种结构化做法各 60 次调用，路由正确率都是 60/60、格式失败都是 0（题太容易，分不出差别，见 EVALUATION.md）；分类让首 token p50 从 1252ms 变成 2553ms（其中分类 1568ms），每轮 in token 97 → 447
- 测试：后端 32 → 53 条，前端 16 → 18 条

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
