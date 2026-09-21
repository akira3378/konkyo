import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Next.js 文档推荐的 Vitest 配置用的是 vite-tsconfig-paths 插件，
    // 但那个插件最新版自己在运行时提示"这个功能 Vite 已经原生支持了"——
    // 直接用原生选项，少一个依赖。
    tsconfigPaths: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
