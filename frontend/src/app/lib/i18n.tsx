"use client";

import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from "react";
import de from "./locales/de";
import en from "./locales/en";

// ── Types ───────────────────────────────────────────────────────

export type Locale = "de" | "en";
type DeepStringify<T> = { [K in keyof T]: T[K] extends Record<string, unknown> ? DeepStringify<T[K]> : string };
type Strings = DeepStringify<typeof de>;

const LOCALES: Record<Locale, Strings> = { de, en };

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string) => string;
  /** Rating keys → localized labels (for lookups by backend value) */
  ratings: Record<string, string>;
  /** Claim rating keys → localized labels */
  claimRatings: Record<string, string>;
}

// ── Context ─────────────────────────────────────────────────────

const I18nContext = createContext<I18nContextValue | null>(null);

function resolve(obj: Record<string, unknown>, key: string): string {
  const parts = key.split(".");
  let current: unknown = obj;
  for (const part of parts) {
    if (current && typeof current === "object" && part in (current as Record<string, unknown>)) {
      current = (current as Record<string, unknown>)[part];
    } else {
      return key; // fallback: return key itself
    }
  }
  return typeof current === "string" ? current : key;
}

// ── Provider ────────────────────────────────────────────────────

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("locale") as Locale | null;
      if (saved && saved in LOCALES) return saved;
    }
    return "de";
  });

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    if (typeof window !== "undefined") {
      localStorage.setItem("locale", l);
      document.documentElement.lang = l;
    }
  }, []);

  // Set html lang on mount
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const strings = LOCALES[locale];

  const t = useCallback(
    (key: string) => resolve(strings as unknown as Record<string, unknown>, key),
    [strings],
  );

  const ratings = strings.ratings;
  const claimRatings = strings.claimRatings;

  return (
    <I18nContext.Provider value={{ locale, setLocale, t, ratings, claimRatings }}>
      {children}
    </I18nContext.Provider>
  );
}

// ── Hook ────────────────────────────────────────────────────────

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}
