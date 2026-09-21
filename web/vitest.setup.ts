import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// React Testing Library 的自动清理靠检测全局 afterEach，但这个项目
// 故意不开 vitest 的 `test.globals`（每个测试文件显式 import，
// 少一层"这个变量哪来的"）——不开全局，RTL 就检测不到，得自己接一下，
// 不然每个测试渲染完的 DOM 会堆在上一个测试后面，下一个测试里
// getByPlaceholderText 之类的查询会因为"匹配到不止一个"而报错。
afterEach(() => {
  cleanup();
});
