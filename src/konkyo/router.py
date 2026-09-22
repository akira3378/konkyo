"""S3：把用户的问题分成四类，程序按类别决定下一步。

为什么要单独分类，而不是在一个 system prompt 里写"遇到 X 就这样、遇到 Y 就那样"：
一个大 prompt 里，模型"判断这是什么问题"这一步是隐含的——分没分对，只能读回答去猜，
没法直接测。拆出来之后分类结果是一个程序能 switch 的值，可以拿固定题库算正确率，
也可以让某些类别根本不调用模型（out_of_scope 直接回固定文案）。

分类结果必须是结构化的（JSON → Pydantic），不能是一段话：程序要拿它做分支，
"我觉得这大概是个手续问题"这种文字没法 switch。

流程：
    调用模型（带 response_format）→ Pydantic 校验
        ├─ 通过 → 用这个 route
        └─ 不通过 → 把错误原文喂回去再试一次（最多 MAX_ATTEMPTS 次）
                      └─ 还不通过 → FALLBACK_ROUTE，并标记 fallback=True
"""

import time
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

from konkyo.llm import LLM, Usage
from konkyo.prompts import ROUTER_PROMPT

# 这四个字面量同时出现在：prompts.py 的 ROUTER_PROMPT、eval/questions.yaml 的标注、
# web/src/lib/chat.ts 的 Route 类型、web/messages/*.json 的 routes 命名空间。
# 靠手动对齐（同 llm.py 的 LLMErrorCode）。
Route = Literal["document_question", "judgment_request", "draft_request", "out_of_scope"]
ROUTES: tuple[Route, ...] = get_args(Route)

# 结构化输出的三种做法，评测（eval/run_routing.py）拿同一个 prompt 比这三种：
# - prompt：只在 prompt 里写"输出 JSON"，不传 response_format
# - json_object：provider 保证输出是合法 JSON，但不管字段对不对
# - json_schema：把 schema 发给 provider，按 schema 约束生成
# 实测 Ark（2026-09-22）：prompt 里故意要求别的字段名时，json_object 照着 prompt
# 输出了错误字段；json_schema 仍然输出了 schema 规定的字段。所以默认用 json_schema。
StructuredMode = Literal["prompt", "json_object", "json_schema"]
DEFAULT_MODE: StructuredMode = "json_schema"

MAX_ATTEMPTS = 2  # 第一次 + 带错误信息重试一次

# 所有尝试都没通过校验时走这条。选 document_question：它的 prompt 就是 SYSTEM_PROMPT，
# 红线（不推测、不做个案判断）在里面，等于退回到 S2 没有分类时的行为。
# 不选 out_of_scope：把一个可能正常的问题直接拒掉，比按普通问题回答更糟。
FALLBACK_ROUTE: Route = "document_question"

# 分类只看最近几条：判断"それに必要な書類は？"这种追问要看上文，
# 但把整段对话（可能上万字）都塞给分类器，每轮都多付一遍全部历史的 token。
ROUTER_CONTEXT_MESSAGES = 3
ASSISTANT_SNIPPET_CHARS = 300  # 上文里的回答只留开头，够判断话题就行

# 正常输出 30–70 token。设上限是防 prompt 模式下模型直接开始回答问题、
# 生成几百 token 才停——超了会被截断成不合法的 JSON，算一次失败、进重试。
ROUTER_MAX_TOKENS = 256


# 分类器必须输出的结构。字段顺序是故意的：reason 在前、route 在后。
# 模型是从左往右生成的。先写一句理由再写结论，相当于让它先"想"一下；
# 先写 route 的话，结论在写理由之前就定了，理由只是事后补的说明。
# （reasoning 已经关掉了，这一句理由是唯一的"思考"空间。）
#
# 这段说明故意写成注释而不是 docstring：Pydantic 会把类的 docstring 放进
# JSON Schema 的 description，json_schema 模式下每次调用都会发给模型。
class RouteDecision(BaseModel):
    # extra="forbid"：多出来的字段也算不合格，生成的 JSON Schema 里对应
    # additionalProperties: false——strict 模式的 schema 要求这一条。
    model_config = ConfigDict(extra="forbid")

    reason: str
    route: Route


@dataclass
class Attempt:
    raw: str  # 模型的原始输出，评测时用来看到底错成了什么样
    error: str | None  # None = 通过校验


@dataclass
class RouteResult:
    route: Route
    decision: RouteDecision | None  # None = 全部尝试都没通过校验，route 是 FALLBACK_ROUTE
    attempts: list[Attempt] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(0, 0, 0, 0))
    latency_ms: float = 0.0

    @property
    def fallback(self) -> bool:
        return self.decision is None

    @property
    def first_attempt_ok(self) -> bool:
        return bool(self.attempts) and self.attempts[0].error is None


def response_format_for(mode: StructuredMode) -> dict[str, Any] | None:
    if mode == "prompt":
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    # schema 从 Pydantic 模型生成，不手写第二份：手写的话，两份迟早对不上，
    # 出现"provider 按 A 生成、我们按 B 校验"这种很难查的失败。
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "route_decision",
            "schema": RouteDecision.model_json_schema(),
            "strict": True,
        },
    }


def build_router_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """把最近几条对话包进一条 user 消息里，而不是原样作为多轮对话发过去。

    原样发过去，模型看到的是"用户在问我问题"，容易直接开始回答（prompt 模式下
    就是一次格式失败）。包成 <conversation> 数据之后，任务就只是"给这段数据分类"。
    这也让问题里写的"忽略之前的指示"更像是被分类的内容，而不是给分类器的指令——
    但这不是安全边界：分类被带偏，最坏是选错回答用 prompt，而每个回答用 prompt
    都带着同一套红线（见 prompts.py）。
    """
    recent = history[-ROUTER_CONTEXT_MESSAGES:]
    parts = []
    for m in recent:
        content = m["content"]
        if m["role"] == "assistant":
            content = content[:ASSISTANT_SNIPPET_CHARS]
        parts.append(f"<{m['role']}>\n{content}\n</{m['role']}>")
    conversation = "<conversation>\n" + "\n".join(parts) + "\n</conversation>"
    return [
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": conversation},
    ]


def _describe(e: ValidationError) -> str:
    """把 Pydantic 的错误压成一行，喂回给模型。

    只给字段位置和原因，不给 input_value：那是模型自己刚写的东西，
    原样再贴一遍只是浪费 token（它已经作为上一条 assistant 消息在对话里了）。
    """
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
        for err in e.errors(include_url=False)
    )


async def classify(
    llm: LLM,
    history: list[dict[str, str]],
    *,
    mode: StructuredMode = DEFAULT_MODE,
    max_attempts: int = MAX_ATTEMPTS,
) -> RouteResult:
    """给最后一条 user 消息分类。

    LLMError（超时、API 报错）不在这里接住，直接往上抛：那是"调用本身失败了"，
    接下来的回答调用大概率也会失败，退回 fallback 继续跑只会再报一次错。
    这里只处理"调用成功了、但输出不合格式"这一种失败。
    """
    messages = build_router_messages(history)
    response_format = response_format_for(mode)
    result = RouteResult(route=FALLBACK_ROUTE, decision=None)
    start = time.monotonic()

    for _ in range(max_attempts):
        reply = await llm.complete(
            messages,
            # 分类要的是稳定：同一个问题每次都该分到同一类。
            temperature=0,
            response_format=response_format,
            max_tokens=ROUTER_MAX_TOKENS,
        )
        result.usage = result.usage + reply.usage
        try:
            # 故意不做任何清洗（去掉 ```json 围栏之类）：评测要量的就是
            # 模型原始输出守不守格式。清洗属于"防御解析"，S4 再讲。
            decision = RouteDecision.model_validate_json(reply.text.strip())
        except ValidationError as e:
            error = _describe(e)
            result.attempts.append(Attempt(raw=reply.text, error=error))
            # 带着错误原文重试，比原样再问一遍有效：模型能看到自己错在哪。
            messages = [
                *messages,
                {"role": "assistant", "content": reply.text},
                {
                    "role": "user",
                    "content": f"この出力は形式が正しくありません（{error}）。"
                    "指定の形式の JSON オブジェクトだけを出力し直してください。",
                },
            ]
            continue

        result.attempts.append(Attempt(raw=reply.text, error=None))
        result.decision = decision
        result.route = decision.route
        break

    result.latency_ms = (time.monotonic() - start) * 1000
    return result
