import { hasLocale } from "next-intl";
import { getRequestConfig } from "next-intl/server";
import { routing } from "./routing";

// 每个请求进来，next-intl 都会调这个函数一次，决定"这次用哪份翻译"。
// 只在这里做一件事：按 URL 里的 locale 段，加载对应的 messages/*.json。
export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
