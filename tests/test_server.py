"""测 FastAPI 的 SSE 端点：不起真的 uvicorn，用 httpx 直接对着 ASGI app 发请求。

关键操作：把 konkyo.server 模块里那个全局的 `llm` 换成假的。
server.py 的 event_stream() 每次请求都从模块全局读 `llm` 这个名字，
所以测试里 import 完模块后直接改它的属性就行，不用改生产代码、
不用搭一套依赖注入。
"""

import json

import httpx
import pytest

import konkyo.server as server_module
from konkyo.llm import LLMError, StreamChunk, Usage


class FakeLLM:
    """站在 server.py 的角度，LLM 只有一个方法要交互：chat_stream()。
    假的实现只要形状对（async generator，吐 StreamChunk）就够了。
    """

    def __init__(self, chunks=None, error: LLMError | None = None):
        self._chunks = chunks or []
        self._error = error

    async def chat_stream(self, messages, temperature=0.3):
        for chunk in self._chunks:
            yield chunk
        if self._error:
            raise self._error


@pytest.fixture(autouse=True)
def restore_llm():
    # 每个测试改完 server_module.llm 之后自动换回来，测试之间不互相影响。
    original = server_module.llm
    yield
    server_module.llm = original


def parse_sse_events(text: str) -> list[tuple[str, dict]]:
    """把响应体按 SSE 格式切成 (event, data字典) 的列表，方便断言。"""
    events = []
    for block in text.strip().split("\n\n"):
        if not block:
            continue
        event = "message"
        data = "{}"
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        events.append((event, json.loads(data)))
    return events


async def post_chat(messages=None) -> httpx.Response:
    transport = httpx.ASGITransport(app=server_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/api/chat",
            json={"messages": messages or [{"role": "user", "content": "hi"}]},
            headers={"Origin": "http://localhost:3000"},
        )


async def test_streams_deltas_then_usage_then_done():
    server_module.llm = FakeLLM(
        chunks=[
            StreamChunk(delta="你"),
            StreamChunk(delta="好"),
            StreamChunk(usage=Usage(10, 5, 2, 8)),
        ]
    )

    response = await post_chat()
    events = parse_sse_events(response.text)

    assert [e for e, _ in events] == ["delta", "delta", "usage", "done"]
    assert [d["delta"] for e, d in events if e == "delta"] == ["你", "好"]


async def test_error_becomes_code_and_detail_not_prose():
    # 这条是 S2 那次国际化事故的回归测试：server.py 曾经直接把拼好的
    # 中文句子塞进 error 事件，前端就没法按语言翻译。现在必须是 code+detail。
    server_module.llm = FakeLLM(
        chunks=[StreamChunk(delta="开始了")],
        error=LLMError("api_error", "上游 401"),
    )

    response = await post_chat()
    events = parse_sse_events(response.text)

    error_events = [d for e, d in events if e == "error"]
    assert len(error_events) == 1
    assert error_events[0] == {"code": "api_error", "detail": "上游 401"}
    # 断线前已经生成的内容要保留，不因为后面出错就丢掉
    assert events[0] == ("delta", {"delta": "开始了"})


async def test_cors_allows_configured_origin():
    server_module.llm = FakeLLM(chunks=[StreamChunk(usage=Usage(1, 1, 0, 1))])

    response = await post_chat()

    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
