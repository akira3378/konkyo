"""LLM 调用的最小封装。

S1 的核心认知：
    LLM 就是一个远程 HTTP API。和前端 fetch 一个 REST 接口没有本质区别。
    区别只有两个：① 输出不确定 ② 请求里带着很大的 context。

    而且模型**永远不会执行任何东西**。它只返回文本。
    执行的永远是你的代码。（这条到 S4 讲 Tool Calling 时会变得非常重要）
"""

from dataclasses import dataclass
from typing import Any

from openai import APIError, APITimeoutError, OpenAI

from konkyo.config import LLMConfig

# deepseek-flash 的价格（美元 / 100 万 token，繁忙时段。off-peak 是一半）
# 来源：https://api-docs.deepseek.com/quick_start/pricing
# 换 provider 时这张表要跟着换 —— 所以成本计算不要写死在业务代码里。
PRICE_PER_MTOK = {
    "input_cache_hit": 0.006,
    "input_cache_miss": 0.30,
    "output": 1.20,
}


@dataclass
class Usage:
    """一次调用花了多少。

    为什么 S1 就要算这个：
    "怎么控制 LLM 成本" 是面试必问题。只会答"截断历史"是入门线。
    要能答：多轮对话的 messages 前缀是重复的，命中前缀缓存时
    输入价格差几十倍（0.006 vs 0.30），所以要把 system prompt 和
    工具定义放最前面、保持前缀稳定。
    """

    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int  # 命中前缀缓存的 input token（DeepSeek 特有字段）
    cache_miss_tokens: int

    @property
    def cost_usd(self) -> float:
        return (
            self.cache_hit_tokens * PRICE_PER_MTOK["input_cache_hit"]
            + self.cache_miss_tokens * PRICE_PER_MTOK["input_cache_miss"]
            + self.output_tokens * PRICE_PER_MTOK["output"]
        ) / 1_000_000

    def __str__(self) -> str:
        hit_rate = self.cache_hit_tokens / self.input_tokens * 100 if self.input_tokens else 0
        return (
            f"in={self.input_tokens} (缓存命中 {self.cache_hit_tokens}, {hit_rate:.0f}%) "
            f"out={self.output_tokens} "
            f"cost≈${self.cost_usd:.6f}"
        )


@dataclass
class Reply:
    text: str
    usage: Usage


class LLM:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.primary()
        # OpenAI SDK 只是 HTTP 请求的封装。base_url 一换就指向别家。
        self.client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key, timeout=60)

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.3) -> Reply:
        """发一轮对话，拿回一个回复。

        messages 是整个对话历史。注意：
        **模型不会自己记住上一轮说了什么。**
        它"记得"，是因为我们每次都把完整历史重新发过去。
        所谓"聊天记忆"，本质是程序在管理这个 list。（S2 会真正用到）
        """
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
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


def _parse_usage(raw: Any) -> Usage:
    """从响应里取用量。

    cache_hit / cache_miss 是 DeepSeek 的扩展字段，别家没有。
    所以用 getattr 取，取不到就退化成"全部未命中"——
    这样换 provider 时不会崩，只是成本算得保守一点。
    这就是"provider 可替换"在代码里的具体样子。
    """
    if raw is None:
        return Usage(0, 0, 0, 0)

    input_tokens = getattr(raw, "prompt_tokens", 0) or 0
    output_tokens = getattr(raw, "completion_tokens", 0) or 0
    hit = getattr(raw, "prompt_cache_hit_tokens", None)
    miss = getattr(raw, "prompt_cache_miss_tokens", None)

    if hit is None or miss is None:
        hit, miss = 0, input_tokens

    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
    )
