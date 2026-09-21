import { useTranslations } from "next-intl";
import { useRef, type KeyboardEvent } from "react";

type Props = {
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
};

export function Composer({ disabled, streaming, onSend, onStop }: Props) {
  const t = useTranslations("chat");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 中文/日文这类需要候选词的输入法，选字确认也是按 Enter。
  // 浏览器会先后发 compositionstart → (选字中) → compositionend → keydown，
  // 选字确认的那个 Enter 既结束了输入法组字、也会被当成"提交"的 Enter 捕获到——
  // 不单独处理就会出现"明明只是在选字，回车就把消息发出去了"。
  // 用 compositionstart/end 自己记一个标志位，Enter 时先查这个标志位再决定要不要提交。
  const isComposingRef = useRef(false);

  function submit() {
    const value = textareaRef.current?.value.trim();
    if (!value) return;
    onSend(value);
    if (textareaRef.current) textareaRef.current.value = "";
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey) return;
    // 双重保险：isComposingRef 是我们自己记的；e.nativeEvent.isComposing 和
    // keyCode === 229 是浏览器原生信号，部分 Safari 版本 compositionend 和
    // keydown 的触发顺序不稳定，只信一个会漏。三个只要有一个说"还在组字"，
    // 就不提交。
    if (isComposingRef.current || e.nativeEvent.isComposing || e.keyCode === 229) {
      return;
    }
    e.preventDefault();
    submit();
  }

  return (
    <div className="flex items-end gap-2 border-t border-zinc-200 bg-white/80 p-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
      <textarea
        ref={textareaRef}
        rows={1}
        placeholder={t("placeholder")}
        disabled={disabled}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => {
          isComposingRef.current = true;
        }}
        onCompositionEnd={() => {
          isComposingRef.current = false;
        }}
        className="max-h-40 flex-1 resize-none rounded-xl border border-zinc-200 bg-white px-3 py-2.5 text-[15px] outline-none focus:border-indigo-400 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900"
      />
      {streaming ? (
        <button
          onClick={onStop}
          className="shrink-0 rounded-xl bg-zinc-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {t("stop")}
        </button>
      ) : (
        <button
          onClick={submit}
          disabled={disabled}
          className="shrink-0 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
        >
          {t("send")}
        </button>
      )}
    </div>
  );
}
