/**
 * 手写的 SSE 客户端。
 *
 * 没有用 EventSource：它只能发 GET，没法在请求体里带 messages 数组。
 * 改用 fetch() + ReadableStream，自己按 SSE 格式（空行分隔）切消息、解析。
 * 这部分逻辑正是 Vercel AI SDK 的 useChat 帮你封装掉的东西。
 */

export type ChatRole = "system" | "user" | "assistant";

export type ChatMessage = {
  role: ChatRole;
  content: string;
};

export type StreamCallbacks = {
  onDelta: (text: string) => void;
  onUsage: (usage: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function streamChat(
  messages: ChatMessage[],
  signal: AbortSignal,
  callbacks: StreamCallbacks,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
      signal,
    });
  } catch (e) {
    if (signal.aborted) return; // 用户主动取消，不是错误
    callbacks.onError(`连不上后端：${(e as Error).message}`);
    return;
  }

  if (!response.ok || !response.body) {
    callbacks.onError(`请求失败：HTTP ${response.status}`);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      // fetch 读到的是原始字节，不是按消息边界切好的——
      // 一次 read() 可能拿到半条消息，也可能拿到好几条，所以要攒在
      // buffer 里，按 SSE 的消息分隔符（空行，即 "\n\n"）自己切。
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawMessage = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        dispatch(rawMessage, callbacks);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (e) {
    if (signal.aborted) return; // abort() 会让 reader.read() 抛异常，这是预期行为
    callbacks.onError(`读取中断：${(e as Error).message}`);
  }
}

function dispatch(rawMessage: string, callbacks: StreamCallbacks): void {
  let event = "message";
  let data = "";
  for (const line of rawMessage.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice("event: ".length);
    else if (line.startsWith("data: ")) data = line.slice("data: ".length);
  }
  if (!data) return;

  const payload = JSON.parse(data) as Record<string, string>;
  switch (event) {
    case "delta":
      callbacks.onDelta(payload.delta ?? "");
      break;
    case "usage":
      callbacks.onUsage(payload.usage ?? "");
      break;
    case "error":
      callbacks.onError(payload.error ?? "未知错误");
      break;
    case "done":
      callbacks.onDone();
      break;
  }
}

export const SYSTEM_PROMPT =
  "あなたは日本の公的文書について答えるアシスタントです。" +
  "根拠が確認できないことは推測せず、「確認できません」と答えてください。";
