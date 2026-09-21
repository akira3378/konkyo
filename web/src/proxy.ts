import createMiddleware from "next-intl/middleware";
import { routing } from "./i18n/routing";

// 访问 "/" 时决定跳到哪个 locale：先看 URL 有没有带（没有）、
// 再看浏览器 Accept-Language、都没有就用 routing.defaultLocale（zh）。
// "暂时默认中文"这句话，落地就是 routing.ts 里的 defaultLocale 一个字段，
// 不是散落在各个组件里的默认值。
export default createMiddleware(routing);

export const config = {
  // 排除 Next.js 内部资源和静态文件，其余路径都过一遍 locale 中间件。
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};
