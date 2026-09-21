"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ChangeEvent } from "react";
import { useTransition } from "react";
import { routing } from "@/i18n/routing";
import { usePathname, useRouter } from "@/i18n/navigation";

export function LocaleSwitcher() {
  const t = useTranslations("locale");
  const tApp = useTranslations("app");
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const [isPending, startTransition] = useTransition();

  function handleChange(e: ChangeEvent<HTMLSelectElement>) {
    const nextLocale = e.target.value as (typeof routing.locales)[number];
    // usePathname() 拿到的路径已经不带 locale 前缀了（next-intl 替你剥掉了），
    // 换语言只是拿同一个 pathname、换个 locale 重新 push 一次——
    // 不是跳去首页，当前在哪个"页面"（现在只有一个，但以后加了页面也一样）不变。
    startTransition(() => {
      router.replace(pathname, { locale: nextLocale });
    });
  }

  return (
    <select
      value={locale}
      onChange={handleChange}
      disabled={isPending}
      aria-label={tApp("languageLabel")}
      className="rounded-md border border-zinc-200 bg-transparent px-1.5 py-0.5 text-xs text-zinc-500 outline-none disabled:opacity-50 dark:border-zinc-700"
    >
      {routing.locales.map((l) => (
        <option key={l} value={l}>
          {t(l)}
        </option>
      ))}
    </select>
  );
}
