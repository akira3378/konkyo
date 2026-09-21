import { createNavigation } from "next-intl/navigation";
import { routing } from "./routing";

// 包了一层 next-intl 版的 Link/useRouter/usePathname/redirect：
// 行为和 next/navigation 原生的一样，但会自动在 URL 里带上当前 locale
// 前缀。语言切换组件（LocaleSwitcher）靠这个知道"现在在哪个 locale、
// 切换后应该跳到哪个 URL"，不用自己拼路径。
export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation(routing);
