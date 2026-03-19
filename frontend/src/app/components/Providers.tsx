"use client";

import { ReactNode } from "react";
import { I18nProvider } from "../lib/i18n";

export default function Providers({ children }: { children: ReactNode }) {
  return <I18nProvider>{children}</I18nProvider>;
}
