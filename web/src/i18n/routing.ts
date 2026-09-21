import { defineRouting } from "next-intl/routing";

/**
 * 所有 locale 相关的配置只在这一个文件里改。
 *
 * 加一种语言只需要：① 这里的 locales 数组加一项 ② messages/ 加一个对应
 * json ③ LocaleSwitcher 里加一条选项——不用碰路由、中间件、任何页面组件。
 * 这是"可扩展"具体落到代码上的样子：新增语言是加配置，不是改逻辑。
 */
export const routing = defineRouting({
  locales: ["zh", "ja", "en"],
  defaultLocale: "zh",
});

export type Locale = (typeof routing.locales)[number];
