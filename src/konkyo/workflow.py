"""S3：一轮对话的固定流程——先分类，再交给这一类的回答方式。

    history ─→ router.classify() ─→ RouteResult
                                      │
               ┌──────────────────────┼─────────────────────┬───────────────┐
        document_question      judgment_request        draft_request     out_of_scope
               │                      │                     │               │
        SYSTEM_PROMPT          JUDGMENT_PROMPT         DRAFT_PROMPT     不调用模型
               └──────────────────────┴─────────────────────┘      （固定文案由调用方显示）
                                      │
                               llm.chat_stream()

这是 Workflow 不是 Agent：走哪几步是这里的代码写死的，模型只负责"分到哪一类"
和"写回答"，不决定下一步做什么。步骤固定、可预测，就不需要 Agent。

server.py 和 cli.py 都用这一个函数，两边的行为不会分叉。
"""

from collections.abc import AsyncIterator
from contextlib import aclosing

from konkyo.llm import LLM, StreamChunk
from konkyo.prompts import DRAFT_PROMPT, JUDGMENT_PROMPT, SYSTEM_PROMPT
from konkyo.router import Route, RouteResult, classify

# out_of_scope 不在这里：它不调用模型。回复是固定文案，显示成哪种语言由调用方决定
# （前端查 messages/*.json，CLI 直接印一句）——和错误只发 code 是同一个思路。
# 不让模型生成拒绝语：固定文案不花 token、不会幻觉、每次都一样。
HANDLER_PROMPTS: dict[Route, str] = {
    "document_question": SYSTEM_PROMPT,
    "judgment_request": JUDGMENT_PROMPT,
    "draft_request": DRAFT_PROMPT,
}


async def run_turn(
    llm: LLM, history: list[dict[str, str]]
) -> AsyncIterator[RouteResult | StreamChunk]:
    """先 yield 一个 RouteResult，再 yield 回答的 StreamChunk（正文片段…，最后是 usage）。

    最后那个 usage 是这一轮的合计（分类 + 回答），不是只有回答那一次调用。
    out_of_scope 只 yield RouteResult 和分类那一次的 usage。
    """
    route = await classify(llm, history)
    yield route

    if route.route == "out_of_scope":
        yield StreamChunk(usage=route.usage)
        return

    messages = [{"role": "system", "content": HANDLER_PROMPTS[route.route]}, *history]
    # aclosing：调用方提前停下（用户点停止）时，把关闭一路传到 llm.chat_stream()，
    # 让它的 finally 立刻关掉上游连接（见 llm.py）。
    async with aclosing(llm.chat_stream(messages)) as chunks:
        async for chunk in chunks:
            if chunk.usage is not None:
                yield StreamChunk(usage=route.usage + chunk.usage)
            else:
                yield chunk
