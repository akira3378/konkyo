"""给前端提供一个流式聊天端点（S2），S3 起中间多了一步分类。

跑法：
    uv run uvicorn konkyo.server:app --reload --port 8000

只做这一件事：把 workflow.run_turn() 的 async generator 转成 SSE，
通过 HTTP 推给浏览器。没有会话持久化、没有鉴权——那些不是这一步要学的。

事件顺序：route → delta… → usage → done（出错时 error → done）。
"""

import json
import sys
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from konkyo.config import ConfigError, cors_allow_origins
from konkyo.llm import LLM, LLMError
from konkyo.router import RouteResult
from konkyo.workflow import run_turn

app = FastAPI()

# SSE 响应默认不带 CORS 头，浏览器会拦——这不是可选项，是浏览器同源策略逼的，
# 前后端不同端口就是"跨域"。允许哪些 origin 从环境变量读（见 config.py），
# 部署到真实域名时改 CORS_ALLOW_ORIGINS，不用改这段代码。
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)

try:
    llm = LLM()
except ConfigError as e:
    print(f"配置错误：{e}", file=sys.stderr)
    raise

# 请求体的上限。这是防滥用的护栏，不是调出来的最优值：没有这些限制，
# 任何人都能塞一个超长的 messages 进来，用我的 key 跑任意长的上下文。
# 超出就是 422，前端按 http 错误显示。部署时再按真实使用情况调整。
MAX_TURNS = 40
MAX_USER_CHARS = 2000
MAX_TOTAL_CHARS = 20000


class ChatTurn(BaseModel):
    # 只收 user/assistant。system 由服务端自己加（见 prompts.py），
    # 客户端发来的 system / tool 一律 422，不静默丢弃——静默丢弃会让调用方
    # 以为自己的 system prompt 生效了。
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=MAX_TURNS)

    @model_validator(mode="after")
    def check_shape(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("最后一条必须是 user：这个端点只回答用户最新的问题")
        if any(m.role == "user" and len(m.content) > MAX_USER_CHARS for m in self.messages):
            raise ValueError(f"单条提问不能超过 {MAX_USER_CHARS} 字")
        if sum(len(m.content) for m in self.messages) > MAX_TOTAL_CHARS:
            raise ValueError(f"对话总长度不能超过 {MAX_TOTAL_CHARS} 字")
        return self


def _sse(event: str, data: dict) -> str:
    """拼一条 SSE 消息。

    格式是固定的：`data: <json>` 后面跟一个空行（两个换行符）。
    这个空行是消息边界，前端按它切分——不是随便加的空格。
    `event:` 字段是可选的，用来区分同一条流里的不同种类消息
    （这里区分"正文片段"和"结束/出错"），前端可以按 event 类型分别处理。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    # 不含 system：回答用哪个 system prompt 要等分类完才知道，由 workflow.py 加。
    history = [m.model_dump() for m in req.messages]

    async def event_stream() -> AsyncIterator[str]:
        start = time.monotonic()
        first_token_at: float | None = None
        try:
            # aclosing：下面 break 出去的时候立刻 aclose() 这个生成器，
            # 一路传到 llm.chat_stream() 的 finally，马上关掉上游连接，
            # 而不是等垃圾回收哪天顺手关。
            async with aclosing(run_turn(llm, history)) as events:
                async for chunk in events:
                    # 客户端断开（用户点了停止，AbortController.abort() 触发的）
                    # 就别再继续从上游读了。
                    if await request.is_disconnected():
                        print("[chat] 客户端已断开，停止转发", file=sys.stderr)
                        break

                    if isinstance(chunk, RouteResult):
                        # 日志只记类别和耗时，不记 reason：reason 是模型对用户问题的转述，
                        # 可能带着用户的个人信息（AGENTS.md 红线 3）。
                        print(
                            f"[chat] route={chunk.route} fallback={chunk.fallback} "
                            f"attempts={len(chunk.attempts)} {chunk.latency_ms:.0f}ms",
                            file=sys.stderr,
                        )
                        yield _sse("route", {"route": chunk.route, "fallback": chunk.fallback})
                        continue

                    if chunk.usage is not None:
                        # 发结构化的数字，不发拼好的句子——和 error 只发 code 是同一个
                        # 理由：显示成哪种语言是前端的事。以前这里发 str(usage)，
                        # 里面的"缓存命中"四个中文字原样出现在日文/英文界面上。
                        # S3 起是这一轮的合计（分类 + 回答），见 workflow.py。
                        yield _sse("usage", asdict(chunk.usage))
                        continue

                    if first_token_at is None:
                        first_token_at = time.monotonic()
                        latency_ms = (first_token_at - start) * 1000
                        # 从收到请求算起，所以含分类那一次调用的时间。
                        print(f"[chat] 首 token 延迟 {latency_ms:.0f}ms", file=sys.stderr)

                    yield _sse("delta", {"delta": chunk.delta})
        except LLMError as e:
            # 只给 code，不给拼好的人话——翻译成哪种语言是前端的事
            # （web/src/lib/chat.ts 的 onError 按 code 去 messages/*.json 查）。
            # detail 是原始技术信息，前端可以选择性展示给开发者排查，不翻译。
            yield _sse("error", {"code": e.code, "detail": e.detail})
        yield _sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
