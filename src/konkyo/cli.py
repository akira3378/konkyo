"""终端里直接跑一轮对话。S1 起有，S3 起和 server.py 走同一个 workflow。

跑法：
    uv run python -m konkyo.cli

S1 时这里直接调 llm.chat()。S3 加了分类之后，如果 CLI 还直接调模型，
终端里的行为（不分类、out_of_scope 也照样回答）就和浏览器里不一样了——
所以改成调 workflow.run_turn()，和 server.py 用同一个函数。

run_turn() 是 async 的，所以整个 main 也是 async：整个会话只开一个事件循环。
不能每一轮各自 asyncio.run() 一次——AsyncOpenAI 的连接池绑在第一次的事件循环上，
那个循环关掉之后，第二轮再用这个客户端会报 "Event loop is closed"。
"""

import asyncio
import sys

from konkyo.config import ConfigError
from konkyo.llm import LLM, LLMError
from konkyo.router import RouteResult
from konkyo.workflow import run_turn

OUT_OF_SCOPE_TEXT = "（这个问题不在 konkyo 的回答范围内：只回答日本公共手续相关的问题。）"


async def amain() -> int:
    try:
        llm = LLM()
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 1

    print(f"model={llm.config.model}  base_url={llm.config.base_url}")
    print("输入问题，空行退出。\n")

    # messages 就是整个对话（只有 user/assistant）。system prompt 不放这里：
    # 用哪一个要等分类完才知道，由 workflow.py 每轮加在最前面。
    history: list[dict[str, str]] = []

    while True:
        try:
            # input() 会卡住事件循环，但终端里只有这一个用户，卡住也没有别的任务受影响。
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            return 0

        history.append({"role": "user", "content": question})
        reply = ""
        usage = None

        try:
            async for event in run_turn(llm, history):
                if isinstance(event, RouteResult):
                    fallback = "  (分类失败，按普通问题回答)" if event.fallback else ""
                    print(f"\n      [route={event.route}  {event.latency_ms:.0f}ms{fallback}]")
                    if event.route == "out_of_scope":
                        reply = OUT_OF_SCOPE_TEXT
                        print(f"\nAI > {reply}", end="")
                    else:
                        print("\nAI > ", end="", flush=True)
                elif event.usage is not None:
                    usage = event.usage
                else:
                    reply += event.delta
                    print(event.delta, end="", flush=True)
        except LLMError as e:
            print(f"\n出错了：{e}\n", file=sys.stderr)
            history.pop()  # 这一轮没成功，不要把它留在历史里污染下一轮
            continue

        # 把回复也塞回历史。不这么做，模型下一轮就不知道自己说过什么。
        history.append({"role": "assistant", "content": reply})
        print(f"\n\n      [{usage}]\n")


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
