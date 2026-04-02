"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useI18n } from "../lib/i18n";
import { useAuth } from "../lib/auth";
import ThemeToggle from "./ThemeToggle";
import LanguageSwitcher from "./LanguageSwitcher";

const TIER_COLORS: Record<string, string> = {
  lite: "bg-text-tertiary/15 text-text-tertiary",
  pro: "bg-accent/12 text-accent",
  max: "bg-success/12 text-success",
};

const TIER_DOTS: Record<string, string> = {
  lite: "bg-text-tertiary",
  pro: "bg-accent",
  max: "bg-success",
};

function UserInitials({ name }: { name: string }) {
  const initials = name
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
  return (
    <div className="w-7 h-7 rounded-full bg-accent/12 text-accent flex items-center justify-center text-[10px] font-semibold tracking-wide">
      {initials || "?"}
    </div>
  );
}

export default function Header() {
  const { t } = useI18n();
  const { user, logout, loading } = useAuth();
  const pathname = usePathname();

  const isActive = (path: string) => pathname === path;

  return (
    <header className="fixed top-3 left-4 right-4 z-50 glass-bar rounded-2xl">
      <div className="flex items-center justify-between px-4 py-2">
        {/* Left: Logo + Nav */}
        <div className="flex items-center gap-1">
          <Link href="/" className="flex items-center hover:opacity-80 transition-opacity mr-3">
            <img
              src="/header-logo.svg"
              alt="FakeNewsGuard"
              className="h-7 block dark:hidden"
            />
            <img
              src="/header-logo-dark.svg"
              alt="FakeNewsGuard"
              className="h-7 hidden dark:block"
            />
          </Link>

          {user && (
            <>
              {/* Nav separator */}
              <div className="w-px h-4 bg-border/60 mr-2 hidden sm:block" />

              <nav className="hidden sm:flex items-center gap-0.5">
                <Link
                  href="/"
                  className={`px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    isActive("/")
                      ? "bg-surface-hover text-text-primary"
                      : "text-text-tertiary hover:text-text-secondary hover:bg-surface-hover/50"
                  }`}
                >
                  {t("nav.newAnalysis")}
                </Link>
                <Link
                  href="/archiv"
                  className={`px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    isActive("/archiv")
                      ? "bg-surface-hover text-text-primary"
                      : "text-text-tertiary hover:text-text-secondary hover:bg-surface-hover/50"
                  }`}
                >
                  {t("nav.archive")}
                </Link>
                <Link
                  href="/netzwerk"
                  className={`px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    isActive("/netzwerk")
                      ? "bg-surface-hover text-text-primary"
                      : "text-text-tertiary hover:text-text-secondary hover:bg-surface-hover/50"
                  }`}
                >
                  {t("graph.title")}
                </Link>
                <Link
                  href="/analytics"
                  className={`px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    isActive("/analytics")
                      ? "bg-surface-hover text-text-primary"
                      : "text-text-tertiary hover:text-text-secondary hover:bg-surface-hover/50"
                  }`}
                >
                  {t("nav.analytics")}
                </Link>
                {user?.admin && (
                  <Link
                    href="/admin"
                    className={`px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
                      isActive("/admin")
                        ? "bg-warning/12 text-warning"
                        : "text-warning/70 hover:text-warning hover:bg-warning/8"
                    }`}
                  >
                    {t("nav.admin")}
                  </Link>
                )}
              </nav>
            </>
          )}
        </div>

        {/* Right: Controls + Auth */}
        <div className="flex items-center gap-1.5">
          <LanguageSwitcher />
          <ThemeToggle />

          {/* Separator before user area */}
          {!loading && (
            <div className="w-px h-4 bg-border/60 mx-1 hidden sm:block" />
          )}

          {!loading && (
            user ? (
              <div className="flex items-center gap-2">
                {/* Tier badge */}
                <span
                  className={`text-[10px] font-semibold tracking-wider px-2 py-0.5 rounded-md flex items-center gap-1.5 ${
                    TIER_COLORS[user.tier] || TIER_COLORS.lite
                  }`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${TIER_DOTS[user.tier] || TIER_DOTS.lite}`} />
                  {user.tier.toUpperCase()}
                </span>

                {/* User avatar + name (links to profile) */}
                <Link href="/profile" className="hidden sm:flex items-center gap-2 hover:opacity-80 transition-opacity">
                  <UserInitials name={user.display_name || user.email || "U"} />
                  <span className="text-xs text-text-secondary font-medium max-w-[100px] truncate">
                    {user.display_name || user.email}
                  </span>
                </Link>

                <button
                  onClick={logout}
                  className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors px-1.5 py-1 rounded-md hover:bg-surface-hover/50"
                >
                  {t("auth.logout")}
                </button>
              </div>
            ) : (
              <Link
                href="/login"
                className="text-xs font-medium text-bg-primary bg-accent hover:bg-accent-hover transition-colors px-3 py-1.5 rounded-lg"
              >
                {t("auth.login")}
              </Link>
            )
          )}
        </div>
      </div>
    </header>
  );
}
