"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";

export default function LoginPage() {
  const { login, register, user } = useAuth();
  const { t } = useI18n();
  const router = useRouter();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Redirect if already logged in
  if (user) {
    router.push("/");
    return null;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      if (mode === "login") {
        await login(email, password, rememberMe);
      } else {
        await register(email, password, displayName);
      }
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.genericError"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] px-4">
      <div className="w-full max-w-sm">
        <div className="glass-card rounded-2xl p-6">
          {/* Tab switcher */}
          <div className="flex gap-1 mb-6 p-1 rounded-lg bg-bg-tertiary/50">
            <button
              type="button"
              className={`flex-1 py-1.5 text-xs font-mono rounded-md transition-all ${
                mode === "login"
                  ? "bg-surface text-text-primary shadow-sm"
                  : "text-text-tertiary hover:text-text-secondary"
              }`}
              onClick={() => { setMode("login"); setError(""); }}
            >
              {t("auth.login")}
            </button>
            <button
              type="button"
              className={`flex-1 py-1.5 text-xs font-mono rounded-md transition-all ${
                mode === "register"
                  ? "bg-surface text-text-primary shadow-sm"
                  : "text-text-tertiary hover:text-text-secondary"
              }`}
              onClick={() => { setMode("register"); setError(""); }}
            >
              {t("auth.register")}
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === "register" && (
              <div>
                <label className="block text-xs font-mono text-text-tertiary mb-1">
                  {t("auth.displayName")}
                </label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  className="w-full px-3 py-2 text-sm font-mono bg-bg-primary border border-border rounded-lg text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-accent transition-colors"
                  placeholder={t("auth.displayNamePlaceholder")}
                />
              </div>
            )}

            <div>
              <label className="block text-xs font-mono text-text-tertiary mb-1">
                {t("auth.email")}
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="w-full px-3 py-2 text-sm font-mono bg-bg-primary border border-border rounded-lg text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-accent transition-colors"
                placeholder="name@example.com"
              />
            </div>

            <div>
              <label className="block text-xs font-mono text-text-tertiary mb-1">
                {t("auth.password")}
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                className="w-full px-3 py-2 text-sm font-mono bg-bg-primary border border-border rounded-lg text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-accent transition-colors"
                placeholder={t("auth.passwordPlaceholder")}
              />
            </div>

            {mode === "login" && (
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="w-3.5 h-3.5 rounded border border-border bg-bg-primary accent-accent cursor-pointer"
                />
                <span className="text-xs font-mono text-text-secondary">
                  {t("auth.rememberMe")}
                </span>
              </label>
            )}

            {error && (
              <p className="text-xs font-mono text-error">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2 text-sm font-mono bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors"
            >
              {loading
                ? t("auth.loading")
                : mode === "login"
                  ? t("auth.loginButton")
                  : t("auth.registerButton")
              }
            </button>
          </form>
        </div>

        <p className="text-center text-xs font-mono text-text-tertiary mt-4">
          {t("auth.tierNote")}
        </p>
      </div>
    </div>
  );
}
