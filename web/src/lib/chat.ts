/**
 * 手写的 SSE 客户端。
 *
 * 没有用 EventSource：它只能发 GET，没法在请求体里带 messages 数组。
 * 改用 fetch() + ReadableStream，自己按 SSE 格式（空行分隔）切消息、解析。
 * 这部分逻辑正是 Vercel AI SDK 的 useChat 帮你封装掉的东西。
 */

// 没有 "system"：system prompt 由服务端加（src/konkyo/prompts.py），
// 后端也会拒绝客户端发来的 system 消息。
export type ChatRole = "user" | "assistant";

/** 发给后端的一条消息。 */
export type ChatMessage = {
  role: ChatRole;
  content: string;
};

/**
 * 错误种类，不是错误文案。
 *
 * 这几个 code 有两个来源：网络层的三个（network/http/read_interrupted）
 * 是这个文件自己判断的；后端来的三个（timeout/api_error/stream_interrupted）
 * 原样转发自 SSE 的 error 事件（对应 src/konkyo/llm.py 的 LLMErrorCode，
 * 两边字面量必须一一对应，靠这条注释手动维护，不是自动生成的）。
 *
 * 不管来自哪一层，这里都只传 code，不传拼好的句子——具体显示成哪句话、
 * 哪种语言，是调用方（page.tsx）查 messages/*.json 的事。这层不该替
 * UI 决定"这句话应该讲成什么样"。
 */
export type ErrorCode =
  | "network"
  | "http"
  | "read_interrupted"
  | "timeout"
  | "api_error"
  | "stream_interrupted"
  | "unknown";

/** 一次调用的 token 用量。字段和 src/konkyo/llm.py 的 Usage 一一对应。
 * 后端只发数字，显示成哪种语言的句子由界面决定（和 ErrorCode 同一个思路）。 */
export type Usage = {
  input_tokens: number;
  output_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
};

/** 界面上的一条消息：比 ChatMessage 多一个只给界面看的 error。
 * 出错信息不能拼进 content——content 会作为对话历史发回给模型。 */
export type UIMessage = ChatMessage & {
  error?: { code: ErrorCode; detail?: string };
};

/**
 * 从界面上的消息里挑出这次要发给后端的对话历史。
 *
 * 出错的那一轮（带 error 的 assistant，连同它前面那条 user）整轮不发——
 * 和 CLI 里 messages.pop() 是同一条规则：没得到回答的问题不留在历史里。
 * 流到一半出错的，已经显示出来的半截回答也一起不发：它不是一个完整的回答。
 *
 * 用户主动停止留下的半截回答（没有 error）照常发：那是用户看过、
 * 自己决定打断的内容，下一轮的提问可能就是接着它问的。
 */
export function toRequestHistory(messages: UIMessage[]): ChatMessage[] {
  const history: ChatMessage[] = [];
  messages.forEach((m, i) => {
    const next = messages[i + 1];
    if (m.role === "user" && next?.role === "assistant" && next.error) return;
    if (m.role === "assistant" && (m.error || m.content === "")) return;
    history.push({ role: m.role, content: m.content });
  });
  return history;
}

export type StreamCallbacks = {
  onDelta: (text: string) => void;
  onUsage: (usage: Usage) => void;
  onDone: () => void;
  /** detail 是原始技术信息（HTTP 状态码、provider 报错原文等），不翻译，调用方决定要不要显示。 */
  onError: (code: ErrorCode, detail?: string) => void;
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
    callbacks.onError("network", (e as Error).message);
    return;
  }

  if (!response.ok || !response.body) {
    callbacks.onError("http", String(response.status));
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
    callbacks.onError("read_interrupted", (e as Error).message);
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

  const payload = JSON.parse(data) as Record<string, unknown>;
  switch (event) {
    case "delta":
      callbacks.onDelta((payload.delta as string | undefined) ?? "");
      break;
    case "usage":
      callbacks.onUsage(payload as Usage);
      break;
    case "error": {
      // 后端只保证发 code 是 LLMErrorCode 里的一个，但跨语言边界，类型
      // 到运行时就没了——防一手万一以后两边字面量没对齐，兜底到 "unknown"，
      // 而不是把一个前端没有对应翻译 key 的字符串直接传给 t()。
      const knownCodes: ErrorCode[] = [
        "network",
        "http",
        "read_interrupted",
        "timeout",
        "api_error",
        "stream_interrupted",
      ];
      const code = knownCodes.includes(payload.code as ErrorCode)
        ? (payload.code as ErrorCode)
        : "unknown";
      callbacks.onError(code, payload.detail as string | undefined);
      break;
    }
    case "done":
      callbacks.onDone();
      break;
  }
}
