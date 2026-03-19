"use client";

import { useI18n, Locale } from "../lib/i18n";

export default function LanguageSwitcher() {
  const { locale, setLocale } = useI18n();

  return (
    <button
      onClick={() => setLocale(locale === "de" ? "en" : "de")}
      className="font-mono text-xs text-text-tertiary hover:text-text-primary transition-colors uppercase tracking-wide"
      aria-label="Switch language"
      title={locale === "de" ? "Switch to English" : "Auf Deutsch wechseln"}
    >
      {locale === "de" ? "EN" : "DE"}
    </button>
  );
}
