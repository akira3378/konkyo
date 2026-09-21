import { afterEach, describe, expect, it, vi } from "vitest";
import { type StreamCallbacks, streamChat } from "./chat";

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
  it("按顺序把 delta/usage/done 事件转发给对应的回调", async () => {
    const body = [
      'event: delta\ndata: {"delta":"你"}\n\n',
      'event: delta\ndata: {"delta":"好"}\n\n',
      'event: usage\ndata: {"usage":"in=1 out=2"}\n\n',
      "event: done\ndata: {}\n\n",
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeSSEResponse([body])),
    );

    const callbacks = makeCallbacks();
    await streamChat([{ role: "user", content: "hi" }], new AbortController().signal, callbacks);

    expect(callbacks.onDelta).toHaveBeenNthCalledWith(1, "你");
    expect(callbacks.onDelta).toHaveBeenNthCalledWith(2, "好");
    expect(callbacks.onUsage).toHaveBeenCalledWith("in=1 out=2");
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

  it("后端给了个前端不认识的 error code 时兜底成 unknown，而不是把陌生字符串硬塞给翻译层", async () => {
    const body = 'event: error\ndata: {"code":"some_future_code","detail":"x"}\n\n';
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeSSEResponse([body])));

    const callbacks = makeCallbacks();
    await streamChat([], new AbortController().signal, callbacks);

    expect(callbacks.onError).toHaveBeenCalledWith("unknown", "x");
  });
});
