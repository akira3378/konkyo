"""LLM 调用的最小封装。

S1 的核心认知：
    LLM 就是一个远程 HTTP API。和前端 fetch 一个 REST 接口没有本质区别。
    区别只有两个：① 输出不确定 ② 请求里带着很大的 context。

    而且模型**永远不会执行任何东西**。它只返回文本。
    执行的永远是你的代码。（这条到 S4 讲 Tool Calling 时会变得非常重要）
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openai import APIError, APITimeoutError, AsyncOpenAI, OpenAI

from konkyo.config import LLMConfig


@dataclass
class Usage:
    """一次调用用了多少 token。

    为什么 S1 就要算这个：
    "怎么控制 LLM 成本" 是面试必问题。只会答"截断历史"是入门线。
    要能答：多轮对话的 messages 前缀是重复的，命中前缀缓存时能省下这部分——
    所以要把 system prompt 和工具定义放最前面、保持前缀稳定。

    这里故意只记 token 数，不算成本（金额）：
    价目表是会过时的外部数字（不同 provider、峰谷时段、汇率都不一样），
    自己维护一份很容易讲错、讲的时候还要先解释一堆计费规则。
    token 数是 API 直接返回的原始事实，没有这些争议，复盘时也最好讲清楚。
    要看这次实际花了多少钱，去 provider 控制台的账单页对着 token 数查，
    不在代码里编一份可能过时的价目表。
    """

    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int  # 命中前缀缓存的 input token
    cache_miss_tokens: int

    def __str__(self) -> str:
        hit_rate = self.cache_hit_tokens / self.input_tokens * 100 if self.input_tokens else 0
        return (
            f"in={self.input_tokens} (缓存命中 {self.cache_hit_tokens}, {hit_rate:.0f}%) "
            f"out={self.output_tokens}"
        )


@dataclass
class Reply:
    text: str
    usage: Usage


@dataclass
class StreamChunk:
    """流式返回的一个片段。

    只有两种：正文片段（delta 非空）、或者流结束时的用量汇总（usage 非空）。
    分成两种类型而不是一路只 yield 字符串，是因为 usage 只在流的最后一条
    chunk 里出现一次——用同一个类型带上"这条是文字还是用量"的信息，
    调用方（server.py）不用另外猜测最后一条和前面的有什么不同。
    """

    delta: str = ""
    usage: Usage | None = None


class LLM:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.primary()
        # OpenAI SDK 只是 HTTP 请求的封装。base_url 一换就指向别家。
        self.client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key, timeout=60)
        # 流式必须用异步客户端：同步客户端的迭代器会阻塞整个事件循环，
        # FastAPI 服务这一个请求的时候，其他请求全部卡住。
        self.async_client = AsyncOpenAI(
            base_url=self.config.base_url, api_key=self.config.api_key, timeout=60
        )

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.3) -> Reply:
        """发一轮对话，拿回一个回复。

        messages 是整个对话历史。注意：
        **模型不会自己记住上一轮说了什么。**
        它"记得"，是因为我们每次都把完整历史重新发过去。
        所谓"聊天记忆"，本质是程序在管理这个 list。（S2 会真正用到）
        """
        # deepseek-v4.1-flash 默认会先生成一段隐藏思维链再回答，这段思考本身
        # 按输出 token 计费，之前实测占了 out 的九成以上——S1 只是简单问答，
        # 不需要这个。这个字段 OpenAI SDK 不认识，走 extra_body 原样透传。
        # 没按 provider 分支：目前只在 Ark 上验证过能用，还没拿 DeepSeek 官方
        # 试过会不会报错——真遇到报错再按 provider 区分，不提前猜。
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except APITimeoutError as e:
            raise RuntimeError("请求超时。网络问题，或者 context 太长。") from e
        except APIError as e:
            # 不要把异常吞掉。把能帮助排查的信息带出去。
            raise RuntimeError(f"API 调用失败：{e}") from e

        return Reply(
            text=response.choices[0].message.content or "",
            usage=_parse_usage(response.usage),
        )

    async def chat_stream(
        self, messages: list[dict[str, str]], temperature: float = 0.3
    ) -> AsyncIterator[StreamChunk]:
        """和 chat() 发的是同一个请求，唯一区别是 stream=True。

        区别不在"发什么"，在"怎么收"：
        chat() 等 SDK 把完整响应体收完，包成一个 Reply 才返回。
        这里 SDK 把 HTTP 响应体按 DeepSeek 推来的 SSE 分片，一条条转成
        chunk 对象——每条到了就立刻从这个 async generator yield 出去，
        不等后面的分片。调用方（server.py）每 yield 一次就能立刻转发给浏览器。
        """
        try:
            stream = await self.async_client.chat.completions.create(
                model=self.config.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                extra_body={"thinking": {"type": "disabled"}},
                stream=True,
                # 不加这个，流式响应完全不带 usage——只有非流式请求默认带。
                stream_options={"include_usage": True},
            )
        except APITimeoutError as e:
            raise RuntimeError("请求超时。网络问题，或者 context 太长。") from e
        except APIError as e:
            raise RuntimeError(f"API 调用失败：{e}") from e

        try:
            async for chunk in stream:
                # 最后一条 chunk：choices 是空列表，usage 是这次调用的汇总。
                # （标准 OpenAI 流式协议就是这样分两种 chunk，不是这家 provider 特有的）
                if chunk.usage is not None:
                    # 这家 provider 会把同一份 usage 在结尾重复发一次
                    # （不同 provider 的实现细节，不是协议要求），收到就
                    # 立刻结束，不然 usage 会被转发两次。
                    yield StreamChunk(usage=_parse_usage(chunk.usage))
                    return
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield StreamChunk(delta=delta)
        except APIError as e:
            # 流已经开始才出错——前面吐出去的文字保留，只是没法继续了。
            raise RuntimeError(f"流式读取中断：{e}") from e


def _parse_usage(raw: Any) -> Usage:
    """从响应里取用量。

    按实测过的 Ark 格式解析：缓存命中数在 prompt_tokens_details.cached_tokens 里，
    只给命中数，没有"未命中数"字段，未命中 = 总数 - 命中。
    没有 DeepSeek 官方接口的兼容代码——没拿真实 key 测过它的字段格式，
    不该照着文档猜一份没验证过的解析逻辑。真的换过去用了再看实际返回改。
    """
    if raw is None:
        return Usage(0, 0, 0, 0)

    input_tokens = getattr(raw, "prompt_tokens", 0) or 0
    output_tokens = getattr(raw, "completion_tokens", 0) or 0

    details = getattr(raw, "prompt_tokens_details", None)
    hit = getattr(details, "cached_tokens", None) if details else None
    hit = hit or 0
    miss = input_tokens - hit

    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
    )
