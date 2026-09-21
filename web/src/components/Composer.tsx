import { useRef, type KeyboardEvent } from "react";

type Props = {
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
};

export function Composer({ disabled, streaming, onSend, onStop }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const value = textareaRef.current?.value.trim();
    if (!value) return;
    onSend(value);
    if (textareaRef.current) textareaRef.current.value = "";
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="flex items-end gap-2 border-t border-zinc-200 bg-white/80 p-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
      <textarea
        ref={textareaRef}
        rows={1}
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        disabled={disabled}
        onKeyDown={handleKeyDown}
        className="max-h-40 flex-1 resize-none rounded-xl border border-zinc-200 bg-white px-3 py-2.5 text-[15px] outline-none focus:border-indigo-400 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900"
      />
      {streaming ? (
        <button
          onClick={onStop}
          className="shrink-0 rounded-xl bg-zinc-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
        >
          停止
        </button>
      ) : (
        <button
          onClick={submit}
          disabled={disabled}
          className="shrink-0 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
        >
          发送
        </button>
      )}
    </div>
  );
}
