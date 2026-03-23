"use client";

import { ReactNode } from "react";
import { I18nProvider } from "../lib/i18n";
import { AuthProvider } from "../lib/auth";

export default function Providers({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <I18nProvider>{children}</I18nProvider>
    </AuthProvider>
  );
}
