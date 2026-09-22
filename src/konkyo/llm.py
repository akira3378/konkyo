"""LLM 调用的最小封装。

S1 的核心认知：
    LLM 就是一个远程 HTTP API。和前端 fetch 一个 REST 接口没有本质区别。
    区别只有两个：① 输出不确定 ② 请求里带着很大的 context。

    而且模型**永远不会执行任何东西**。它只返回文本。
    执行的永远是你的代码。（这条到 S4 讲 Tool Calling 时会变得非常重要）
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from openai import APIError, APITimeoutError, AsyncOpenAI, OpenAI

from konkyo.config import LLMConfig

# 后端只往外抛这几个固定的"错误种类"，不抛人话。
# 人话（翻译成中/日/英）是前端的事——具体在 web/messages/{zh,ja,en}.json
# 的 errors 命名空间里，key 必须和这里的字面量一一对应。
# 两边靠字面量字符串手动对齐，没有自动生成，但正因为这个集合很小、
# 很少变，手动对齐的成本远低于搭一套跨 Python/TS 的代码生成。
LLMErrorCode = Literal["timeout", "api_error", "stream_interrupted"]


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

    def __add__(self, other: "Usage") -> "Usage":
        # S3 起一轮对话有两次调用（先分类、再回答），界面上显示的是这一轮的合计。
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_hit_tokens + other.cache_hit_tokens,
            self.cache_miss_tokens + other.cache_miss_tokens,
        )

    def __str__(self) -> str:
        hit_rate = self.cache_hit_tokens / self.input_tokens * 100 if self.input_tokens else 0
        return (
            f"in={self.input_tokens} (缓存命中 {self.cache_hit_tokens}, {hit_rate:.0f}%) "
            f"out={self.output_tokens}"
        )


class LLMError(RuntimeError):
    """LLM 调用失败。带一个稳定的 code，给前端挑翻译用；detail 是原始技术
    信息（provider 返回的英文报错等），只给开发者排查看，不奢望它是人话。

    以前这里直接 raise RuntimeError("请求超时。网络问题，或者 context 太长。")——
    这段中文会原样透传给前端、原样显示在界面上，不管界面当前是中/日/英哪个
    语言。国际化如果只做"界面上的字面量"，后端抛出来的这种运行时文案就是
    漏网之鱼：它不是组件里 t("...") 能覆盖到的地方，因为它压根不是前端写的字符串。
    改成后端只给 code，前端拿 code 去查 messages/*.json，这类文案就不可能
    再绕开翻译层直接上屏——不是"记得每次都翻译"，是结构上没有别的路可走。
    """

    def __init__(self, code: LLMErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


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

    def _base_kwargs(self, messages: list[dict[str, str]], temperature: float) -> dict[str, Any]:
        """chat() / chat_stream() / complete() 三种调用共用的请求参数。

        deepseek-v4.1-flash 默认会先生成一段隐藏思维链再回答，这段思考本身
        按输出 token 计费，之前实测占了 out 的九成以上——这里的问答和分类都
        不需要这个。这个字段 OpenAI SDK 不认识，走 extra_body 原样透传。
        没按 provider 分支：目前只在 Ark 上验证过能用，还没拿 DeepSeek 官方
        试过会不会报错——真遇到报错再按 provider 区分，不提前猜。
        """
        return {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "extra_body": {"thinking": {"type": "disabled"}},
        }

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.3) -> Reply:
        """发一轮对话，拿回一个回复。

        messages 是整个对话历史。注意：
        **模型不会自己记住上一轮说了什么。**
        它"记得"，是因为我们每次都把完整历史重新发过去。
        所谓"聊天记忆"，本质是程序在管理这个 list。（S2 会真正用到）
        """
        try:
            kwargs = self._base_kwargs(messages, temperature)
            response = self.client.chat.completions.create(**kwargs)
        except APITimeoutError as e:
            raise LLMError("timeout", str(e)) from e
        except APIError as e:
            # 不要把异常吞掉。把能帮助排查的信息带出去（放 detail，不是拼进人话文案里）。
            raise LLMError("api_error", str(e)) from e

        return Reply(
            text=response.choices[0].message.content or "",
            usage=_parse_usage(response.usage),
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> Reply:
        """异步、非流式。S3 的分类（router.py）用这个。

        为什么不直接用 chat()：chat() 是同步客户端，在 FastAPI 的 async 端点里
        调用会卡住整个事件循环（和 chat_stream() 必须用异步客户端是同一个理由）。
        为什么不用 chat_stream()：分类结果是一段 JSON，不收完整就没法校验，
        流式对它没有意义。

        response_format / max_tokens 只在传了的时候才放进请求：显式传 None
        SDK 会发一个 null 过去，provider 未必当成"没传"。
        """
        kwargs = self._base_kwargs(messages, temperature)
        if response_format is not None:
            kwargs["response_format"] = response_format
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            response = await self.async_client.chat.completions.create(**kwargs)
        except APITimeoutError as e:
            raise LLMError("timeout", str(e)) from e
        except APIError as e:
            raise LLMError("api_error", str(e)) from e

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
                **self._base_kwargs(messages, temperature),
                stream=True,
                # 不加这个，流式响应完全不带 usage——只有非流式请求默认带。
                stream_options={"include_usage": True},
            )
        except APITimeoutError as e:
            raise LLMError("timeout", str(e)) from e
        except APIError as e:
            raise LLMError("api_error", str(e)) from e

        try:
            async for chunk in stream:
                # 先取正文，再看 usage，顺序不能反。
                # 实测 Ark（2026-09-22）：最后一段正文和 usage 在同一个 chunk 里
                # （content="んにちは！"、finish_reason=stop、usage 非空），之后还会
                # 再发一个只有 usage、choices 为空的 chunk。以前先判断 usage 就 return，
                # 每个回答的最后一段正文都被丢掉了——长回答只少结尾几个字不显眼，
                # 短回答（"こんにちは！"只剩"こ"）才暴露出来。
                if chunk.choices:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield StreamChunk(delta=delta)
                if chunk.usage is not None:
                    # 第一次见到 usage 就结束：后面那个重复的 usage chunk 不再转发。
                    yield StreamChunk(usage=_parse_usage(chunk.usage))
                    return
        except APIError as e:
            # 流已经开始才出错——前面吐出去的文字保留，只是没法继续了。
            raise LLMError("stream_interrupted", str(e)) from e
        finally:
            # 调用方提前停止（用户点了停止、server.py 里 break）时，这个生成器会被
            # aclose()，走到这里。显式关掉上游的 HTTP 响应，不依赖垃圾回收什么时候
            # 顺手关——否则连接会一直挂着，上游也还在往这条连接里写。
            # 注意：这只保证"我们这边关了连接"。provider 断开后是否立刻停止生成、
            # 停止计费，是 provider 侧的行为，没有验证过（要对账单才知道）。
            await stream.aclose()


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
