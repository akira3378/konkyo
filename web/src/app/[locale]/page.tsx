"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { Composer } from "@/components/Composer";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { MessageBubble } from "@/components/MessageBubble";
import {
  type ErrorCode,
  streamChat,
  toRequestHistory,
  type UIMessage,
  type Usage,
} from "@/lib/chat";

export default function Home() {
  const t = useTranslations("chat");
  const tApp = useTranslations("app");
  // messages 是界面上显示的整个对话。
  // 这就是 S1 注释里说的"聊天记忆本质是程序在管理这个 list"——
  // 现在管理这个 list 的从终端循环换成了这个组件的 state。
  // 不含 system prompt：那由服务端加（src/konkyo/prompts.py）。
  // 每次发送时用 toRequestHistory() 从这里挑出真正要发给后端的历史。
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const streamStartRef = useRef(0);
  const gotFirstTokenRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // messages 变化就滚到底部——不然长对话里新消息会生成在看不见的地方，
  // 用户得自己往下拉才发现回答已经开始了。
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const appendToLastAssistant = useCallback((text: string) => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, content: last.content + text };
      return next;
    });
  }, []);

  const handleSend = useCallback(
    (text: string) => {
      const withQuestion: UIMessage[] = [...messages, { role: "user", content: text }];
      // 先把用户消息 + 一个空的 assistant 占位一起放进 state。
      // 占位的 content 会在 onDelta 里被逐字追加——这就是打字机效果的来源：
      // 每次 delta 到达就触发一次 React 重渲染，不是等全部到齐才显示。
      setMessages([...withQuestion, { role: "assistant", content: "" }]);
      setStreaming(true);
      setLatencyMs(null);
      setUsage(null);
      gotFirstTokenRef.current = false;
      streamStartRef.current = performance.now();

      const controller = new AbortController();
      abortRef.current = controller;

      streamChat(toRequestHistory(withQuestion), controller.signal, {
        onDelta: (delta) => {
          if (!gotFirstTokenRef.current) {
            gotFirstTokenRef.current = true;
            setLatencyMs(Math.round(performance.now() - streamStartRef.current));
          }
          appendToLastAssistant(delta);
        },
        onUsage: (usage) => setUsage(usage),
        onDone: () => setStreaming(false),
        onError: (code: ErrorCode, detail) => {
          // 以前这里把错误文案拼进 assistant 的 content，下一轮会被当成
          // "模型说过的话"发回给模型。现在错误只挂在消息的 error 字段上：
          // 界面照样显示（MessageBubble 负责翻译），但 toRequestHistory()
          // 会把这一整轮从历史里去掉。
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { ...next[next.length - 1], error: { code, detail } };
            return next;
          });
          setStreaming(false);
        },
      });
    },
    [messages, appendToLastAssistant],
  );

  const handleStop = useCallback(() => {
    // AbortController.abort() 让底层 fetch 的 reader.read() 立刻抛异常，
    // streamChat() 里捕获到、静默返回。后端那边 request.is_disconnected()
    // 会在下一次循环感知到，停止继续问上游要 token。
    abortRef.current?.abort();
    setStreaming(false);
    // 一个 token 都还没收到就被停止——占位的空 assistant 气泡留着没意义，
    // 撤回比留一个看不出内容的空框对用户更清楚。
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant" && last.content === "") {
        return prev.slice(0, -1);
      }
      return prev;
    });
  }, []);

  return (
    <div className="mx-auto flex h-dvh w-full max-w-2xl flex-col">
      <header className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold tracking-tight">{tApp("title")}</span>
          <span className="text-xs text-zinc-400">{tApp("subtitle")}</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 font-mono text-[11px] text-zinc-400">
            {latencyMs !== null && (
              <span className="whitespace-nowrap">{t("firstToken", { ms: latencyMs })}</span>
            )}
            {usage && (
              <span className="hidden whitespace-nowrap sm:inline">
                {t("usage", {
                  input: usage.input_tokens,
                  rate: usage.input_tokens
                    ? Math.round((usage.cache_hit_tokens / usage.input_tokens) * 100)
                    : 0,
                  output: usage.output_tokens,
                })}
              </span>
            )}
          </div>
          <LocaleSwitcher />
        </div>
      </header>

      <main className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <p className="mt-10 text-center text-sm text-zinc-400">{t("emptyHint")}</p>
        )}
        {messages.map((m, i) => (
          <MessageBubble
            key={i}
            message={m}
            streaming={streaming && i === messages.length - 1 && m.role === "assistant"}
          />
        ))}
        <div ref={bottomRef} />
      </main>

      <Composer disabled={streaming} streaming={streaming} onSend={handleSend} onStop={handleStop} />
    </div>
  );
}
