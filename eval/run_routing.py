"""S3 评测：路由正确率 + 结构化输出失败率。

跑法：
    uv run python eval/run_routing.py                          # 三种结构化方式都跑，每题 1 次
    uv run python eval/run_routing.py --modes json_schema --repeat 3

打真实 API（不是 mock）：要量的就是真实模型守不守格式、分得对不对。
三种方式用同一个 prompt、同一套题，唯一的变量是 response_format（见 router.py）。
按顺序一次一题地跑，不并发：并发会让延迟数字互相干扰。

指标定义（EVALUATION.md 里用的就是这几个）：
- 路由正确率 = 分对的调用数 / 总调用数。走了 fallback 的一律算错，
  哪怕 fallback 的类别碰巧和标注一样——那不是分类器分对的。
- 结构化失败率（首次）= 第一次输出没通过 RouteDecision 校验的调用数 / 总调用数
- 结构化失败率（重试后）= 重试后仍不合格、走了 fallback 的调用数 / 总调用数
- 分类延迟 p50/p95：一次 classify()（含重试）的耗时，也就是分类给首 token 延迟加了多少

每次调用的原始输出存进 eval/results/（已 gitignore），分错的题可以回去看模型到底写了什么。
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import get_args

import yaml

from konkyo.llm import LLM
from konkyo.router import ROUTES, StructuredMode, classify

EVAL_DIR = Path(__file__).parent
DATASET = EVAL_DIR / "questions.yaml"
RESULTS_DIR = EVAL_DIR / "results"
MODES: tuple[StructuredMode, ...] = get_args(StructuredMode)


def load_dataset(path: Path) -> tuple[dict, list[dict]]:
    """读题库并检查格式。标注写错（类别拼错、id 重复）要在打 API 之前就报出来。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = data["items"]
    ids = [item["id"] for item in items]
    duplicated = [i for i, c in Counter(ids).items() if c > 1]
    if duplicated:
        raise SystemExit(f"题库里 id 重复：{duplicated}")
    for item in items:
        if item["route"] not in ROUTES:
            raise SystemExit(f"{item['id']} 的 route 不是合法类别：{item['route']!r}")
    return data, items


def git_revision() -> str:
    """记下是哪一版代码跑出来的数字。带 -dirty 表示有没提交的改动。"""
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{rev}-dirty" if dirty else rev


def p50_p95(values: list[float]) -> tuple[float, float]:
    # inclusive 就是线性插值，和 scripts/bench_latency.py 的 percentile() 同一种算法。
    if len(values) < 2:
        return values[0], values[0]
    q = statistics.quantiles(values, n=20, method="inclusive")
    return q[9], q[18]


async def run_mode(llm: LLM, items: list[dict], mode: StructuredMode, repeat: int) -> list[dict]:
    records = []
    for item in items:
        for r in range(repeat):
            history = [{"role": "user", "content": item["question"]}]
            result = await classify(llm, history, mode=mode)
            correct = not result.fallback and result.route == item["route"]
            record = {
                "mode": mode,
                "id": item["id"],
                "repeat": r,
                "gold": item["route"],
                "route": result.route,
                "fallback": result.fallback,
                "correct": correct,
                "first_attempt_ok": result.first_attempt_ok,
                "reason": result.decision.reason if result.decision else None,
                "attempts": [{"raw": a.raw, "error": a.error} for a in result.attempts],
                "latency_ms": round(result.latency_ms),
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            }
            records.append(record)
            mark = "✓" if correct else "✗"
            first = "ok" if result.first_attempt_ok else "NG"
            predicted = "(fallback)" if result.fallback else result.route
            print(
                f"  [{mode}] {item['id']} #{r + 1}  {mark} {predicted:<18} "
                f"首次格式={first}  {result.latency_ms:.0f}ms"
            )
    return records


def summarize(mode: str, records: list[dict]) -> None:
    n = len(records)
    correct = sum(r["correct"] for r in records)
    first_fail = sum(not r["first_attempt_ok"] for r in records)
    final_fail = sum(r["fallback"] for r in records)
    p50, p95 = p50_p95([r["latency_ms"] for r in records])
    in_med = statistics.median(r["input_tokens"] for r in records)
    out_med = statistics.median(r["output_tokens"] for r in records)

    print(f"\n=== {mode}（{n} 次调用）===")
    print(f"路由正确率            {correct}/{n} = {correct / n:.0%}")
    print(f"结构化失败率（首次）  {first_fail}/{n} = {first_fail / n:.0%}")
    print(f"结构化失败率（重试后）{final_fail}/{n} = {final_fail / n:.0%}")
    print(f"分类延迟              p50={p50:.0f}ms  p95={p95:.0f}ms")
    print(f"token（中位数）       in={in_med:.0f}  out={out_med:.0f}")

    # 混淆矩阵：行是标注，列是分类器的结果。看"错成了什么"比只看正确率有用——
    # judgment_request 被分成 document_question，和被分成 out_of_scope，后果完全不一样。
    columns = [*ROUTES, "(fallback)"]
    print("\n混淆矩阵（行=标注，列=结果）")
    print(" " * 20 + "".join(f"{c[:10]:>12}" for c in columns))
    for gold in ROUTES:
        row = [r for r in records if r["gold"] == gold]
        counts = Counter("(fallback)" if r["fallback"] else r["route"] for r in row)
        print(f"{gold:<20}" + "".join(f"{counts.get(c, 0):>12}" for c in columns))

    wrong = [r for r in records if not r["correct"]]
    if wrong:
        print("\n分错的：")
        for r in wrong:
            got = "(fallback)" if r["fallback"] else r["route"]
            detail = r["reason"] or r["attempts"][-1]["error"]
            print(f"  {r['id']} #{r['repeat'] + 1}  标注={r['gold']}  结果={got}  {detail}")

    format_fails = [r for r in records if not r["first_attempt_ok"]]
    if format_fails:
        print("\n首次格式不合格的（原始输出开头）：")
        for r in format_fails:
            raw = r["attempts"][0]["raw"].replace("\n", "\\n")[:80]
            print(f"  {r['id']} #{r['repeat'] + 1}  {r['attempts'][0]['error']}  | {raw}")

    # repeat > 1 时：同一题每次分到的类别不一样，说明分类不稳定（temperature=0 也可能发生）。
    by_id: dict[str, set[str]] = {}
    for r in records:
        by_id.setdefault(r["id"], set()).add("(fallback)" if r["fallback"] else r["route"])
    unstable = {i: routes for i, routes in by_id.items() if len(routes) > 1}
    if unstable:
        print("\n多次结果不一致的：")
        for i, routes in unstable.items():
            print(f"  {i}  {sorted(routes)}")


async def amain() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default=",".join(MODES), help=f"逗号分隔：{', '.join(MODES)}")
    parser.add_argument("--repeat", type=int, default=1, help="每题跑几次")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        raise SystemExit(f"不认识的模式：{unknown}，可选：{MODES}")

    data, items = load_dataset(args.dataset)
    llm = LLM()
    meta = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "model": llm.config.model,
        "code": git_revision(),
        "dataset": str(args.dataset.relative_to(EVAL_DIR.parent)),
        "dataset_version": data.get("version"),
        "labels_reviewed": bool(data.get("labels_reviewed")),
        "modes": modes,
        "repeat": args.repeat,
        "items": len(items),
    }
    print(json.dumps(meta, ensure_ascii=False))
    if not meta["labels_reviewed"]:
        print(
            "⚠ labels_reviewed=false：标注还没人工确认过，这次的数字不能写进 EVALUATION.md",
            file=sys.stderr,
        )

    all_records: dict[str, list[dict]] = {}
    try:
        for mode in modes:
            print(f"\n跑 {mode} ...")
            all_records[mode] = await run_mode(llm, items, mode, args.repeat)
    finally:
        # 同 bench_latency.py：不显式关连接池，退出时会报一堆无害但误导人的噪音。
        await llm.async_client.close()

    for mode, records in all_records.items():
        summarize(mode, records)

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"routing-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": meta}, ensure_ascii=False) + "\n")
        for records in all_records.values():
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n原始结果：{out.relative_to(EVAL_DIR.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
