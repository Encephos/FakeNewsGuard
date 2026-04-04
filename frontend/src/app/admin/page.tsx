"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import {
  PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  AreaChart, Area,
} from "recharts";

// ── Types ──────────────────────────────────────────────────────────

interface UserRow {
  id: string;
  email: string | null;
  display_name: string;
  tier: string;
  admin: number;
  telegram_id: string | null;
  created_at: number;
  last_login: number | null;
  analyses_total: number;
  analyses_month: number;
  last_analysis: number | null;
}

interface UsageEntry {
  tier_used: string;
  created_at: number;
  claims: number;
  rating: string | null;
  source: string;
}

interface Stats {
  total_users: number;
  total_analyses: number;
  month_analyses: number;
  tier_distribution: Record<string, number>;
}

interface EndpointStat {
  count: number;
  errors: number;
  avg_ms: number;
}

interface Metrics {
  requests_total: number;
  requests_errors: number;
  requests_4xx: number;
  requests_5xx: number;
  auth_attempts: number;
  auth_failures: number;
  avg_latency_ms: number;
  p95_latency_ms: number | null;
  uptime_seconds: number;
  active_jobs: number;
  by_endpoint: Record<string, EndpointStat>;
}

interface LogEntry {
  timestamp: number;
  level: string;
  logger: string;
  message: string;
  exc?: string;
}

interface AnalyticsData {
  verdict_distribution: Record<string, number>;
  confidence_histogram: Array<{ bucket: string; count: number }>;
  top_domains: Array<{ domain: string; count: number; avg_tier: number }>;
  analyses_per_day: Array<{ date: string; count: number }>;
  period_days: number;
}

// ── Constants ──────────────────────────────────────────────────────

const TIER_OPTIONS = ["lite", "pro", "max"] as const;

const TIER_STYLES: Record<string, string> = {
  lite: "bg-bg-tertiary/60 text-text-secondary",
  pro: "bg-accent/15 text-accent",
  max: "bg-success/15 text-success",
};

const TIER_BAR_COLORS: Record<string, string> = {
  lite: "bg-text-tertiary/40",
  pro: "bg-accent",
  max: "bg-success",
};

const LOG_LEVEL_STYLES: Record<string, string> = {
  DEBUG: "text-text-tertiary",
  INFO: "text-text-secondary",
  WARNING: "text-warning",
  ERROR: "text-error",
  CRITICAL: "text-error font-bold",
};

const LOG_LEVEL_DOT: Record<string, string> = {
  DEBUG: "bg-text-tertiary/40",
  INFO: "bg-text-secondary",
  WARNING: "bg-warning",
  ERROR: "bg-error",
  CRITICAL: "bg-error",
};

const VERDICT_COLORS: Record<string, string> = {
  TRUE: "#22c55e",
  MOSTLY_TRUE: "#86efac",
  MISLEADING: "#eab308",
  MOSTLY_FALSE: "#f97316",
  FALSE: "#ef4444",
  UNVERIFIABLE: "#6b7280",
};

const HISTOGRAM_COLORS = [
  "#ef4444", "#f97316", "#fb923c", "#fbbf24", "#facc15",
  "#a3e635", "#4ade80", "#34d399", "#10b981", "#22c55e",
];

function domainTierColor(tier: number): string {
  if (tier <= 2) return "#22c55e";
  if (tier === 3) return "#eab308";
  return "#f97316";
}

// ── Helpers ────────────────────────────────────────────────────────

function formatDate(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatLogTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

// ── Reusable components ────────────────────────────────────────────

function MetricCard({
  label,
  value,
  sub,
  accent,
  icon,
}: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: "default" | "success" | "warning" | "error";
  icon?: React.ReactNode;
}) {
  const accentColors = {
    default: "border-l-text-tertiary/30",
    success: "border-l-success",
    warning: "border-l-warning",
    error: "border-l-error",
  };
  return (
    <div
      className={`glass-inner rounded-xl px-4 py-3.5 border-l-[3px] ${
        accentColors[accent ?? "default"]
      } admin-card-enter`}
    >
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <div className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-1.5">
            {label}
          </div>
          <div className="text-2xl font-semibold text-text-primary tracking-tight leading-none">
            {value}
          </div>
          {sub && (
            <div className="text-[10px] text-text-tertiary mt-1.5 font-medium">{sub}</div>
          )}
        </div>
        {icon && (
          <div className="text-text-tertiary/40 flex-shrink-0 ml-2">{icon}</div>
        )}
      </div>
    </div>
  );
}

function TierDistributionBar({ distribution, total }: { distribution: Record<string, number>; total: number }) {
  if (total === 0) return null;
  return (
    <div className="glass-inner rounded-xl px-4 py-3.5 border-l-[3px] border-l-text-tertiary/30 admin-card-enter">
      <div className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-3">
        Tier Distribution
      </div>
      {/* Stacked bar */}
      <div className="flex h-4 rounded-full overflow-hidden bg-bg-tertiary/40 mb-3">
        {TIER_OPTIONS.map((tier) => {
          const count = distribution[tier] ?? 0;
          const pct = (count / total) * 100;
          if (pct === 0) return null;
          return (
            <div
              key={tier}
              className={`${TIER_BAR_COLORS[tier]} transition-all duration-700 ease-out`}
              style={{ width: `${pct}%` }}
              title={`${tier.toUpperCase()}: ${count}`}
            />
          );
        })}
      </div>
      {/* Legend */}
      <div className="flex items-center gap-3 flex-wrap">
        {TIER_OPTIONS.map((tier) => {
          const count = distribution[tier] ?? 0;
          return (
            <div key={tier} className="flex items-center gap-1">
              <div className={`w-2.5 h-2.5 rounded-full ${TIER_BAR_COLORS[tier]}`} />
              <span className="text-[11px] font-semibold text-text-secondary">
                {tier.toUpperCase()}
              </span>
              <span className="text-[10px] text-text-tertiary tabular-nums">
                {count}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EndpointBar({
  path,
  count,
  errors,
  avgMs,
  maxCount,
}: {
  path: string;
  count: number;
  errors: number;
  avgMs: number;
  maxCount: number;
}) {
  const barWidth = maxCount > 0 ? (count / maxCount) * 100 : 0;
  const errorPct = count > 0 ? Math.round((errors / count) * 100) : 0;
  const errorBarWidth = count > 0 ? (errors / count) * 100 : 0;

  return (
    <div className="group px-4 py-2.5 hover:bg-surface-hover/30 transition-colors">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-mono text-text-primary truncate max-w-[280px]">
          {path}
        </span>
        <div className="flex items-center gap-4 text-[10px] font-mono text-text-tertiary flex-shrink-0 ml-4">
          <span>{count} req</span>
          <span>{avgMs}ms</span>
          <span className={errorPct > 0 ? "text-error font-medium" : "text-success"}>
            {errorPct}% err
          </span>
        </div>
      </div>
      {/* Bar visualization */}
      <div className="flex h-1.5 rounded-full overflow-hidden bg-bg-tertiary/30">
        <div
          className="bg-accent/60 rounded-full transition-all duration-500 ease-out relative"
          style={{ width: `${barWidth}%` }}
        >
          {/* Error portion within the bar */}
          {errorBarWidth > 0 && (
            <div
              className="absolute right-0 top-0 h-full bg-error rounded-r-full"
              style={{ width: `${errorBarWidth}%` }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function ErrorRateGauge({ errors, total }: { errors: number; total: number }) {
  const pct = total > 0 ? (errors / total) * 100 : 0;
  const color = pct > 10 ? "text-error" : pct > 5 ? "text-warning" : "text-success";
  const barColor = pct > 10 ? "bg-error" : pct > 5 ? "bg-warning" : "bg-success";
  return (
    <div className="glass-inner rounded-xl px-4 py-3.5 admin-card-enter">
      <div className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-1.5">
        Error Rate
      </div>
      <div className={`text-2xl font-semibold tracking-tight leading-none ${color}`}>
        {pct.toFixed(1)}%
      </div>
      <div className="mt-2.5 h-1.5 rounded-full bg-bg-tertiary/40 overflow-hidden">
        <div
          className={`h-full rounded-full ${barColor} transition-all duration-700 ease-out`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <div className="text-[10px] text-text-tertiary mt-1.5 font-medium">
        {errors} / {total}
      </div>
    </div>
  );
}

function LatencyIndicator({ avg, p95 }: { avg: number; p95: number | null }) {
  const avgColor = avg > 1000 ? "text-error" : avg > 500 ? "text-warning" : "text-success";
  return (
    <div className="glass-inner rounded-xl px-4 py-3.5 admin-card-enter">
      <div className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-1.5">
        Latency
      </div>
      <div className={`text-2xl font-semibold tracking-tight leading-none ${avgColor}`}>
        {avg}<span className="text-sm font-normal text-text-tertiary ml-0.5">ms</span>
      </div>
      {p95 !== null && (
        <div className="flex items-center gap-2 mt-2">
          <div className="flex-1 h-1 rounded-full bg-bg-tertiary/40 overflow-hidden">
            <div
              className="h-full rounded-full bg-warning/60 transition-all duration-700"
              style={{ width: `${Math.min((avg / p95) * 100, 100)}%` }}
            />
          </div>
          <span className="text-[10px] text-text-tertiary font-medium flex-shrink-0">
            P95: {p95}ms
          </span>
        </div>
      )}
    </div>
  );
}

// ── SVG Icons (inline, lightweight) ──────────────────────────────

function IconUsers() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function IconChart() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </svg>
  );
}

function IconClock() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

function IconActivity() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  );
}

function IconShield() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function IconRefresh({ spinning }: { spinning?: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={spinning ? "animate-spin" : ""}
    >
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

// ── Tab icons ────────────────────────────────────────────────────

function TabIconUsers() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
    </svg>
  );
}

function TabIconSystem() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
      <line x1="8" y1="21" x2="16" y2="21" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  );
}

function TabIconInvites() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="5" width="20" height="14" rx="2" />
      <path d="M2 10h20" />
    </svg>
  );
}

function TabIconAnalytics() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 3v18h18" />
      <path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3" />
    </svg>
  );
}

// ── Main Page ──────────────────────────────────────────────────────

export default function AdminPage() {
  const { user, token, loading: authLoading } = useAuth();
  const { t } = useI18n();
  const router = useRouter();

  type Tab = "users" | "invites" | "system" | "analytics";
  const [activeTab, setActiveTab] = useState<Tab>("users");

  // Users tab state
  const [users, setUsers] = useState<UserRow[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [changingTier, setChangingTier] = useState<string | null>(null);
  const [userSearch, setUserSearch] = useState("");
  const [userSourceFilter, setUserSourceFilter] = useState<"all" | "telegram" | "web">("all");

  // Usage Modal state
  const [selectedUsageUser, setSelectedUsageUser] = useState<UserRow | null>(null);
  const [usageData, setUsageData] = useState<UsageEntry[] | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);

  // System tab state
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logLevel, setLogLevel] = useState<string>("");
  const [systemLoading, setSystemLoading] = useState(false);

  // Invites tab state
  interface InviteCode {
    id: string;
    code: string;
    created_by: string;
    label: string;
    max_uses: number;
    used_count: number;
    is_active: number;
    created_at: number;
    expires_at: number | null;
  }
  const [inviteCodes, setInviteCodes] = useState<InviteCode[]>([]);
  const [invitesLoading, setInvitesLoading] = useState(false);
  const [newCodeLabel, setNewCodeLabel] = useState("");
  const [newCodeMaxUses, setNewCodeMaxUses] = useState(1);
  const [newCodeExpiresDays, setNewCodeExpiresDays] = useState<number | null>(null);
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const [creatingCode, setCreatingCode] = useState(false);

  // Analytics tab state
  const [analyticsData, setAnalyticsData] = useState<AnalyticsData | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsDays, setAnalyticsDays] = useState(30);

  const headers = useCallback(
    () => ({
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }),
    [token],
  );

  const openUsageModal = useCallback(async (u: UserRow) => {
    setSelectedUsageUser(u);
    setUsageData(null);
    setUsageLoading(true);
    try {
      const res = await fetch(`/api/v1/admin/users/${u.id}/usage`, { headers: headers() });
      if (res.ok) {
        const data = await res.json();
        setUsageData(data.usage || []);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setUsageLoading(false);
    }
  }, [headers]);

  // ── Fetch users + stats ──────────────────────────────────────────
  const fetchUsersData = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      const [usersRes, statsRes] = await Promise.all([
        fetch("/api/v1/admin/users", { headers: headers() }),
        fetch("/api/v1/admin/stats", { headers: headers() }),
      ]);
      if (usersRes.status === 403 || statsRes.status === 403) {
        setError(t("admin.noAccess"));
        return;
      }
      if (!usersRes.ok || !statsRes.ok) {
        setError(t("admin.loadError"));
        return;
      }
      const usersData = await usersRes.json();
      const statsData = await statsRes.json();
      setUsers(usersData.users ?? []);
      setStats(statsData);
    } catch {
      setError(t("admin.loadError"));
    } finally {
      setLoading(false);
    }
  }, [token, headers, t]);

  // ── Fetch system metrics + logs ──────────────────────────────────
  const fetchSystemData = useCallback(async () => {
    if (!token) return;
    setSystemLoading(true);
    try {
      const levelParam = logLevel ? `&level=${logLevel}` : "";
      const [metricsRes, logsRes] = await Promise.all([
        fetch("/api/v1/admin/metrics", { headers: headers() }),
        fetch(`/api/v1/admin/logs?limit=150${levelParam}`, { headers: headers() }),
      ]);
      if (metricsRes.ok) setMetrics(await metricsRes.json());
      if (logsRes.ok) {
        const data = await logsRes.json();
        setLogs(data.logs ?? []);
      }
    } catch {
      // non-critical
    } finally {
      setSystemLoading(false);
    }
  }, [token, headers, logLevel]);

  // ── Fetch invite codes ────────────────────────────────────────────
  const fetchInviteCodes = useCallback(async () => {
    if (!token) return;
    setInvitesLoading(true);
    try {
      const res = await fetch("/api/v1/admin/registration-codes", { headers: headers() });
      if (res.ok) {
        const data = await res.json();
        setInviteCodes(data.codes ?? []);
      }
    } catch {
      // non-critical
    } finally {
      setInvitesLoading(false);
    }
  }, [token, headers]);

  const fetchAnalyticsData = useCallback(async () => {
    if (!token) return;
    setAnalyticsLoading(true);
    try {
      const [analyticsRes, metricsRes] = await Promise.all([
        fetch(`/api/v1/admin/analytics?days=${analyticsDays}`, { headers: headers() }),
        fetch("/api/v1/admin/metrics", { headers: headers() }),
      ]);
      if (analyticsRes.ok) setAnalyticsData(await analyticsRes.json());
      if (metricsRes.ok) setMetrics(await metricsRes.json());
    } catch {
      // non-critical
    } finally {
      setAnalyticsLoading(false);
    }
  }, [token, headers, analyticsDays]);

  const handleCreateCode = useCallback(async () => {
    if (!token) return;
    setCreatingCode(true);
    try {
      const res = await fetch("/api/v1/admin/registration-codes", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          label: newCodeLabel,
          max_uses: newCodeMaxUses,
          expires_days: newCodeExpiresDays,
        }),
      });
      if (res.ok) {
        setNewCodeLabel("");
        setNewCodeMaxUses(1);
        setNewCodeExpiresDays(null);
        fetchInviteCodes();
      }
    } finally {
      setCreatingCode(false);
    }
  }, [token, headers, newCodeLabel, newCodeMaxUses, newCodeExpiresDays, fetchInviteCodes]);

  const handleRevokeCode = useCallback(async (codeId: string) => {
    if (!token) return;
    const res = await fetch(`/api/v1/admin/registration-codes/${codeId}`, {
      method: "DELETE",
      headers: headers(),
    });
    if (res.ok) {
      setInviteCodes((prev) =>
        prev.map((c) => (c.id === codeId ? { ...c, is_active: 0 } : c)),
      );
    }
  }, [token, headers]);

  const copyCode = useCallback((code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedCode(code);
    setTimeout(() => setCopiedCode(null), 2000);
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user || !user.admin) {
      router.push("/");
      return;
    }
    fetchUsersData();
  }, [authLoading, user, router, fetchUsersData]);

  useEffect(() => {
    if (activeTab === "system" && token && user?.admin) {
      fetchSystemData();
    }
  }, [activeTab, token, user, fetchSystemData]);

  useEffect(() => {
    if (activeTab === "invites" && token && user?.admin) {
      fetchInviteCodes();
    }
  }, [activeTab, token, user, fetchInviteCodes]);

  useEffect(() => {
    if (activeTab === "analytics" && token && user?.admin) {
      fetchAnalyticsData();
    }
  }, [activeTab, token, user, fetchAnalyticsData]);

  // Auto-refresh worker queue every 10s while Analytics tab is active
  useEffect(() => {
    if (activeTab !== "analytics" || !token || !user?.admin) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch("/api/v1/admin/metrics", { headers: headers() });
        if (res.ok) setMetrics(await res.json());
      } catch { /* non-critical */ }
    }, 10000);
    return () => clearInterval(id);
  }, [activeTab, token, user, headers]);

  // ── Tier change ──────────────────────────────────────────────────
  const handleTierChange = async (userId: string, newTier: string) => {
    setChangingTier(userId);
    try {
      const res = await fetch(`/api/v1/admin/users/${userId}/tier`, {
        method: "PATCH",
        headers: headers(),
        body: JSON.stringify({ tier: newTier }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || "Fehler beim Ändern des Tiers.");
        return;
      }
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, tier: newTier } : u)),
      );
    } finally {
      setChangingTier(null);
    }
  };

  // ── Filtered users ───────────────────────────────────────────────
  const filteredUsers = useMemo(() => {
    let result = users;
    if (userSourceFilter === "telegram") result = result.filter((u) => !!u.telegram_id && !u.email);
    else if (userSourceFilter === "web") result = result.filter((u) => !!u.email);
    if (!userSearch.trim()) return result;
    const q = userSearch.toLowerCase();
    return result.filter(
      (u) =>
        (u.display_name && u.display_name.toLowerCase().includes(q)) ||
        (u.email && u.email.toLowerCase().includes(q)) ||
        (u.telegram_id && u.telegram_id.includes(q)),
    );
  }, [users, userSearch, userSourceFilter]);

  // ── Guard ────────────────────────────────────────────────────────
  if (authLoading || loading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)]">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 rounded-full border-2 border-accent/30 border-t-accent animate-spin" />
          <span className="text-xs text-text-tertiary">{t("admin.loading")}</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)]">
        <div className="glass-card rounded-2xl px-6 py-4">
          <p className="text-sm text-error">{error}</p>
        </div>
      </div>
    );
  }

  // ── Top endpoints sorted by count ────────────────────────────────
  const topEndpoints = metrics
    ? Object.entries(metrics.by_endpoint)
        .sort((a, b) => b[1].count - a[1].count)
        .slice(0, 10)
    : [];
  const maxEndpointCount = topEndpoints.length > 0 ? topEndpoints[0][1].count : 0;

  // ── Render ───────────────────────────────────────────────────────
  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
      {/* Header bar */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-semibold text-text-primary tracking-tight">
            {t("admin.title")}
          </h1>
          <p className="text-xs text-text-tertiary mt-0.5">
            {stats
              ? `${stats.total_users} ${t("admin.totalUsers").toLowerCase()} · ${stats.total_analyses} ${t("admin.totalAnalyses").toLowerCase()}`
              : ""}
          </p>
        </div>

        {/* Tab switcher */}
        <div className="flex gap-0.5 glass-inner rounded-xl p-1">
          {(["users", "invites", "system", "analytics"] as Tab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium transition-all ${
                activeTab === tab
                  ? "bg-accent text-white shadow-sm"
                  : "text-text-tertiary hover:text-text-primary hover:bg-surface-hover/50"
              }`}
            >
              {tab === "users" ? <TabIconUsers /> : tab === "invites" ? <TabIconInvites /> : tab === "system" ? <TabIconSystem /> : <TabIconAnalytics />}
              {t(`admin.tab.${tab}`)}
            </button>
          ))}
        </div>
      </div>

      {/* ── TAB: USERS ─────────────────────────────────────────────── */}
      {activeTab === "users" && (
        <div className="space-y-6 animate-fade-in">
          {/* Stats row */}
          {stats && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <MetricCard
                label={t("admin.totalUsers")}
                value={stats.total_users}
                icon={<IconUsers />}
              />
              <MetricCard
                label={t("admin.totalAnalyses")}
                value={stats.total_analyses}
                icon={<IconChart />}
                accent="success"
              />
              <MetricCard
                label={t("admin.monthAnalyses")}
                value={stats.month_analyses}
                sub={
                  stats.total_analyses > 0
                    ? `${Math.round((stats.month_analyses / stats.total_analyses) * 100)}% of total`
                    : undefined
                }
                icon={<IconActivity />}
                accent="warning"
              />
              <TierDistributionBar
                distribution={stats.tier_distribution}
                total={stats.total_users}
              />
            </div>
          )}

          {/* Source filter + Search bar */}
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex gap-1 glass-inner rounded-xl p-1 flex-shrink-0">
              {(["all", "web", "telegram"] as const).map((src) => (
                <button
                  key={src}
                  onClick={() => setUserSourceFilter(src)}
                  className={`px-2.5 py-1 rounded-lg text-[10px] font-medium transition-all ${
                    userSourceFilter === src
                      ? "bg-accent text-white"
                      : "text-text-tertiary hover:text-text-primary"
                  }`}
                >
                  {src === "all" ? "Alle" : src === "telegram" ? "Telegram" : "Web"}
                </button>
              ))}
            </div>
            <div className="relative flex-1 min-w-[180px]">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="absolute left-3.5 top-1/2 -translate-y-1/2 text-text-tertiary"
            >
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              value={userSearch}
              onChange={(e) => setUserSearch(e.target.value)}
              placeholder={t("admin.searchUsers")}
              className="w-full glass-inner rounded-xl pl-9 pr-4 py-2.5 text-xs text-text-primary placeholder:text-text-tertiary border-0 outline-none focus:ring-1 focus:ring-accent/30 transition-all"
            />
            {userSearch && (
              <span className="absolute right-3.5 top-1/2 -translate-y-1/2 text-[10px] text-text-tertiary">
                {filteredUsers.length} / {users.length}
              </span>
            )}
            </div>
          </div>

          {/* User table */}
          <div className="glass-card rounded-2xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left px-4 py-3 font-medium text-[10px] uppercase tracking-wider text-text-tertiary">
                      {t("admin.user")}
                    </th>
                    <th className="text-left px-4 py-3 font-medium text-[10px] uppercase tracking-wider text-text-tertiary">
                      {t("admin.tier")}
                    </th>
                    <th className="text-right px-4 py-3 font-medium text-[10px] uppercase tracking-wider text-text-tertiary">
                      {t("admin.analysesTotal")}
                    </th>
                    <th className="text-right px-4 py-3 font-medium text-[10px] uppercase tracking-wider text-text-tertiary">
                      {t("admin.analysesMonth")}
                    </th>
                    <th className="text-left px-4 py-3 font-medium text-[10px] uppercase tracking-wider text-text-tertiary hidden md:table-cell">
                      {t("admin.lastAnalysis")}
                    </th>
                    <th className="text-left px-4 py-3 font-medium text-[10px] uppercase tracking-wider text-text-tertiary hidden md:table-cell">
                      {t("admin.registered")}
                    </th>
                    <th className="text-right px-4 py-3 font-medium text-[10px] uppercase tracking-wider text-text-tertiary">
                      Aktionen
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filteredUsers.map((u, i) => (
                    <tr
                      key={u.id}
                      className="border-b border-border/40 hover:bg-surface-hover/40 transition-colors"
                      style={{ animationDelay: `${i * 20}ms` }}
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2.5">
                          {/* Avatar */}
                          <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-semibold flex-shrink-0 ${
                            u.admin === 1
                              ? "bg-warning/12 text-warning"
                              : "bg-accent/8 text-accent"
                          }`}>
                            {(u.display_name || u.email || "?")[0].toUpperCase()}
                          </div>
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className="text-text-primary font-medium truncate">
                                {u.display_name || u.email || "—"}
                              </span>
                              {u.admin === 1 && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-warning/12 text-warning font-semibold uppercase tracking-wider">
                                  Admin
                                </span>
                              )}
                              {!u.email && u.telegram_id && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent/10 text-accent font-semibold uppercase tracking-wider">
                                  Telegram
                                </span>
                              )}
                            </div>
                            <div className="text-[10px] text-text-tertiary truncate mt-0.5 flex items-center gap-1">
                              {u.email && <span>{u.email}</span>}
                              {u.email && u.telegram_id && <span>·</span>}
                              {u.telegram_id && (
                                <span className="inline-flex items-center gap-0.5">
                                  <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" className="text-accent flex-shrink-0">
                                    <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12L7.17 13.667l-2.96-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.978.892z"/>
                                  </svg>
                                  {u.telegram_id}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={u.tier}
                          onChange={(e) => handleTierChange(u.id, e.target.value)}
                          disabled={changingTier === u.id}
                          className={`text-[11px] font-semibold px-2.5 py-1 rounded-lg border-0 cursor-pointer transition-all ${
                            TIER_STYLES[u.tier] || TIER_STYLES.lite
                          } ${changingTier === u.id ? "opacity-50" : "hover:opacity-80"}`}
                        >
                          {TIER_OPTIONS.map((ti) => (
                            <option key={ti} value={ti}>
                              {ti.toUpperCase()}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="text-text-primary font-medium">{u.analyses_total}</span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className={`font-medium ${u.analyses_month > 0 ? "text-success" : "text-text-tertiary"}`}>
                          {u.analyses_month}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-text-tertiary hidden md:table-cell">
                        {formatDate(u.last_analysis)}
                      </td>
                      <td className="px-4 py-3 text-text-tertiary hidden md:table-cell">
                        {formatDate(u.created_at)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => openUsageModal(u)}
                          className="p-1.5 rounded-lg text-text-tertiary hover:text-accent hover:bg-accent/10 transition-colors"
                          title="Nutzungshistorie"
                        >
                          <IconActivity />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {filteredUsers.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-4 py-10 text-center text-text-tertiary text-xs">
                        {userSearch ? t("admin.noSearchResults") : t("admin.noUsers")}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Usage Modal */}
          {selectedUsageUser && (
            <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
              <div className="glass-card rounded-2xl w-full max-w-2xl max-h-[80vh] flex flex-col overflow-hidden animate-fade-in shadow-2xl">
                <div className="flex items-center justify-between px-6 py-4 border-b border-border">
                  <div>
                    <h3 className="text-sm font-semibold text-text-primary">
                      Nutzungshistorie: {selectedUsageUser.display_name || selectedUsageUser.email}
                    </h3>
                    <p className="text-[10px] text-text-tertiary mt-1">
                      Letzte 30 Tage
                    </p>
                  </div>
                  <button
                    onClick={() => setSelectedUsageUser(null)}
                    className="p-1.5 rounded-lg text-text-tertiary hover:text-text-primary hover:bg-surface-hover transition-colors"
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </div>
                
                <div className="flex-1 overflow-y-auto p-6">
                  {usageLoading ? (
                    <div className="flex justify-center py-8">
                      <IconRefresh spinning={true} />
                    </div>
                  ) : usageData && usageData.length > 0 ? (
                    <div className="divide-y divide-border/20">
                      {usageData.map((entry, idx) => (
                        <div key={idx} className="flex items-center justify-between py-3">
                          <div className="flex items-center gap-3">
                            <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase ${
                              TIER_STYLES[entry.tier_used] || TIER_STYLES.lite
                            }`}>
                              {entry.tier_used}
                            </span>
                            <div className="flex flex-col">
                              <span className="text-xs font-medium text-text-primary">
                                {formatDate(entry.created_at)}
                              </span>
                              <span className="text-[10px] text-text-tertiary">
                                {entry.source} · {entry.claims} Claims
                              </span>
                            </div>
                          </div>
                          <span className={`text-[10px] font-bold px-2 py-1 rounded-md bg-surface-hover ${
                            entry.rating === "Wahr" || entry.rating === "Größtenteils wahr" ? "text-success" :
                            entry.rating === "Falsch" || entry.rating === "Größtenteils falsch" ? "text-error" : 
                            "text-warning"
                          }`}>
                            {entry.rating || "Unbekannt"}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-8 text-xs text-text-tertiary">
                      Keine Nutzungen in den letzten 30 Tagen.
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── TAB: INVITES ───────────────────────────────────────────── */}
      {activeTab === "invites" && (
        <div className="space-y-6 animate-fade-in">
          {/* Create code form */}
          <div className="glass-card rounded-2xl p-5">
            <h3 className="text-sm font-semibold text-text-primary mb-4">{t("admin.invites.createCode")}</h3>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 items-end">
              <div>
                <label className="block text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-1.5">
                  {t("admin.invites.label")}
                </label>
                <input
                  type="text"
                  value={newCodeLabel}
                  onChange={(e) => setNewCodeLabel(e.target.value)}
                  placeholder={t("admin.invites.labelPlaceholder")}
                  className="w-full glass-inner rounded-lg px-3 py-2 text-xs text-text-primary placeholder:text-text-tertiary border-0 outline-none focus:ring-1 focus:ring-accent/30"
                />
              </div>
              <div>
                <label className="block text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-1.5">
                  {t("admin.invites.maxUses")}
                </label>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={newCodeMaxUses}
                  onChange={(e) => setNewCodeMaxUses(Math.max(1, parseInt(e.target.value) || 1))}
                  className="w-full glass-inner rounded-lg px-3 py-2 text-xs text-text-primary border-0 outline-none focus:ring-1 focus:ring-accent/30"
                />
              </div>
              <div>
                <label className="block text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-1.5">
                  {t("admin.invites.expiresDays")}
                </label>
                <select
                  value={newCodeExpiresDays ?? ""}
                  onChange={(e) => setNewCodeExpiresDays(e.target.value ? parseInt(e.target.value) : null)}
                  className="w-full glass-inner rounded-lg px-3 py-2 text-xs text-text-primary border-0 outline-none focus:ring-1 focus:ring-accent/30 bg-transparent"
                >
                  <option value="">{t("admin.invites.noExpiry")}</option>
                  <option value="1">1</option>
                  <option value="7">7</option>
                  <option value="14">14</option>
                  <option value="30">30</option>
                  <option value="90">90</option>
                </select>
              </div>
              <button
                onClick={handleCreateCode}
                disabled={creatingCode}
                className="px-4 py-2 text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors"
              >
                {creatingCode ? "..." : t("admin.invites.createCode")}
              </button>
            </div>
          </div>

          {/* Codes table */}
          <div className="glass-card rounded-2xl overflow-hidden">
            <div className="px-5 py-3.5 border-b border-[var(--glass-inner-border)]">
              <h3 className="text-sm font-semibold text-text-primary">{t("admin.invites.title")}</h3>
            </div>

            {invitesLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="w-6 h-6 rounded-full border-2 border-accent/30 border-t-accent animate-spin" />
              </div>
            ) : inviteCodes.length === 0 ? (
              <div className="px-5 py-12 text-center text-xs text-text-tertiary">
                {t("admin.invites.noCodes")}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[var(--glass-inner-border)] text-text-tertiary">
                      <th className="px-4 py-2.5 text-left font-medium">{t("admin.invites.code")}</th>
                      <th className="px-4 py-2.5 text-left font-medium">{t("admin.invites.label")}</th>
                      <th className="px-4 py-2.5 text-center font-medium">{t("admin.invites.uses")}</th>
                      <th className="px-4 py-2.5 text-center font-medium">{t("admin.invites.status")}</th>
                      <th className="px-4 py-2.5 text-left font-medium">{t("admin.invites.created")}</th>
                      <th className="px-4 py-2.5 text-right font-medium">{t("admin.invites.actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inviteCodes.map((ic) => {
                      const now = Date.now() / 1000;
                      const isExpired = ic.expires_at !== null && ic.expires_at < now;
                      const isExhausted = ic.used_count >= ic.max_uses;
                      const isRevoked = !ic.is_active;
                      const statusKey = isRevoked ? "revoked" : isExpired ? "expired" : isExhausted ? "exhausted" : "active";
                      const statusColor = statusKey === "active" ? "bg-success/15 text-success" : "bg-text-tertiary/15 text-text-tertiary";

                      return (
                        <tr key={ic.id} className="border-b border-[var(--glass-inner-border)] last:border-0 hover:bg-surface-hover/30 transition-colors">
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <code className="font-mono text-text-primary tracking-wider">{ic.code}</code>
                              <button
                                onClick={() => copyCode(ic.code)}
                                className="text-text-tertiary hover:text-text-primary transition-colors"
                                title="Copy"
                              >
                                {copiedCode === ic.code ? (
                                  <span className="text-success text-[10px]">{t("admin.invites.copied")}</span>
                                ) : (
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                  </svg>
                                )}
                              </button>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-text-secondary">{ic.label || "—"}</td>
                          <td className="px-4 py-3 text-center text-text-secondary tabular-nums">
                            {ic.used_count} / {ic.max_uses}
                          </td>
                          <td className="px-4 py-3 text-center">
                            <span className={`inline-block px-2 py-0.5 rounded-md text-[10px] font-semibold ${statusColor}`}>
                              {t(`admin.invites.${statusKey}`)}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-text-tertiary">{formatDate(ic.created_at)}</td>
                          <td className="px-4 py-3 text-right">
                            {ic.is_active && !isExpired && !isExhausted ? (
                              <button
                                onClick={() => handleRevokeCode(ic.id)}
                                className="text-[10px] text-error hover:text-error/80 font-medium transition-colors"
                              >
                                {t("admin.invites.revoke")}
                              </button>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB: SYSTEM ────────────────────────────────────────────── */}
      {activeTab === "system" && (
        <div className="space-y-6 animate-fade-in">
          {/* Refresh */}
          <div className="flex justify-end">
            <button
              onClick={fetchSystemData}
              disabled={systemLoading}
              className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 glass-inner rounded-lg text-text-secondary hover:text-text-primary transition-all disabled:opacity-50"
            >
              <IconRefresh spinning={systemLoading} />
              {t("admin.system.refresh")}
            </button>
          </div>

          {metrics && (
            <>
              {/* Row 1: Main metrics */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <MetricCard
                  label={t("admin.system.uptime")}
                  value={formatUptime(metrics.uptime_seconds)}
                  icon={<IconClock />}
                  accent="success"
                />
                <MetricCard
                  label={t("admin.system.requestsTotal")}
                  value={metrics.requests_total.toLocaleString()}
                  icon={<IconActivity />}
                />
                <ErrorRateGauge
                  errors={metrics.requests_errors}
                  total={metrics.requests_total}
                />
                <LatencyIndicator
                  avg={metrics.avg_latency_ms}
                  p95={metrics.p95_latency_ms}
                />
              </div>

              {/* Row 2: Secondary metrics */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <MetricCard
                  label={t("admin.system.activeJobs")}
                  value={metrics.active_jobs}
                  accent={metrics.active_jobs > 0 ? "warning" : "default"}
                />
                <MetricCard
                  label="4xx"
                  value={metrics.requests_4xx}
                  accent={metrics.requests_4xx > 0 ? "warning" : "default"}
                />
                <MetricCard
                  label="5xx"
                  value={metrics.requests_5xx}
                  accent={metrics.requests_5xx > 0 ? "error" : "default"}
                />
                <MetricCard
                  label={t("admin.system.authFailures")}
                  value={metrics.auth_failures}
                  sub={`${metrics.auth_attempts} ${t("admin.system.authAttempts").toLowerCase()}`}
                  icon={<IconShield />}
                  accent={metrics.auth_failures > 0 ? "error" : "default"}
                />
              </div>

              {/* Endpoints with bar chart */}
              {topEndpoints.length > 0 && (
                <div className="glass-card rounded-2xl overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                    <span className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary">
                      {t("admin.system.topEndpoints")}
                    </span>
                    <span className="text-[10px] text-text-tertiary">
                      Top {topEndpoints.length}
                    </span>
                  </div>
                  <div className="divide-y divide-border/20">
                    {topEndpoints.map(([path, ep]) => (
                      <EndpointBar
                        key={path}
                        path={path}
                        count={ep.count}
                        errors={ep.errors}
                        avgMs={ep.avg_ms}
                        maxCount={maxEndpointCount}
                      />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {/* Log viewer */}
          <div className="glass-card rounded-2xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary">
                  {t("admin.system.recentLogs")}
                </span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-md glass-inner text-text-tertiary font-medium">
                  {logs.length}
                </span>
              </div>
              <div className="flex items-center gap-1">
                {["", "DEBUG", "INFO", "WARNING", "ERROR"].map((level) => (
                  <button
                    key={level}
                    onClick={() => setLogLevel(level)}
                    className={`text-[10px] px-2 py-1 rounded-md font-medium transition-all ${
                      logLevel === level
                        ? "bg-accent/12 text-accent"
                        : "text-text-tertiary hover:text-text-secondary hover:bg-surface-hover/50"
                    }`}
                  >
                    {level || t("admin.system.allLevels")}
                  </button>
                ))}
              </div>
            </div>
            <div className="overflow-y-auto max-h-[420px]">
              {logs.length === 0 ? (
                <div className="px-4 py-10 text-center text-xs text-text-tertiary">
                  {t("admin.system.noLogs")}
                </div>
              ) : (
                <div className="divide-y divide-border/10">
                  {logs.map((entry, i) => (
                    <div key={i} className="flex items-start gap-2 px-4 py-2 hover:bg-surface-hover/20 transition-colors group">
                      <span
                        className={`w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 ${
                          LOG_LEVEL_DOT[entry.level] ?? "bg-text-tertiary"
                        }`}
                      />
                      <span className="text-[10px] font-mono text-text-tertiary whitespace-nowrap w-16 flex-shrink-0 pt-0.5">
                        {formatLogTime(entry.timestamp)}
                      </span>
                      <span
                        className={`text-[10px] font-mono whitespace-nowrap w-14 flex-shrink-0 pt-0.5 font-medium ${
                          LOG_LEVEL_STYLES[entry.level] ?? "text-text-secondary"
                        }`}
                      >
                        {entry.level}
                      </span>
                      <span className="text-[10px] font-mono text-text-tertiary whitespace-nowrap w-20 truncate flex-shrink-0 pt-0.5 hidden sm:block">
                        {entry.logger}
                      </span>
                      <span className="text-xs font-mono text-text-secondary break-all min-w-0 pt-0.5">
                        {entry.message}
                        {entry.exc && (
                          <span className="block text-error text-[10px] mt-0.5 truncate opacity-80">
                            {entry.exc}
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── TAB: ANALYTICS ────────────────────────────────────────── */}
      {activeTab === "analytics" && (
        <div className="space-y-6 animate-fade-in">
          {/* Period selector + worker queue gauge + refresh */}
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex gap-1 glass-inner rounded-xl p-1">
              {([7, 30, 90] as const).map((d) => (
                <button
                  key={d}
                  onClick={() => setAnalyticsDays(d)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    analyticsDays === d
                      ? "bg-accent text-white"
                      : "text-text-tertiary hover:text-text-primary"
                  }`}
                >
                  {d}d
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              {metrics != null && (
                <div className="glass-inner rounded-xl px-3 py-1.5 flex items-center gap-2">
                  <span className="text-[10px] uppercase tracking-wider text-text-tertiary font-medium">
                    {t("admin.system.activeJobs")}
                  </span>
                  <span className={`text-sm font-semibold tabular-nums ${metrics.active_jobs > 0 ? "text-warning" : "text-success"}`}>
                    {metrics.active_jobs}
                  </span>
                </div>
              )}
              <button
                onClick={fetchAnalyticsData}
                disabled={analyticsLoading}
                className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 glass-inner rounded-lg text-text-secondary hover:text-text-primary transition-all disabled:opacity-50"
              >
                <IconRefresh spinning={analyticsLoading} />
                {t("admin.system.refresh")}
              </button>
            </div>
          </div>

          {analyticsLoading && !analyticsData ? (
            <div className="flex items-center justify-center py-20">
              <div className="w-8 h-8 rounded-full border-2 border-accent/30 border-t-accent animate-spin" />
            </div>
          ) : analyticsData ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">

              {/* Verdict Distribution — PieChart (donut) */}
              <div className="glass-card rounded-2xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-border">
                  <h3 className="text-sm font-semibold text-text-primary">{t("admin.analytics.verdictDistribution")}</h3>
                  <p className="text-[10px] text-text-tertiary mt-0.5">Last {analyticsData.period_days} days</p>
                </div>
                <div className="p-4">
                  <ResponsiveContainer width="100%" height={240}>
                    <PieChart>
                      <Pie
                        data={Object.entries(analyticsData.verdict_distribution).map(([name, value]) => ({ name, value }))}
                        cx="50%"
                        cy="50%"
                        innerRadius={55}
                        outerRadius={85}
                        paddingAngle={2}
                        dataKey="value"
                      >
                        {Object.entries(analyticsData.verdict_distribution).map(([name]) => (
                          <Cell key={name} fill={VERDICT_COLORS[name] ?? "#6b7280"} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{ background: "var(--color-surface, #1a1a2e)", border: "1px solid var(--color-border, #2a2a3e)", borderRadius: "8px", fontSize: "11px" }}
                        formatter={(value: number, name: string) => {
                          const total = Object.values(analyticsData.verdict_distribution).reduce((a, b) => a + b, 0);
                          return [`${value} (${total ? Math.round((value / total) * 100) : 0}%)`, name];
                        }}
                      />
                      <Legend iconSize={8} wrapperStyle={{ fontSize: "10px", paddingTop: "8px" }} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Confidence Histogram — BarChart */}
              <div className="glass-card rounded-2xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-border">
                  <h3 className="text-sm font-semibold text-text-primary">{t("admin.analytics.confidenceHistogram")}</h3>
                </div>
                <div className="p-4">
                  <ResponsiveContainer width="100%" height={240}>
                    <BarChart data={analyticsData.confidence_histogram} margin={{ top: 4, right: 4, bottom: 4, left: -20 }}>
                      <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.1} />
                      <XAxis dataKey="bucket" tick={{ fontSize: 9 }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fontSize: 9 }} tickLine={false} axisLine={false} allowDecimals={false} />
                      <Tooltip
                        contentStyle={{ background: "var(--color-surface, #1a1a2e)", border: "1px solid var(--color-border, #2a2a3e)", borderRadius: "8px", fontSize: "11px" }}
                      />
                      <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                        {analyticsData.confidence_histogram.map((_, i) => (
                          <Cell key={i} fill={HISTOGRAM_COLORS[i]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Top Domains — Horizontal BarChart */}
              <div className="glass-card rounded-2xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-border">
                  <h3 className="text-sm font-semibold text-text-primary">{t("admin.analytics.topDomains")}</h3>
                </div>
                <div className="p-4">
                  {analyticsData.top_domains.length > 0 ? (
                    <ResponsiveContainer width="100%" height={Math.max(200, analyticsData.top_domains.length * 26)}>
                      <BarChart layout="vertical" data={analyticsData.top_domains} margin={{ top: 4, right: 12, bottom: 4, left: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.1} horizontal={false} />
                        <XAxis type="number" tick={{ fontSize: 9 }} tickLine={false} axisLine={false} allowDecimals={false} />
                        <YAxis type="category" dataKey="domain" width={110} tick={{ fontSize: 9 }} tickLine={false} axisLine={false} />
                        <Tooltip
                          contentStyle={{ background: "var(--color-surface, #1a1a2e)", border: "1px solid var(--color-border, #2a2a3e)", borderRadius: "8px", fontSize: "11px" }}
                        />
                        <Bar dataKey="count" radius={[0, 3, 3, 0]}>
                          {analyticsData.top_domains.map((d, i) => (
                            <Cell key={i} fill={domainTierColor(d.avg_tier)} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <p className="text-xs text-text-tertiary text-center py-8">{t("admin.analytics.noData")}</p>
                  )}
                </div>
              </div>

              {/* Analyses Per Day — AreaChart */}
              <div className="glass-card rounded-2xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-border">
                  <h3 className="text-sm font-semibold text-text-primary">{t("admin.analytics.analysesPerDay")}</h3>
                </div>
                <div className="p-4">
                  <ResponsiveContainer width="100%" height={240}>
                    <AreaChart data={analyticsData.analyses_per_day} margin={{ top: 4, right: 4, bottom: 4, left: -20 }}>
                      <defs>
                        <linearGradient id="analyticsAreaGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.1} />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 9 }}
                        tickLine={false}
                        axisLine={false}
                        tickFormatter={(v: string) => v.slice(5)}
                        interval="preserveStartEnd"
                      />
                      <YAxis tick={{ fontSize: 9 }} tickLine={false} axisLine={false} allowDecimals={false} />
                      <Tooltip
                        contentStyle={{ background: "var(--color-surface, #1a1a2e)", border: "1px solid var(--color-border, #2a2a3e)", borderRadius: "8px", fontSize: "11px" }}
                      />
                      <Area
                        type="monotone"
                        dataKey="count"
                        stroke="#6366f1"
                        strokeWidth={2}
                        fill="url(#analyticsAreaGrad)"
                        dot={false}
                        activeDot={{ r: 4, fill: "#6366f1" }}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>

            </div>
          ) : (
            <p className="text-xs text-text-tertiary text-center py-10">{t("admin.analytics.noData")}</p>
          )}
        </div>
      )}
    </div>
  );
}
