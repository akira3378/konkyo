import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";
import zhMessages from "../../messages/zh.json";
import { Composer } from "./Composer";

/**
 * 中日文输入法选字确认那个 bug 的回归测试。
 *
 * jsdom 里没有真实输入法，模拟不出"用户在候选词窗口里选字"这个操作本身，
 * 只能手动派发浏览器在这个过程中真正会发的那几个事件
 * （compositionstart → keydown(isComposing:true) → compositionend），
 * 断言这几个事件按什么顺序来、Composer 该怎么反应。
 */

function renderComposer(props: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const onSend = vi.fn();
  const onStop = vi.fn();
  render(
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      <Composer disabled={false} streaming={false} onSend={onSend} onStop={onStop} {...props} />
    </NextIntlClientProvider>,
  );
  const textarea = screen.getByPlaceholderText(zhMessages.chat.placeholder);
  return { textarea, onSend, onStop };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Composer", () => {
  it("IME 组字期间按 Enter 不会触发发送", () => {
    const { textarea, onSend } = renderComposer();

    fireEvent.change(textarea, { target: { value: "こんにちは" } });
    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true });

    expect(onSend).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("こんにちは"); // 输入框内容没被清空，说明确实没当成提交处理
  });

  it("IME 组字结束后按 Enter 正常发送", () => {
    const { textarea, onSend } = renderComposer();

    fireEvent.change(textarea, { target: { value: "こんにちは" } });
    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true }); // 选字确认，忽略
    fireEvent.compositionEnd(textarea);
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: false }); // 真正要发送的那次

    expect(onSend).toHaveBeenCalledExactlyOnceWith("こんにちは");
  });

  it("普通输入（没有 IME）按 Enter 直接发送", () => {
    const { textarea, onSend } = renderComposer();

    fireEvent.change(textarea, { target: { value: "hello" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledExactlyOnceWith("hello");
  });

  it("Shift+Enter 换行，不发送", () => {
    const { textarea, onSend } = renderComposer();

    fireEvent.change(textarea, { target: { value: "hello" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("空白内容不发送", () => {
    const { textarea, onSend } = renderComposer();

    fireEvent.change(textarea, { target: { value: "   " } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("streaming 时显示停止按钮，点击触发 onStop", () => {
    const { onStop } = renderComposer({ streaming: true });

    fireEvent.click(screen.getByRole("button"));

    expect(onStop).toHaveBeenCalledOnce();
  });
});
