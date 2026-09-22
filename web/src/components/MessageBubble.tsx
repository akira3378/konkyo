import { useTranslations } from "next-intl";
import type { UIMessage } from "@/lib/chat";

type Props = {
  message: UIMessage;
  streaming?: boolean;
};

export function MessageBubble({ message, streaming }: Props) {
  const tErrors = useTranslations("errors");
  const tRoutes = useTranslations("routes");
  const isUser = message.role === "user";
  const { error, route } = message;

  // headline 永远是翻译过的（查 messages/*.json 的 errors 命名空间）；
  // detail 是后端/浏览器的原始报错，故意不翻译，只附在括号里给排查用。
  let errorText: string | null = null;
  if (error) {
    const headline =
      error.code === "http" ? tErrors("http", { status: error.detail ?? "?" }) : tErrors(error.code);
    errorText = error.detail && error.code !== "http" ? `${headline}（${error.detail}）` : headline;
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap ${
          isUser
            ? "bg-indigo-600 text-white rounded-br-sm"
            : "bg-zinc-100 text-zinc-900 rounded-bl-sm dark:bg-zinc-800 dark:text-zinc-100"
        }`}
      >
        {route && (
          // 这一轮被分到了哪一类（S3）。放在回答上面，让用户知道"为什么这样回答"：
          // 比如被判为个案判断请求，回答开头就会先说明不能判断。
          <p className="mb-1 text-[11px] font-medium text-zinc-400">
            {tRoutes(route.route)}
            {route.fallback && ` · ${tRoutes("fallback")}`}
          </p>
        )}
        {message.content}
        {streaming && (
          <span className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] animate-pulse bg-current align-middle" />
        )}
        {errorText && (
          // 错误单独一行显示，不是 content 的一部分——content 会作为历史发回给模型，
          // 这行字不该跟着发过去（见 lib/chat.ts 的 toRequestHistory）。
          <p
            role="alert"
            className={`text-xs text-red-600 dark:text-red-400 ${message.content ? "mt-2" : ""}`}
          >
            {errorText}
          </p>
        )}
      </div>
    </div>
  );
}
