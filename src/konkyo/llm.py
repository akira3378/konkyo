"""LLM 调用的最小封装。

S1 的核心认知：
    LLM 就是一个远程 HTTP API。和前端 fetch 一个 REST 接口没有本质区别。
    区别只有两个：① 输出不确定 ② 请求里带着很大的 context。

    而且模型**永远不会执行任何东西**。它只返回文本。
    执行的永远是你的代码。（这条到 S4 讲 Tool Calling 时会变得非常重要）
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from openai import APIError, APITimeoutError, OpenAI

from konkyo.config import LLMConfig

# deepseek-flash 官方 API 的价格（美元 / 100 万 token，繁忙时段。off-peak 是一半）
# 来源：https://api-docs.deepseek.com/quick_start/pricing
# 现在用的是火山方舟转发（见下面 ARK_PRICE），但这张表故意不删——
# 003 号判断：之后可能切回 DeepSeek 官方 API，到时候不用重查一遍价格。
DEEPSEEK_PRICE_PER_MTOK = {
    "currency": "USD",
    "input_cache_hit": 0.006,
    "input_cache_miss": 0.30,
    "output": 1.20,
}

# 火山方舟（Volcengine Ark）deepseek-v4-1-flash 的价格（元 / 100 万 token）。
# 来源：https://www.volcengine.com/docs/82379/1099320 （2026-09-20 查）
# 分高峰/空闲两档，峰值定义：北京时间周一至周五 09:00–12:00、14:00–18:00。
ARK_PRICE_PER_MTOK = {
    "currency": "CNY",
    "peak": {"input_cache_hit": 0.04, "input_cache_miss": 2.00, "output": 8.00},
    "offpeak": {"input_cache_hit": 0.02, "input_cache_miss": 1.00, "output": 4.00},
}

_ARK_TZ = ZoneInfo("Asia/Shanghai")


def _is_ark(base_url: str) -> bool:
    return "volces.com" in base_url


def _is_ark_peak_hour(at: datetime | None = None) -> bool:
    """判断给定时间是否落在火山方舟的高峰时段（北京时间）。"""
    now = (at or datetime.now(_ARK_TZ)).astimezone(_ARK_TZ)
    if now.weekday() >= 5:  # 周六=5, 周日=6
        return False
    return (9 <= now.hour < 12) or (14 <= now.hour < 18)


def _rates_for(base_url: str) -> tuple[dict, str]:
    """按 base_url 选价目表。返回 (单价表, 货币)。

    这里用 base_url 里的域名分辨 provider，不是完美方案（换个转发域名就认不出来），
    但对现在"只有两家、手动配置"的规模够用了。要更严谨可以在 LLMConfig 里显式加
    一个 provider 字段，等真的需要再加，S1 不提前做这个抽象。
    """
    if _is_ark(base_url):
        rates = ARK_PRICE_PER_MTOK["peak"] if _is_ark_peak_hour() else ARK_PRICE_PER_MTOK["offpeak"]
        return rates, ARK_PRICE_PER_MTOK["currency"]
    return DEEPSEEK_PRICE_PER_MTOK, DEEPSEEK_PRICE_PER_MTOK["currency"]


_CURRENCY_SYMBOL = {"USD": "$", "CNY": "¥"}


@dataclass
class Usage:
    """一次调用花了多少。

    为什么 S1 就要算这个：
    "怎么控制 LLM 成本" 是面试必问题。只会答"截断历史"是入门线。
    要能答：多轮对话的 messages 前缀是重复的，命中前缀缓存时
    输入价格差几十倍，所以要把 system prompt 和工具定义放最前面、保持前缀稳定。

    cost / currency 在构造时算好存进来（而不是做成 property 现算），
    因为价目表要按 provider（base_url）选，Usage 自己不知道是谁发的请求——
    算价格这一步放在 llm.py 里知道 base_url 的地方做。
    """

    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int  # 命中前缀缓存的 input token
    cache_miss_tokens: int
    cost: float = 0.0
    currency: str = "USD"

    def __str__(self) -> str:
        hit_rate = self.cache_hit_tokens / self.input_tokens * 100 if self.input_tokens else 0
        symbol = _CURRENCY_SYMBOL.get(self.currency, self.currency + " ")
        return (
            f"in={self.input_tokens} (缓存命中 {self.cache_hit_tokens}, {hit_rate:.0f}%) "
            f"out={self.output_tokens} "
            f"cost≈{symbol}{self.cost:.6f}"
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
        # deepseek-v4.1-flash 默认会先生成一段隐藏思维链再回答，这段思考本身
        # 按输出 token 计费，之前实测占了 out 的九成以上——S1 只是简单问答，
        # 不需要这个。这个字段是 Ark 的扩展（OpenAI SDK 不认识，走 extra_body），
        # 不确定 DeepSeek 官方接口是否认同一个字段名，所以只在走 Ark 时才加，
        # 换回 DeepSeek 官方时这行自动不生效，不会因为字段不认识报错。
        extra_body = {"thinking": {"type": "disabled"}} if _is_ark(self.config.base_url) else None

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                extra_body=extra_body,
            )
        except APITimeoutError as e:
            raise RuntimeError("请求超时。网络问题，或者 context 太长。") from e
        except APIError as e:
            # 不要把异常吞掉。把能帮助排查的信息带出去。
            raise RuntimeError(f"API 调用失败：{e}") from e

        return Reply(
            text=response.choices[0].message.content or "",
            usage=_parse_usage(response.usage, self.config.base_url),
        )


def _parse_usage(raw: Any, base_url: str) -> Usage:
    """从响应里取用量，并按 base_url 对应的 provider 算价格。

    缓存命中数字的字段名两家不一样，不是同一套扩展字段：
    - DeepSeek 官方 API：扁平字段 prompt_cache_hit_tokens / prompt_cache_miss_tokens
    - Ark（标准 OpenAI usage 结构）：嵌套在 prompt_tokens_details.cached_tokens 里，
      而且只给命中数，没有"未命中数"字段，未命中 = 总数 - 命中，要自己算
    两种都试，都取不到就退化成"全部未命中"——这样再换一家 provider 也不会崩，
    只是成本算得保守一点。这就是"provider 可替换"在代码里的具体样子。
    """
    rates, currency = _rates_for(base_url)
    if raw is None:
        return Usage(0, 0, 0, 0, 0.0, currency)

    input_tokens = getattr(raw, "prompt_tokens", 0) or 0
    output_tokens = getattr(raw, "completion_tokens", 0) or 0

    hit = getattr(raw, "prompt_cache_hit_tokens", None)
    miss = getattr(raw, "prompt_cache_miss_tokens", None)

    if hit is None:
        details = getattr(raw, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        if cached is not None:
            hit, miss = cached, input_tokens - cached

    if hit is None or miss is None:
        hit, miss = 0, input_tokens

    cost = (
        hit * rates["input_cache_hit"]
        + miss * rates["input_cache_miss"]
        + output_tokens * rates["output"]
    ) / 1_000_000

    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        cost=cost,
        currency=currency,
    )
