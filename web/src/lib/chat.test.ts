import { afterEach, describe, expect, it, vi } from "vitest";
import { type StreamCallbacks, streamChat, toRequestHistory, type UIMessage } from "./chat";

/** 拼一个假的 SSE 响应：把每个字符串片段当作一次独立的 fetch 读取结果，
 * 用来验证 chat.ts 自己写的"按 \n\n 切消息"逻辑——包括消息被硬切成
 * 两半、横跨两次 read() 的情况。
 */
function makeSSEResponse(rawChunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of rawChunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, { status });
}

function makeCallbacks(): StreamCallbacks {
  return {
    onRoute: vi.fn(),
    onDelta: vi.fn(),
    onUsage: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("streamChat", () => {
  it("按顺序把 route/delta/usage/done 事件转发给对应的回调", async () => {
    const body = [
      'event: route\ndata: {"route":"document_question","fallback":false}\n\n',
      'event: delta\ndata: {"delta":"你"}\n\n',
      'event: delta\ndata: {"delta":"好"}\n\n',
      'event: usage\ndata: {"input_tokens":1,"output_tokens":2,"cache_hit_tokens":0,"cache_miss_tokens":1}\n\n',
      "event: done\ndata: {}\n\n",
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeSSEResponse([body])),
    );

    const callbacks = makeCallbacks();
    await streamChat([{ role: "user", content: "hi" }], new AbortController().signal, callbacks);

    expect(callbacks.onRoute).toHaveBeenCalledExactlyOnceWith("document_question", false);
    expect(callbacks.onDelta).toHaveBeenNthCalledWith(1, "你");
    expect(callbacks.onDelta).toHaveBeenNthCalledWith(2, "好");
    expect(callbacks.onUsage).toHaveBeenCalledWith({
      input_tokens: 1,
      output_tokens: 2,
      cache_hit_tokens: 0,
      cache_miss_tokens: 1,
    });
    expect(callbacks.onDone).toHaveBeenCalledOnce();
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it("一条 SSE 消息被硬切成两次 read() 也能正确拼回来", async () => {
    // fetch 读到的是原始字节，不保证一次 read() 刚好是一条完整消息——
    // 这里故意在消息中间切一刀，模拟这种情况。
    const fullMessage = 'event: delta\ndata: {"delta":"分片测试"}\n\n';
    const cutPoint = 20;
    const firstHalf = fullMessage.slice(0, cutPoint);
    const secondHalf = fullMessage.slice(cutPoint);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeSSEResponse([firstHalf, secondHalf])),
    );

    const callbacks = makeCallbacks();
    await streamChat([], new AbortController().signal, callbacks);

    expect(callbacks.onDelta).toHaveBeenCalledExactlyOnceWith("分片测试");
  });

  it("HTTP 非 200 时报 http code，带上状态码作为 detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeSSEResponse([], 500)));

    const callbacks = makeCallbacks();
    await streamChat([], new AbortController().signal, callbacks);

    expect(callbacks.onError).toHaveBeenCalledWith("http", "500");
  });

  it("fetch 直接失败（连不上）时报 network code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const callbacks = makeCallbacks();
    await streamChat([], new AbortController().signal, callbacks);

    expect(callbacks.onError).toHaveBeenCalledWith("network", "Failed to fetch");
  });

  it("用户主动 abort 时静默返回，不当成错误报出去", async () => {
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("The user aborted a request.", "AbortError")),
    );

    const callbacks = makeCallbacks();
    await streamChat([], controller.signal, callbacks);

    expect(callbacks.onError).not.toHaveBeenCalled();
    expect(callbacks.onDone).not.toHaveBeenCalled();
  });

  it("fallback=true 原样转给回调：界面要标出这一轮是分类失败后按普通问题回答的", async () => {
    const body = 'event: route\ndata: {"route":"document_question","fallback":true}\n\n';
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeSSEResponse([body])));

    const callbacks = makeCallbacks();
    await streamChat([], new AbortController().signal, callbacks);

    expect(callbacks.onRoute).toHaveBeenCalledWith("document_question", true);
  });

  it("不认识的 route 不转给回调，而不是猜一个类别显示出来", async () => {
    const body = 'event: route\ndata: {"route":"some_future_route","fallback":false}\n\n';
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeSSEResponse([body])));

    const callbacks = makeCallbacks();
    await streamChat([], new AbortController().signal, callbacks);

    expect(callbacks.onRoute).not.toHaveBeenCalled();
  });

  it("后端给了个前端不认识的 error code 时兜底成 unknown，而不是把陌生字符串硬塞给翻译层", async () => {
    const body = 'event: error\ndata: {"code":"some_future_code","detail":"x"}\n\n';
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeSSEResponse([body])));

    const callbacks = makeCallbacks();
    await streamChat([], new AbortController().signal, callbacks);

    expect(callbacks.onError).toHaveBeenCalledWith("unknown", "x");
  });
});

describe("toRequestHistory", () => {
  it("出错的那一轮（问题 + 回答）整轮不发给后端，错误文案不会变成模型的历史", () => {
    // 回归测试：以前错误文案被拼进 assistant 的 content，下一轮原样发给了模型。
    const messages: UIMessage[] = [
      { role: "user", content: "Q1" },
      { role: "assistant", content: "A1" },
      { role: "user", content: "Q2" },
      { role: "assistant", content: "半截", error: { code: "stream_interrupted", detail: "x" } },
      { role: "user", content: "Q3" },
    ];

    expect(toRequestHistory(messages)).toEqual([
      { role: "user", content: "Q1" },
      { role: "assistant", content: "A1" },
      { role: "user", content: "Q3" },
    ]);
  });

  it("用户主动停止留下的半截回答照常发（没有 error）", () => {
    const messages: UIMessage[] = [
      { role: "user", content: "Q1" },
      { role: "assistant", content: "被停止的半截回答" },
      { role: "user", content: "Q2" },
    ];

    expect(toRequestHistory(messages)).toEqual(messages);
  });

  it("空的 assistant 占位不发", () => {
    const messages: UIMessage[] = [
      { role: "user", content: "Q1" },
      { role: "assistant", content: "" },
    ];

    expect(toRequestHistory(messages)).toEqual([{ role: "user", content: "Q1" }]);
  });

  it("发出去的每条只有 role 和 content，不带界面用的字段", () => {
    const messages: UIMessage[] = [
      { role: "user", content: "Q1" },
      { role: "assistant", content: "A1", route: { route: "document_question", fallback: false } },
    ];

    for (const m of toRequestHistory(messages)) {
      expect(Object.keys(m).sort()).toEqual(["content", "role"]);
    }
  });
});
