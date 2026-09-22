"""S1：终端 → Python → LLM → Python → 终端。

跑法：
    uv run python -m konkyo.cli

这一阶段只做这一件事。没有 FastAPI、没有前端、没有 Agent、没有 RAG。
完成的标准是：能解释清楚从敲回车到看见回答，中间发生了什么。
"""

import sys

from konkyo.config import ConfigError
from konkyo.llm import LLM
from konkyo.prompts import SYSTEM_PROMPT


def main() -> int:
    try:
        llm = LLM()
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 1

    print(f"model={llm.config.model}  base_url={llm.config.base_url}")
    print("输入问题，空行退出。\n")

    # messages 就是整个对话。system 放最前面且保持不变 ——
    # 这不只是习惯问题，稳定的前缀才能命中缓存（见 llm.py 的 Usage 注释）。
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            return 0

        messages.append({"role": "user", "content": question})

        try:
            reply = llm.chat(messages)
        except RuntimeError as e:
            print(f"\n出错了：{e}\n", file=sys.stderr)
            messages.pop()  # 这一轮没成功，不要把它留在历史里污染下一轮
            continue

        # 把回复也塞回历史。不这么做，模型下一轮就不知道自己说过什么。
        messages.append({"role": "assistant", "content": reply.text})

        print(f"\nAI > {reply.text}\n")
        print(f"      [{reply.usage}]\n")


if __name__ == "__main__":
    raise SystemExit(main())
