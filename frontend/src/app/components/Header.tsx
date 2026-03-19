"use client";

import Link from "next/link";
import { useI18n } from "../lib/i18n";
import ThemeToggle from "./ThemeToggle";
import LanguageSwitcher from "./LanguageSwitcher";

export default function Header() {
  const { t } = useI18n();

  return (
    <header className="fixed top-3 left-4 right-4 z-50 glass-bar rounded-2xl">
      <div className="flex items-center justify-between px-5 py-2.5">
        <Link href="/" className="flex items-center hover:opacity-80 transition-opacity">
          <img
            src="/header-logo.svg"
            alt="FakeNewsGuard"
            className="h-8 block dark:hidden"
          />
          <img
            src="/header-logo-dark.svg"
            alt="FakeNewsGuard"
            className="h-8 hidden dark:block"
          />
        </Link>
        <div className="flex items-center gap-4">
          <Link
            href="/archiv"
            className="font-mono text-xs text-text-tertiary hover:text-text-primary transition-colors"
          >
            {t("nav.archive")}
          </Link>
          <LanguageSwitcher />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
