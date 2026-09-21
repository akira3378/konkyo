import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import i18next from "eslint-plugin-i18next";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // check-i18n-parity.mjs 管得到"三份翻译文件的 key 是否对齐"，
    // 管不到"页面上这段文字有没有被换成 t(...)"——那是写代码那一刻就该
    // 拦下来的事，不是等三份文件都写完才比对。这条规则扫 JSX 里的文本节点
    // 和 placeholder/alt/aria-label/title/value 这几个常见的人话属性，
    // 只要是没走 t(...) 的字面量文本就报错。
    files: ["src/**/*.{jsx,tsx}"],
    ...i18next.configs["flat/recommended"],
    rules: {
      // 默认 mode 是 "jsx-text-only"，只查 JSX 文本节点，不查
      // placeholder/alt/aria-label/title 这几个属性里的字面量——
      // 换成 "jsx-only" 把这几个也纳入检查范围（实测确认过，默认 mode
      // 下这个规则完全不会报 placeholder 里的硬编码文字）。
      "i18next/no-literal-string": [
        "error",
        {
          mode: "jsx-only",
          callees: {
            exclude: [
              "i18n(ext)?",
              "t",
              // 项目里 useTranslations() 的返回值除了裸 t，还会按命名空间
              // 叫 tApp/tErrors 这类名字（见 page.tsx、LocaleSwitcher.tsx）。
              // 不加这条，jsx-only 模式下这些调用的字符串参数会被误报成
              // "没翻译的硬编码文字"——它们其实就是翻译调用本身。
              "t[A-Z][A-Za-z]*",
            ],
          },
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
