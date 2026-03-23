"use client";

import { useState, useEffect, useCallback, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";

// ── Constants ──────────────────────────────────────────────────────

const TIER_COLORS: Record<string, string> = {
  lite: "bg-text-tertiary/12 text-text-tertiary",
  pro: "bg-accent/12 text-accent",
  max: "bg-success/12 text-success",
};

const TIER_DOTS: Record<string, string> = {
  lite: "bg-text-tertiary",
  pro: "bg-accent",
  max: "bg-success",
};

// ── Main Page ──────────────────────────────────────────────────────

export default function ProfilePage() {
  const { user, token, loading: authLoading, updateProfile, refreshUser } = useAuth();
  const { t } = useI18n();
  const router = useRouter();

  // Display name editing
  const [displayName, setDisplayName] = useState("");
  const [nameSaving, setNameSaving] = useState(false);
  const [nameSuccess, setNameSuccess] = useState(false);
  const [nameError, setNameError] = useState("");

  // Telegram linking
  const [linkCode, setLinkCode] = useState<string | null>(null);
  const [linkLoading, setLinkLoading] = useState(false);
  const [linkError, setLinkError] = useState("");
  const [unlinkLoading, setUnlinkLoading] = useState(false);
  const [codeExpiresAt, setCodeExpiresAt] = useState<number>(0);
  const [countdown, setCountdown] = useState(0);

  // Init display name from user
  useEffect(() => {
    if (user) {
      setDisplayName(user.display_name || "");
    }
  }, [user]);

  // Redirect if not logged in
  useEffect(() => {
    if (!authLoading && !user) {
      router.push("/login");
    }
  }, [authLoading, user, router]);

  // Countdown timer for link code
  useEffect(() => {
    if (codeExpiresAt <= 0) return;
    const interval = setInterval(() => {
      const remaining = Math.max(0, Math.floor((codeExpiresAt - Date.now()) / 1000));
      setCountdown(remaining);
      if (remaining <= 0) {
        setLinkCode(null);
        setCodeExpiresAt(0);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [codeExpiresAt]);

  // ── Handlers ─────────────────────────────────────────────────────

  const handleNameSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = displayName.trim();
    if (!trimmed || trimmed === user?.display_name) return;

    setNameSaving(true);
    setNameError("");
    setNameSuccess(false);
    try {
      await updateProfile(trimmed);
      setNameSuccess(true);
      setTimeout(() => setNameSuccess(false), 3000);
    } catch (err) {
      setNameError(err instanceof Error ? err.message : t("profile.error"));
    } finally {
      setNameSaving(false);
    }
  };

  const handleRequestLinkCode = useCallback(async () => {
    if (!token) return;
    setLinkLoading(true);
    setLinkError("");
    try {
      const res = await fetch("/api/auth/telegram/request-link", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || t("profile.error"));
      }
      const data = await res.json();
      setLinkCode(data.code);
      setCodeExpiresAt(Date.now() + (data.expires_in ?? 600) * 1000);
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : t("profile.error"));
    } finally {
      setLinkLoading(false);
    }
  }, [token, t]);

  const handleUnlink = useCallback(async () => {
    if (!token) return;
    if (!confirm(t("profile.unlinkConfirm"))) return;

    setUnlinkLoading(true);
    try {
      const res = await fetch("/api/auth/telegram/unlink", {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || t("profile.error"));
      }
      await refreshUser();
    } catch (err) {
      alert(err instanceof Error ? err.message : t("profile.error"));
    } finally {
      setUnlinkLoading(false);
    }
  }, [token, refreshUser, t]);

  // ── Guard ────────────────────────────────────────────────────────

  if (authLoading || !user) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)]">
        <div className="w-8 h-8 rounded-full border-2 border-accent/30 border-t-accent animate-spin" />
      </div>
    );
  }

  const isNameChanged = displayName.trim() !== (user.display_name || "");
  const telegramLinked = !!user.telegram_id;

  return (
    <div className="max-w-lg mx-auto px-4 py-8">
      <h1 className="text-xl font-semibold text-text-primary tracking-tight mb-6">
        {t("profile.title")}
      </h1>

      <div className="space-y-5">
        {/* ── Account info ──────────────────────────────────────── */}
        <div className="glass-card rounded-2xl p-5">
          <h2 className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-4">
            {t("profile.account")}
          </h2>

          <div className="space-y-3">
            {/* Email (read-only) */}
            <div>
              <label className="block text-xs text-text-tertiary mb-1">
                {t("auth.email")}
              </label>
              <div className="px-3 py-2 text-sm bg-bg-tertiary/30 border border-border/50 rounded-lg text-text-secondary">
                {user.email || "—"}
              </div>
            </div>

            {/* Tier (read-only) */}
            <div>
              <label className="block text-xs text-text-tertiary mb-1">
                {t("profile.plan")}
              </label>
              <div className="flex items-center gap-2">
                <span className={`text-xs font-semibold tracking-wider px-2.5 py-1 rounded-lg flex items-center gap-1.5 ${TIER_COLORS[user.tier] || TIER_COLORS.lite}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${TIER_DOTS[user.tier] || TIER_DOTS.lite}`} />
                  {user.tier.toUpperCase()}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* ── Display name ──────────────────────────────────────── */}
        <div className="glass-card rounded-2xl p-5">
          <h2 className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-4">
            {t("profile.displayNameSection")}
          </h2>

          <form onSubmit={handleNameSubmit} className="space-y-3">
            <div>
              <label className="block text-xs text-text-tertiary mb-1">
                {t("auth.displayName")}
              </label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                maxLength={50}
                className="w-full px-3 py-2 text-sm bg-bg-primary border border-border rounded-lg text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-accent transition-colors"
                placeholder={t("profile.displayNamePlaceholder")}
              />
            </div>

            {nameError && (
              <p className="text-xs text-error">{nameError}</p>
            )}

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={nameSaving || !isNameChanged || !displayName.trim()}
                className="px-4 py-1.5 text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-40 transition-all"
              >
                {nameSaving ? t("profile.saving") : t("profile.save")}
              </button>
              {nameSuccess && (
                <span className="text-xs text-success font-medium animate-fade-in">
                  {t("profile.saved")}
                </span>
              )}
            </div>
          </form>
        </div>

        {/* ── Telegram linking ──────────────────────────────────── */}
        <div className="glass-card rounded-2xl p-5">
          <h2 className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-4">
            {t("profile.telegram")}
          </h2>

          {telegramLinked ? (
            /* Already linked */
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-success" />
                <span className="text-sm text-text-primary font-medium">
                  {t("profile.telegramLinked")}
                </span>
              </div>
              <div className="px-3 py-2 text-sm bg-bg-tertiary/30 border border-border/50 rounded-lg text-text-secondary font-mono">
                ID: {user.telegram_id}
              </div>
              <button
                onClick={handleUnlink}
                disabled={unlinkLoading}
                className="text-xs text-error hover:text-error/80 transition-colors disabled:opacity-50"
              >
                {unlinkLoading ? "…" : t("profile.unlinkTelegram")}
              </button>
            </div>
          ) : (
            /* Not linked */
            <div className="space-y-4">
              <p className="text-xs text-text-secondary leading-relaxed">
                {t("profile.telegramDescription")}
              </p>

              {linkCode ? (
                /* Show the code */
                <div className="space-y-3 animate-fade-in">
                  <div className="text-center">
                    <div className="text-xs text-text-tertiary mb-2">{t("profile.yourCode")}</div>
                    <div className="inline-block px-6 py-3 rounded-xl bg-accent/8 border-2 border-accent/20">
                      <span className="text-2xl font-mono font-bold text-accent tracking-[0.3em]">
                        {linkCode}
                      </span>
                    </div>
                    {countdown > 0 && (
                      <div className="text-[10px] text-text-tertiary mt-2">
                        {t("profile.codeExpires").replace("{seconds}", String(countdown))}
                      </div>
                    )}
                  </div>

                  <div className="glass-inner rounded-xl p-3">
                    <ol className="text-xs text-text-secondary space-y-1.5 list-decimal list-inside">
                      <li>{t("profile.step1")}</li>
                      <li>
                        {t("profile.step2")}{" "}
                        <code className="px-1.5 py-0.5 rounded bg-bg-tertiary text-text-primary text-[11px]">
                          /link {linkCode}
                        </code>
                      </li>
                      <li>{t("profile.step3")}</li>
                    </ol>
                  </div>

                  <button
                    onClick={handleRequestLinkCode}
                    disabled={linkLoading}
                    className="text-xs text-text-tertiary hover:text-text-primary transition-colors"
                  >
                    {t("profile.newCode")}
                  </button>
                </div>
              ) : (
                /* Button to generate code */
                <div>
                  <button
                    onClick={handleRequestLinkCode}
                    disabled={linkLoading}
                    className="flex items-center gap-2 px-4 py-2 text-xs font-medium glass-inner rounded-xl hover:bg-surface-hover/50 text-text-primary transition-all disabled:opacity-50"
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21.198 2.433a2.242 2.242 0 0 0-1.022.215l-8.609 3.33c-2.068.8-4.133 1.598-5.724 2.21a405 405 0 0 0-2.849 1.106c-.189.076-.512.2-.768.395a1.26 1.26 0 0 0-.478.76c-.025.233.077.476.157.598a1.2 1.2 0 0 0 .392.347c.293.17.644.265.784.305l4.348 1.244c.175.48 1.207 3.3 1.435 3.93.147.405.29.667.468.873a1.3 1.3 0 0 0 .553.39c.2.078.388.088.488.088h.001a1.07 1.07 0 0 0 .645-.245l2.064-1.68 4.08 3.008c.217.18.52.334.907.334.24 0 .5-.065.73-.186a1.55 1.55 0 0 0 .676-.678c.13-.247.19-.505.22-.688.027-.18.04-.358.053-.54l1.905-16.032a2.2 2.2 0 0 0-.088-.783 1.24 1.24 0 0 0-.488-.65 1.19 1.19 0 0 0-.764-.192" />
                    </svg>
                    {linkLoading ? "…" : t("profile.connectTelegram")}
                  </button>
                  {linkError && (
                    <p className="text-xs text-error mt-2">{linkError}</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
