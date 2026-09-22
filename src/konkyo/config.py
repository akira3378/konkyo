"""配置：把环境变量集中在一个地方读。

为什么不在用到的地方直接 os.getenv()：
- key 散落在各处，将来换 provider 要改很多处
- 缺 key 时报错发生在很深的调用里，不好排查
这里在启动时就检查，缺了立刻说清楚缺哪个。
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# 从 .env 读进 os.environ。.env 已在 .gitignore 里，不会被提交。
# override=False：真实环境变量优先于 .env（部署时用环境变量覆盖）
load_dotenv(override=False)


class ConfigError(RuntimeError):
    """配置缺失。启动时抛，不要等到调用 API 才失败。"""


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"环境变量 {name} 没有设置。先 cp .env.example .env 再填。")
    return value


@dataclass(frozen=True)
class LLMConfig:
    """一个 LLM provider 的配置。

    base_url 和 model 都从环境变量来，代码里不写死任何一家。
    但"能换"只是配置层面的：真正实测过的只有 Ark（见 .env.example），
    换 provider 之后 llm.py 里的 extra_body 和 usage 解析要拿真实响应重新确认。

    三个值都必填，不给默认值：以前默认指向 DeepSeek 官方 API，
    而那条路径从没实测过——漏配时悄悄连到一个没验证过的 provider，
    不如启动时直接报缺哪个。
    """

    base_url: str
    api_key: str
    model: str

    @classmethod
    def primary(cls) -> "LLMConfig":
        return cls(
            base_url=_require("LLM_BASE_URL"),
            api_key=_require("LLM_API_KEY"),
            model=_require("LLM_MODEL"),
        )


def cors_allow_origins() -> list[str]:
    """server.py 允许哪些前端 origin 跨域调用。

    本地开发默认只放开 localhost:3000。部署到真实域名时，
    改这一个环境变量（逗号分隔多个 origin），不用碰 server.py 里的代码——
    和 LLMConfig 的 base_url/model 一个道理：会随环境变化的值不写死在代码里。
    """
    raw = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
