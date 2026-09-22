"""测 LLM 类：给定 OpenAI SDK 的各种响应/异常，断言我们包出来的行为对不对。

不打真实 API——慢、花钱、结果不确定。把 self.client/self.async_client
换成假的（Mock），让它按测试想要的方式返回数据或抛异常，只测「我们自己
写的这层逻辑」：异常怎么转成 LLMError、流式 chunk 怎么拼成 delta/usage。
"""

from contextlib import aclosing
from types import SimpleNamespace

import httpx
import pytest
from openai import APIError, APITimeoutError

from konkyo.config import LLMConfig
from konkyo.llm import LLM, LLMError, StreamChunk, _parse_usage


def make_llm() -> LLM:
    # 不走 LLMConfig.primary()（会去读环境变量、可能因为没配 key 报错）。
    # 测试用的配置是假的，反正不会真的发请求。
    config = LLMConfig(base_url="http://test.invalid", api_key="test", model="test-model")
    return LLM(config=config)


def make_usage_obj(prompt=10, completion=5, cached=2):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )


def make_delta_chunk(content: str | None):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))],
    )


def make_usage_chunk(usage_obj):
    return SimpleNamespace(usage=usage_obj, choices=[])


def dummy_api_error(message="boom") -> APIError:
    request = httpx.Request("POST", "http://test.invalid/chat/completions")
    return APIError(message, request=request, body=None)


def dummy_timeout_error() -> APITimeoutError:
    request = httpx.Request("POST", "http://test.invalid/chat/completions")
    return APITimeoutError(request=request)


class TestParseUsage:
    """_parse_usage 是纯函数，不用碰 LLM 类，直接喂各种形状的输入。"""

    def test_none_returns_zeroed_usage(self):
        usage = _parse_usage(None)
        assert (usage.input_tokens, usage.output_tokens, usage.cache_hit_tokens) == (0, 0, 0)

    def test_normal_shape(self):
        usage = _parse_usage(make_usage_obj(prompt=100, completion=50, cached=30))
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_hit_tokens == 30
        assert usage.cache_miss_tokens == 70  # 100 - 30，没有单独的"未命中"字段，减出来的

    def test_missing_details_defaults_to_zero_hit(self):
        # 没有 prompt_tokens_details 这个字段的 provider（不是每家都有前缀缓存）
        usage = _parse_usage(SimpleNamespace(prompt_tokens=10, completion_tokens=5))
        assert usage.cache_hit_tokens == 0
        assert usage.cache_miss_tokens == 10


class TestChat:
    """LLM.chat()：同步、非流式那条路径。"""

    def test_timeout_raises_llm_error_with_timeout_code(self):
        llm = make_llm()
        error = dummy_timeout_error()
        llm.client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(error)

        with pytest.raises(LLMError) as exc_info:
            llm.chat([{"role": "user", "content": "hi"}])
        assert exc_info.value.code == "timeout"

    def test_api_error_raises_llm_error_with_api_error_code(self):
        llm = make_llm()
        llm.client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(dummy_api_error())

        with pytest.raises(LLMError) as exc_info:
            llm.chat([{"role": "user", "content": "hi"}])
        assert exc_info.value.code == "api_error"

    def test_successful_reply_carries_text_and_usage(self):
        llm = make_llm()
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="你好"))],
            usage=make_usage_obj(),
        )
        llm.client.chat.completions.create = lambda **kw: response

        reply = llm.chat([{"role": "user", "content": "hi"}])
        assert reply.text == "你好"
        assert reply.usage.input_tokens == 10


class TestChatStream:
    """LLM.chat_stream()：异步生成器那条路径，S2 的核心。"""

    async def test_yields_deltas_in_order(self):
        llm = make_llm()

        async def fake_stream():
            yield make_delta_chunk("你")
            yield make_delta_chunk("好")
            yield make_usage_chunk(make_usage_obj())

        async def fake_create(**kwargs):
            return fake_stream()

        llm.async_client.chat.completions.create = fake_create

        chunks = [c async for c in llm.chat_stream([{"role": "user", "content": "hi"}])]

        assert [c.delta for c in chunks if c.delta] == ["你", "好"]
        assert chunks[-1].usage is not None

    async def test_stops_after_first_usage_chunk(self):
        # 实测过 Ark 会把同一份 usage 在结尾发两次（见 llm.py 里的注释）。
        # 收到第一条 usage 就该 return，不该把重复的那条也转发出去。
        llm = make_llm()

        async def fake_stream():
            yield make_delta_chunk("嗨")
            yield make_usage_chunk(make_usage_obj())
            yield make_usage_chunk(make_usage_obj())  # 重复的第二条，不该出现在结果里

        async def fake_create(**kwargs):
            return fake_stream()

        llm.async_client.chat.completions.create = fake_create

        chunks = [c async for c in llm.chat_stream([{"role": "user", "content": "hi"}])]

        usage_chunks = [c for c in chunks if c.usage is not None]
        assert len(usage_chunks) == 1

    async def test_keeps_text_of_chunk_that_also_carries_usage(self):
        # 回归测试：实测 Ark 把最后一段正文和 usage 放在同一个 chunk 里，
        # 之后再单独发一次 usage。以前先判断 usage 就 return，最后一段正文丢了。
        llm = make_llm()

        async def fake_stream():
            yield make_delta_chunk("こ")
            yield SimpleNamespace(
                usage=make_usage_obj(),
                choices=[SimpleNamespace(delta=SimpleNamespace(content="んにちは！"))],
            )
            yield make_usage_chunk(make_usage_obj())

        async def fake_create(**kwargs):
            return fake_stream()

        llm.async_client.chat.completions.create = fake_create

        chunks = [c async for c in llm.chat_stream([{"role": "user", "content": "hi"}])]

        assert "".join(c.delta for c in chunks) == "こんにちは！"
        assert len([c for c in chunks if c.usage is not None]) == 1
        assert chunks[-1].usage is not None  # usage 在正文之后

    async def test_closes_upstream_when_consumer_stops_early(self):
        # 用户点"停止"时 server.py 会 break 出去并 aclose() 这个生成器。
        # 这里断言：上游的流也会被立刻关掉，不是等垃圾回收。
        llm = make_llm()
        upstream_closed = False

        async def fake_stream():
            nonlocal upstream_closed
            try:
                yield make_delta_chunk("一")
                yield make_delta_chunk("二")
                yield make_usage_chunk(make_usage_obj())
            finally:
                upstream_closed = True

        async def fake_create(**kwargs):
            return fake_stream()

        llm.async_client.chat.completions.create = fake_create

        async with aclosing(llm.chat_stream([{"role": "user", "content": "hi"}])) as chunks:
            async for _ in chunks:
                break

        assert upstream_closed

    async def test_error_before_stream_starts_raises_llm_error(self):
        llm = make_llm()

        async def fake_create(**kwargs):
            raise dummy_timeout_error()

        llm.async_client.chat.completions.create = fake_create

        with pytest.raises(LLMError) as exc_info:
            async for _ in llm.chat_stream([{"role": "user", "content": "hi"}]):
                pass
        assert exc_info.value.code == "timeout"

    async def test_error_mid_stream_raises_stream_interrupted(self):
        llm = make_llm()

        async def fake_stream():
            yield make_delta_chunk("开始了")
            raise dummy_api_error("连接断了")

        async def fake_create(**kwargs):
            return fake_stream()

        llm.async_client.chat.completions.create = fake_create

        received: list[StreamChunk] = []
        with pytest.raises(LLMError) as exc_info:
            async for chunk in llm.chat_stream([{"role": "user", "content": "hi"}]):
                received.append(chunk)

        # 断掉之前已经吐出去的内容不该丢
        assert received[0].delta == "开始了"
        assert exc_info.value.code == "stream_interrupted"
