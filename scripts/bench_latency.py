"""S2 的"首 token 延迟"基准：同一个问题跑 N 次，量出 p50/p95，顺带记 token 用量。

跑法：
    uv run python scripts/bench_latency.py                    # S1 非流式 vs S2 流式
    uv run python scripts/bench_latency.py --compare-routing  # S2 vs S3（先分类）的首 token

对比两个模式，不是随便选的两个数字：
- 非流式（S1 的 LLM.chat()）：用户从提问到看见任何文字，等的是"完整回复生成完"。
  这就是流式要解决的那个问题本身的基线——S1 没有"首字"这个概念，
  第一个字和最后一个字同时出现，所以非流式的"首字延迟"= 完整生成时间。
- 流式（S2 的 LLM.chat_stream()）：用户等的是"第一个字出现"，不是全部生成完。

固定问题、固定次数、直接打真实 API——这是 EVALUATION.md 自己定的规矩
（"手动试几个例子不叫评测，要有固定数据集、能自动跑、可复现"）。

--compare-routing（S3 起）：量"先分类再回答"给首 token 加了多少时间。
- S2：带 SYSTEM_PROMPT 直接流式回答（S2 服务端的做法）
- S3：workflow.run_turn()，先分类、再用同一个 SYSTEM_PROMPT 流式回答
两边回答用的 prompt 一样，差别只有分类那一步。两种交替跑（S2、S3、S2、S3…）：
EVALUATION.md 里记过同一份代码两天差一倍，分开两段时间跑的话，差异可能来自外部。
"""

import argparse
import asyncio
import statistics
import time

from konkyo.llm import LLM, Usage
from konkyo.prompts import SYSTEM_PROMPT
from konkyo.router import RouteResult
from konkyo.workflow import run_turn

QUESTION = (
    "在留資格「留学」から「技術・人文知識・国際業務」への変更について、"
    "必要な書類を教えてください。"
)
N = 15


def percentile(data: list[float], p: float) -> float:
    data = sorted(data)
    k = (len(data) - 1) * p
    f = int(k)
    c = min(f + 1, len(data) - 1)
    if f == c:
        return data[f]
    return data[f] + (data[c] - data[f]) * (k - f)


def bench_non_streaming(llm: LLM, n: int) -> tuple[list[float], list[Usage]]:
    latencies = []
    usages = []
    for i in range(n):
        messages = [{"role": "user", "content": QUESTION}]
        start = time.monotonic()
        reply = llm.chat(messages)
        latencies.append((time.monotonic() - start) * 1000)
        usages.append(reply.usage)
        print(f"  [非流式 {i + 1}/{n}] {latencies[-1]:.0f}ms  {reply.usage}")
    return latencies, usages


async def bench_streaming(llm: LLM, n: int) -> tuple[list[float], list[Usage]]:
    latencies = []
    usages = []
    for i in range(n):
        messages = [{"role": "user", "content": QUESTION}]
        start = time.monotonic()
        first_token_at = None
        usage = None
        # 拿到首字时间之后不提前 break：让流正常跑完再关闭连接，
        # 不然上游还在继续生成、我们却不读了，等于白占资源、还可能因为
        # 没正常关闭连接而在这台机器上攒一堆半死不活的 httpx 连接。
        async for chunk in llm.chat_stream(messages):
            if chunk.delta and first_token_at is None:
                first_token_at = time.monotonic()
            if chunk.usage is not None:
                usage = chunk.usage
        latencies.append((first_token_at - start) * 1000)
        usages.append(usage)
        print(f"  [流式首token {i + 1}/{n}] {latencies[-1]:.0f}ms  {usage}")
    # 15 次迭代共用同一个 AsyncOpenAI 客户端，循环里从没显式关过它的连接池。
    # 不在这收尾，Python 解释器退出时会自己去关那些还没关掉的 httpx 连接，
    # 但那时事件循环已经不稳定了，会报一堆 "asyncgen is already running"
    # 的噪音——数据不受影响，但看着像是哪里错了，容易误导人去排查一个
    # 不存在的 bug。
    await llm.async_client.close()
    return latencies, usages


async def first_token_s2(llm: LLM) -> tuple[float, Usage | None]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": QUESTION},
    ]
    start = time.monotonic()
    first_token_at = None
    usage = None
    async for chunk in llm.chat_stream(messages):
        if chunk.delta and first_token_at is None:
            first_token_at = time.monotonic()
        if chunk.usage is not None:
            usage = chunk.usage
    return (first_token_at - start) * 1000, usage


async def first_token_s3(llm: LLM) -> tuple[float, float, str, Usage | None]:
    """返回 (首 token 延迟, 其中分类花的时间, 分到的类别, 这一轮的 token 合计)。"""
    start = time.monotonic()
    first_token_at = None
    route_ms = 0.0
    route = "?"
    usage = None
    async for event in run_turn(llm, [{"role": "user", "content": QUESTION}]):
        if isinstance(event, RouteResult):
            route_ms = event.latency_ms
            route = event.route
        elif event.usage is not None:
            usage = event.usage
        elif event.delta and first_token_at is None:
            first_token_at = time.monotonic()
    return (first_token_at - start) * 1000, route_ms, route, usage


async def bench_routing(llm: LLM, n: int) -> None:
    s2: list[float] = []
    s3: list[float] = []
    router: list[float] = []
    s2_usage: list[Usage] = []
    s3_usage: list[Usage] = []
    for i in range(n):
        ms, usage = await first_token_s2(llm)
        s2.append(ms)
        s2_usage.append(usage)
        print(f"  [S2 {i + 1}/{n}] 首token {ms:.0f}ms  {usage}")

        ms, route_ms, route, usage = await first_token_s3(llm)
        s3.append(ms)
        router.append(route_ms)
        s3_usage.append(usage)
        print(f"  [S3 {i + 1}/{n}] 首token {ms:.0f}ms（分类 {route_ms:.0f}ms, {route}）  {usage}")
    await llm.async_client.close()

    print("\n=== 结果 ===")
    for name, data in [("S2 首 token", s2), ("S3 首 token", s3), ("S3 其中分类", router)]:
        print(
            f"{name}：p50={percentile(data, 0.5):.0f}ms  p95={percentile(data, 0.95):.0f}ms  "
            f"mean={statistics.mean(data):.0f}ms"
        )
    print(f"S2 token：{summarize_usage(s2_usage)}")
    print(f"S3 token（分类 + 回答）：{summarize_usage(s3_usage)}")


def summarize_usage(usages: list[Usage]) -> str:
    ins = [u.input_tokens for u in usages]
    outs = [u.output_tokens for u in usages]
    return (
        f"in 中位数={statistics.median(ins):.0f}  "
        f"out 中位数={statistics.median(outs):.0f}（最少 {min(outs)}、最多 {max(outs)}）"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare-routing", action="store_true")
    args = parser.parse_args()

    llm = LLM()
    print(f"model={llm.config.model}  N={N}")
    print(f"问题：{QUESTION}\n")

    if args.compare_routing:
        print("交替测 S2（直接回答）和 S3（先分类）的首 token...")
        asyncio.run(bench_routing(llm, N))
        return

    print("测非流式（S1 模式，等完整回复）...")
    non_streaming, non_streaming_usage = bench_non_streaming(llm, N)

    print("\n测流式首 token（S2 模式）...")
    streaming, streaming_usage = asyncio.run(bench_streaming(llm, N))

    print("\n=== 结果 ===")
    print(
        f"非流式完整回复：p50={percentile(non_streaming, 0.5):.0f}ms  "
        f"p95={percentile(non_streaming, 0.95):.0f}ms  "
        f"mean={statistics.mean(non_streaming):.0f}ms"
    )
    print(
        f"流式首 token：  p50={percentile(streaming, 0.5):.0f}ms  "
        f"p95={percentile(streaming, 0.95):.0f}ms  "
        f"mean={statistics.mean(streaming):.0f}ms"
    )
    print(f"非流式 token：{summarize_usage(non_streaming_usage)}")
    print(f"流式 token：  {summarize_usage(streaming_usage)}")


if __name__ == "__main__":
    main()
