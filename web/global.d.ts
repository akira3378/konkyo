import type { routing } from "@/i18n/routing";
import type messages from "./messages/zh.json";

// 让 useTranslations("chat") 之类的调用在编译期就能检查 key 存不存在、
// 参数对不对——翻译文件里删一个 key、某处用到它的地方会直接报类型错误，
// 不用等运行时才发现漏翻了。三份 messages/*.json 结构必须一致，这里只
// 拿 zh.json 当类型的"模板"。
declare module "next-intl" {
  interface AppConfig {
    Locale: (typeof routing.locales)[number];
    Messages: typeof messages;
  }
}
