"use client";

import { useState, useEffect, useMemo } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  BarChart,
  Bar,
  LineChart,
  Line,
  AreaChart,
  Area,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from "recharts";
import { useI18n } from "../lib/i18n";

// ── Types ────────────────────────────────────────────────────────────

type Period = "7d" | "30d" | "90d" | "all";

interface TimelineBucket {
  date: string;
  count: number;
  avg_confidence: number;
  rating_distribution: Record<string, number>;
  avg_claims_per_analysis: number;
}

interface TimelineData {
  buckets: TimelineBucket[];
  period: string;
  bucket: string;
  total_analyses: number;
}

interface Topic {
  topic: string;
  count: number;
  avg_rating_score: number;
  trend: "rising" | "stable" | "declining";
}

interface TopicsData {
  topics: Topic[];
  period: string;
}

interface Source {
  domain: string;
  citation_count: number;
  first_seen: string;
  last_seen: string;
}

interface SourcesData {
  sources: Source[];
  total_unique_sources: number;
  period: string;
}

interface AccuracyBucket {
  date: string;
  avg_confidence: number;
  high_confidence_ratio: number;
  fabricated_ratio: number;
}

interface ConfidenceBand {
  range: string;
  count: number;
  avg_rating_score: number;
}

interface AccuracyData {
  accuracy_over_time: AccuracyBucket[];
  overall_brier_score: number;
  confidence_bands: ConfidenceBand[];
  period: string;
}

interface Platform {
  platform: string;
  count: number;
  avg_rating_score: number;
  avg_confidence: number;
}

interface PlatformsData {
  platforms: Platform[];
  period: string;
}

// ── Colors ───────────────────────────────────────────────────────────

const RATING_COLORS: Record<string, string> = {
  RELIABLE:          "#1a6b3c",
  MOSTLY_RELIABLE:   "#2d9e5f",
  MIXED:             "#a16200",
  MISLEADING:        "#c41e1e",
  HIGHLY_MISLEADING: "#8b1515",
  FABRICATED:        "#5c0000",
};

const TREND_STYLE: Record<string, string> = {
  rising:   "text-success",
  stable:   "text-text-tertiary",
  declining: "text-error",
};

const PLATFORM_COLORS = [
  "#c41e1e", "#1a6b3c", "#a16200", "#1e5f8b", "#6b1a6b", "#6b6b1a",
];

const RATINGS_ORDER = [
  "RELIABLE", "MOSTLY_RELIABLE", "MIXED", "MISLEADING", "HIGHLY_MISLEADING", "FABRICATED",
];

// ── Helpers ───────────────────────────────────────────────────────────

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function shortLabel(date: string): string {
  if (!date) return "";
  if (date.includes("W")) {
    // ISO week: 2026-W12 → W12
    return date.split("-")[1] ?? date;
  }
  const parts = date.split("-");
  if (parts.length === 2) return date; // year-month
  return parts.slice(1).join("/");   // month/day
}

function computeKpis(timeline: TimelineData | null) {
  if (!timeline || !timeline.buckets || timeline.total_analyses === 0) {
    return { total: 0, avgConf: 0, topRating: "—", trendSign: "→" };
  }
  const buckets = timeline.buckets;
  const total = timeline.total_analyses;

  const totalConf = buckets.reduce((s, b) => s + b.avg_confidence * b.count, 0);
  const avgConf = total > 0 ? totalConf / total : 0;

  // Mode rating across all buckets
  const ratingTotals: Record<string, number> = {};
  for (const b of buckets) {
    for (const [r, n] of Object.entries(b.rating_distribution)) {
      ratingTotals[r] = (ratingTotals[r] ?? 0) + n;
    }
  }
  const topRating =
    Object.entries(ratingTotals).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "—";

  // Trend: compare first half vs second half count
  const mid = Math.floor(buckets.length / 2);
  const first = buckets.slice(0, mid).reduce((s, b) => s + b.count, 0);
  const second = buckets.slice(mid).reduce((s, b) => s + b.count, 0);
  const trendSign = second > first * 1.1 ? "↑" : second < first * 0.9 ? "↓" : "→";

  return { total, avgConf, topRating, trendSign };
}

// ── Skeleton loader ───────────────────────────────────────────────────

function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`animate-pulse bg-surface-hover/60 rounded-xl ${className}`} />
  );
}

// ── Chart tooltip style ───────────────────────────────────────────────

const TOOLTIP_STYLE = {
  backgroundColor: "var(--surface-card, #fff)",
  border: "1px solid var(--border)",
  borderRadius: "10px",
  fontSize: "11px",
  color: "var(--text-primary)",
};

// ── Sub-components ────────────────────────────────────────────────────

function KpiCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="glass-card p-4 flex flex-col gap-1">
      <span className="text-[11px] text-text-tertiary font-medium uppercase tracking-wider">
        {label}
      </span>
      <span className="text-2xl font-bold text-text-primary leading-tight">{value}</span>
      {sub && <span className="text-xs text-text-tertiary">{sub}</span>}
    </div>
  );
}

function SectionCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="glass-card p-5">
      <h2 className="text-sm font-semibold text-text-secondary mb-4">{title}</h2>
      {children}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const { t } = useI18n();
  const [period, setPeriod] = useState<Period>("30d");

  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [topics, setTopics] = useState<TopicsData | null>(null);
  const [sources, setSources] = useState<SourcesData | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracyData | null>(null);
  const [platforms, setPlatforms] = useState<PlatformsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const p = `period=${period}`;
    const fetchJson = (url: string) =>
      fetch(url).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });

    Promise.all([
      fetchJson(`/api/v1/analytics/timeline?${p}`),
      fetchJson(`/api/v1/analytics/topics?${p}`),
      fetchJson(`/api/v1/analytics/sources?${p}`),
      fetchJson(`/api/v1/analytics/accuracy?${p}`),
      fetchJson(`/api/v1/analytics/platforms?${p}`),
    ])
      .then(([tl, tp, sr, ac, pf]) => {
        if (tl.error === "archive_disabled") {
          setError("archive_disabled");
          return;
        }
        setTimeline(tl);
        setTopics(tp);
        setSources(sr);
        setAccuracy(ac);
        setPlatforms(pf);
      })
      .catch(() => setError("fetch_error"))
      .finally(() => setLoading(false));
  }, [period]);

  const kpis = useMemo(() => computeKpis(timeline), [timeline]);

  // ── Derived chart data ──────────────────────────────────────────

  // Timeline chart: buckets → {date, count, avgConf}
  const timelineChartData = useMemo(
    () =>
      (timeline?.buckets ?? []).map((b) => ({
        date: shortLabel(b.date),
        count: b.count,
        conf: Math.round(b.avg_confidence * 100),
        claims: b.avg_claims_per_analysis,
      })),
    [timeline]
  );

  // Stacked area: each bucket → {date, RELIABLE, MOSTLY_RELIABLE, …} (percentages)
  const ratingAreaData = useMemo(
    () =>
      (timeline?.buckets ?? []).map((b) => {
        const total = b.count || 1;
        const row: Record<string, number | string> = { date: shortLabel(b.date) };
        for (const r of RATINGS_ORDER) {
          row[r] = Math.round(((b.rating_distribution[r] ?? 0) / total) * 100);
        }
        return row;
      }),
    [timeline]
  );

  // Topics: top 10, horizontal bar
  const topicsChartData = useMemo(
    () =>
      (topics?.topics ?? [])
        .slice(0, 10)
        .map((t) => ({ name: t.topic, count: t.count, trend: t.trend })),
    [topics]
  );

  // Platforms: pie data
  const platformsChartData = useMemo(
    () => (platforms?.platforms ?? []).map((p) => ({ name: p.platform, value: p.count })),
    [platforms]
  );

  // Accuracy: actual confidence vs ideal diagonal
  const accuracyBandData = useMemo(
    () =>
      (accuracy?.confidence_bands ?? []).map((b) => ({
        range: b.range,
        actual: b.avg_rating_score,
        count: b.count,
      })),
    [accuracy]
  );

  // ── Periods ─────────────────────────────────────────────────────
  const PERIODS: Period[] = ["7d", "30d", "90d", "all"];

  // ── Empty state ─────────────────────────────────────────────────
  const isEmpty = !loading && timeline?.total_analyses === 0;

  // ── Render ──────────────────────────────────────────────────────
  return (
    <div className="min-h-screen pt-20 pb-12 px-4 sm:px-6 max-w-6xl mx-auto">

      {/* Period selector */}
      <div className="flex items-center gap-2 mb-6">
        <span className="text-sm font-semibold text-text-primary mr-1">
          {t("analytics.title")}
        </span>
        <div className="flex items-center gap-1 ml-auto">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                period === p
                  ? "bg-accent text-white"
                  : "glass-inner text-text-secondary hover:text-text-primary"
              }`}
            >
              {t(`analytics.period.${p}`)}
            </button>
          ))}
        </div>
      </div>

      {/* Error / disabled state */}
      {error && (
        <div className="glass-card p-8 text-center text-text-tertiary text-sm">
          {error === "archive_disabled"
            ? "Archiv ist deaktiviert."
            : "Daten konnten nicht geladen werden."}
        </div>
      )}

      {!error && (
        <>
          {/* KPI Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            {loading ? (
              <>
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
              </>
            ) : (
              <>
                <KpiCard label={t("analytics.kpi.total")} value={kpis.total.toLocaleString()} />
                <KpiCard
                  label={t("analytics.kpi.avgConfidence")}
                  value={pct(kpis.avgConf)}
                />
                <KpiCard
                  label={t("analytics.kpi.topRating")}
                  value={kpis.topRating === "—" ? "—" : kpis.topRating.replace(/_/g, " ")}
                />
                <KpiCard label={t("analytics.kpi.trend")} value={kpis.trendSign} />
              </>
            )}
          </div>

          {/* Empty state */}
          {isEmpty && (
            <div className="glass-card p-12 text-center text-text-tertiary text-sm mt-4">
              {t("analytics.empty")}
            </div>
          )}

          {!isEmpty && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

              {/* 1. Timeline: volume + confidence */}
              <div className="lg:col-span-2">
                <SectionCard title={t("analytics.timeline.title")}>
                  {loading ? (
                    <Skeleton className="h-52" />
                  ) : (
                    <ResponsiveContainer width="100%" height={220}>
                      <ComposedChart data={timelineChartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.5} />
                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
                        <YAxis yAxisId="left" tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
                        <YAxis
                          yAxisId="right"
                          orientation="right"
                          domain={[0, 100]}
                          unit="%"
                          tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                        />
                        <Tooltip contentStyle={TOOLTIP_STYLE} />
                        <Bar yAxisId="left" dataKey="count" fill="#c41e1e" fillOpacity={0.7} radius={[3, 3, 0, 0]} name="Analysen" />
                        <Line
                          yAxisId="right"
                          type="monotone"
                          dataKey="conf"
                          stroke="#1a6b3c"
                          strokeWidth={2}
                          dot={false}
                          name="Ø Konfidenz %"
                        />
                      </ComposedChart>
                    </ResponsiveContainer>
                  )}
                </SectionCard>
              </div>

              {/* 2. Rating distribution stacked area */}
              <div className="lg:col-span-2">
                <SectionCard title={t("analytics.ratingTrend.title")}>
                  {loading ? (
                    <Skeleton className="h-52" />
                  ) : (
                    <ResponsiveContainer width="100%" height={220}>
                      <AreaChart data={ratingAreaData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.5} />
                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
                        <YAxis unit="%" tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
                        <Tooltip contentStyle={TOOLTIP_STYLE} />
                        <Legend iconSize={10} wrapperStyle={{ fontSize: 10 }} />
                        {RATINGS_ORDER.map((r) => (
                          <Area
                            key={r}
                            type="monotone"
                            dataKey={r}
                            stackId="1"
                            stroke={RATING_COLORS[r]}
                            fill={RATING_COLORS[r]}
                            fillOpacity={0.8}
                            name={r.replace(/_/g, " ")}
                          />
                        ))}
                      </AreaChart>
                    </ResponsiveContainer>
                  )}
                </SectionCard>
              </div>

              {/* 3. Top Topics */}
              <SectionCard title={t("analytics.topics.title")}>
                {loading ? (
                  <Skeleton className="h-52" />
                ) : topicsChartData.length === 0 ? (
                  <p className="text-xs text-text-tertiary">{t("analytics.empty")}</p>
                ) : (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart
                      data={topicsChartData}
                      layout="vertical"
                      margin={{ left: 8, right: 8 }}
                    >
                      <XAxis type="number" tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
                      <YAxis
                        type="category"
                        dataKey="name"
                        tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                        width={90}
                      />
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                      <Bar dataKey="count" radius={[0, 3, 3, 0]} name="Häufigkeit">
                        {topicsChartData.map((entry, index) => (
                          <Cell
                            key={index}
                            fill={
                              entry.trend === "rising"
                                ? "#1a6b3c"
                                : entry.trend === "declining"
                                ? "#c41e1e"
                                : "#a16200"
                            }
                            fillOpacity={0.75}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </SectionCard>

              {/* 4. Platforms */}
              <SectionCard title={t("analytics.platforms.title")}>
                {loading ? (
                  <Skeleton className="h-52" />
                ) : platformsChartData.length === 0 ? (
                  <p className="text-xs text-text-tertiary">{t("analytics.empty")}</p>
                ) : (
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie
                        data={platformsChartData}
                        cx="50%"
                        cy="50%"
                        innerRadius={55}
                        outerRadius={90}
                        dataKey="value"
                        nameKey="name"
                        label={({ name, percent }) =>
                          `${name} ${Math.round((percent ?? 0) * 100)}%`
                        }
                        labelLine={false}
                        fontSize={10}
                      >
                        {platformsChartData.map((_entry, index) => (
                          <Cell
                            key={index}
                            fill={PLATFORM_COLORS[index % PLATFORM_COLORS.length]}
                            fillOpacity={0.85}
                          />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                    </PieChart>
                  </ResponsiveContainer>
                )}
              </SectionCard>

              {/* 5. Sources table */}
              <div className="lg:col-span-2">
                <SectionCard title={t("analytics.sources.title")}>
                  {loading ? (
                    <Skeleton className="h-40" />
                  ) : !sources || sources.sources.length === 0 ? (
                    <p className="text-xs text-text-tertiary">{t("analytics.empty")}</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-text-tertiary border-b border-border/40">
                            <th className="text-left py-2 pr-4 font-medium">{t("analytics.sources.domain")}</th>
                            <th className="text-right py-2 pr-4 font-medium">{t("analytics.sources.citations")}</th>
                            <th className="text-right py-2 pr-4 font-medium hidden sm:table-cell">{t("analytics.sources.firstSeen")}</th>
                            <th className="text-right py-2 font-medium hidden sm:table-cell">{t("analytics.sources.lastSeen")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sources.sources.slice(0, 15).map((src, i) => (
                            <tr
                              key={i}
                              className="border-b border-border/20 hover:bg-surface-hover/30 transition-colors"
                            >
                              <td className="py-2 pr-4 text-text-primary font-medium">{src.domain}</td>
                              <td className="py-2 pr-4 text-right text-text-secondary">{src.citation_count}</td>
                              <td className="py-2 pr-4 text-right text-text-tertiary hidden sm:table-cell">{src.first_seen}</td>
                              <td className="py-2 text-right text-text-tertiary hidden sm:table-cell">{src.last_seen}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {sources.total_unique_sources > 15 && (
                        <p className="text-[10px] text-text-tertiary mt-2">
                          +{sources.total_unique_sources - 15} weitere Quellen
                        </p>
                      )}
                    </div>
                  )}
                </SectionCard>
              </div>

              {/* 6. Accuracy calibration */}
              <div className="lg:col-span-2">
                <SectionCard title={t("analytics.accuracy.title")}>
                  {loading ? (
                    <Skeleton className="h-52" />
                  ) : !accuracy || accuracy.confidence_bands.every((b) => b.count === 0) ? (
                    <p className="text-xs text-text-tertiary">{t("analytics.empty")}</p>
                  ) : (
                    <div className="flex flex-col sm:flex-row gap-6">
                      {/* Confidence bands bar chart */}
                      <div className="flex-1">
                        <p className="text-[10px] text-text-tertiary mb-2">
                          Konfidenzbereich → Ø Bewertungs-Score (5=Zuverlässig, 0=Falsch)
                        </p>
                        <ResponsiveContainer width="100%" height={180}>
                          <BarChart data={accuracyBandData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.5} />
                            <XAxis dataKey="range" tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
                            <YAxis domain={[0, 5]} tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
                            <Tooltip contentStyle={TOOLTIP_STYLE} />
                            <ReferenceLine y={2.5} stroke="var(--text-tertiary)" strokeDasharray="4 4" strokeOpacity={0.5} />
                            <Bar dataKey="actual" name="Ø Rating-Score" radius={[3, 3, 0, 0]}>
                              {accuracyBandData.map((entry, index) => (
                                <Cell
                                  key={index}
                                  fill={entry.actual >= 3.5 ? "#1a6b3c" : entry.actual >= 2 ? "#a16200" : "#c41e1e"}
                                  fillOpacity={0.75}
                                />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                      {/* Brier score + accuracy over time mini stats */}
                      <div className="flex flex-col gap-3 justify-center sm:w-48">
                        <div className="glass-inner p-3 rounded-xl">
                          <p className="text-[10px] text-text-tertiary mb-1">Brier Score</p>
                          <p className="text-xl font-bold text-text-primary">
                            {accuracy.overall_brier_score.toFixed(3)}
                          </p>
                          <p className="text-[10px] text-text-tertiary">
                            {accuracy.overall_brier_score < 0.1
                              ? "Sehr gut kalibriert"
                              : accuracy.overall_brier_score < 0.2
                              ? "Gut kalibriert"
                              : accuracy.overall_brier_score < 0.33
                              ? "Mäßig kalibriert"
                              : "Schlecht kalibriert"}
                          </p>
                        </div>
                        {accuracy.accuracy_over_time.length > 0 && (
                          <div className="glass-inner p-3 rounded-xl">
                            <p className="text-[10px] text-text-tertiary mb-1">Ø Konfidenz (aktuell)</p>
                            <p className="text-xl font-bold text-text-primary">
                              {pct(
                                accuracy.accuracy_over_time[
                                  accuracy.accuracy_over_time.length - 1
                                ]?.avg_confidence ?? 0
                              )}
                            </p>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </SectionCard>
              </div>

            </div>
          )}
        </>
      )}
    </div>
  );
}
