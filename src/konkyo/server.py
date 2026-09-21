"""S2：给前端提供一个流式聊天端点。

跑法：
    uv run uvicorn konkyo.server:app --reload --port 8000

只做这一件事：把 llm.chat_stream() 的 async generator 转成 SSE，
通过 HTTP 推给浏览器。没有会话持久化、没有鉴权——那些不是这一步要学的。
"""

import json
import sys
import time
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from konkyo.config import ConfigError, cors_allow_origins
from konkyo.llm import LLM, LLMError

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


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]


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
    async def event_stream() -> AsyncIterator[str]:
        start = time.monotonic()
        first_token_at: float | None = None
        try:
            async for chunk in llm.chat_stream(req.messages):
                # 客户端断开（用户点了停止，AbortController.abort() 触发的）
                # 就别再继续从上游读了——不然模型还在后台生成，白付 token 费。
                if await request.is_disconnected():
                    print("[chat] 客户端已断开，停止转发", file=sys.stderr)
                    break

                if chunk.usage is not None:
                    yield _sse("usage", {"usage": str(chunk.usage)})
                    continue

                if first_token_at is None:
                    first_token_at = time.monotonic()
                    latency_ms = (first_token_at - start) * 1000
                    print(f"[chat] 首 token 延迟 {latency_ms:.0f}ms", file=sys.stderr)

                yield _sse("delta", {"delta": chunk.delta})
        except LLMError as e:
            # 只给 code，不给拼好的人话——翻译成哪种语言是前端的事
            # （web/src/lib/chat.ts 的 onError 按 code 去 messages/*.json 查）。
            # detail 是原始技术信息，前端可以选择性展示给开发者排查，不翻译。
            yield _sse("error", {"code": e.code, "detail": e.detail})
        yield _sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
