"use client";

import { useState, useEffect, useMemo, useCallback, useRef, type ReactNode } from "react";
import { useI18n } from "../lib/i18n";
import {
  KPICards,
  PeriodSelector,
  TimelineChart,
  RatingDistribution,
  TopTopics,
  PlatformDonut,
  SourcesTable,
  AccuracyCalibration,
  DashboardGrid,
  WidgetVisibilityToggle,
  useDashboardLayout,
} from "../components/analytics";
import {
  Period,
  DateRange,
  WidgetId,
  TimelineData,
  TopicsData,
  SourcesData,
  AccuracyData,
  PlatformsData,
  RATINGS_ORDER,
  computeKpis,
} from "../components/analytics/types";
import { exportCsv, makeCsvFilename } from "../components/analytics/exportCsv";
import { exportPng, makePngFilename } from "../components/analytics/exportPng";

export default function AnalyticsPage() {
  const { t } = useI18n();
  const [period, setPeriod] = useState<Period>("30d");
  const [dateRange, setDateRange] = useState<DateRange | null>(null);
  const { order, visible, reorder, toggleVisibility } = useDashboardLayout();

  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [topics, setTopics] = useState<TopicsData | null>(null);
  const [sources, setSources] = useState<SourcesData | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracyData | null>(null);
  const [platforms, setPlatforms] = useState<PlatformsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const periodLabel = period === "custom" && dateRange
    ? `${dateRange.from}_${dateRange.to}`
    : period;

  useEffect(() => {
    let qs: string;
    if (period === "custom" && dateRange?.from && dateRange?.to) {
      if (dateRange.from > dateRange.to) return;
      qs = `date_from=${dateRange.from}&date_to=${dateRange.to}`;
    } else if (period === "custom") {
      return;
    } else {
      qs = `period=${period}`;
    }

    setLoading(true);
    setError(null);
    const fetchJson = (url: string) =>
      fetch(url).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });

    Promise.all([
      fetchJson(`/api/v1/analytics/timeline?${qs}`),
      fetchJson(`/api/v1/analytics/topics?${qs}`),
      fetchJson(`/api/v1/analytics/sources?${qs}`),
      fetchJson(`/api/v1/analytics/accuracy?${qs}`),
      fetchJson(`/api/v1/analytics/platforms?${qs}`),
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
  }, [period, dateRange]);

  const kpis = useMemo(() => computeKpis(timeline), [timeline]);
  const isEmpty = !loading && timeline?.total_analyses === 0;

  // ── Chart refs for PNG export ────────────────────────────────────
  const timelineRef = useRef<HTMLDivElement>(null);
  const ratingRef = useRef<HTMLDivElement>(null);
  const topicsRef = useRef<HTMLDivElement>(null);
  const platformsRef = useRef<HTMLDivElement>(null);
  const accuracyRef = useRef<HTMLDivElement>(null);

  // ── CSV export callbacks ─────────────────────────────────────────
  const csvTimeline = useCallback(() => {
    if (!timeline?.buckets) return;
    const ratingCols = RATINGS_ORDER.map((r) => ({
      key: r,
      header: r.replace(/_/g, " "),
    }));
    exportCsv(
      timeline.buckets.map((b) => ({
        date: b.date,
        count: b.count,
        avg_confidence: b.avg_confidence,
        avg_claims: b.avg_claims_per_analysis,
        ...b.rating_distribution,
      })),
      [
        { key: "date", header: "Date" },
        { key: "count", header: "Analyses" },
        { key: "avg_confidence", header: "Avg Confidence" },
        { key: "avg_claims", header: "Avg Claims" },
        ...ratingCols,
      ],
      makeCsvFilename("timeline", periodLabel),
    );
  }, [timeline, periodLabel]);

  const csvRatingDist = useCallback(() => {
    if (!timeline?.buckets) return;
    exportCsv(
      timeline.buckets.map((b) => {
        const total = b.count || 1;
        const row: Record<string, unknown> = { date: b.date };
        for (const r of RATINGS_ORDER) {
          row[r] = Math.round(((b.rating_distribution[r] ?? 0) / total) * 100);
        }
        return row;
      }),
      [
        { key: "date", header: "Date" },
        ...RATINGS_ORDER.map((r) => ({ key: r, header: r.replace(/_/g, " ") + " %" })),
      ],
      makeCsvFilename("rating_distribution", periodLabel),
    );
  }, [timeline, periodLabel]);

  const csvTopics = useCallback(() => {
    if (!topics?.topics) return;
    exportCsv(
      topics.topics as unknown as Record<string, unknown>[],
      [
        { key: "topic", header: "Topic" },
        { key: "count", header: "Count" },
        { key: "avg_rating_score", header: "Avg Rating Score" },
        { key: "trend", header: "Trend" },
      ],
      makeCsvFilename("topics", periodLabel),
    );
  }, [topics, periodLabel]);

  const csvPlatforms = useCallback(() => {
    if (!platforms?.platforms) return;
    exportCsv(
      platforms.platforms as unknown as Record<string, unknown>[],
      [
        { key: "platform", header: "Platform" },
        { key: "count", header: "Count" },
        { key: "avg_rating_score", header: "Avg Rating Score" },
        { key: "avg_confidence", header: "Avg Confidence" },
      ],
      makeCsvFilename("platforms", periodLabel),
    );
  }, [platforms, periodLabel]);

  const csvSources = useCallback(() => {
    if (!sources?.sources) return;
    exportCsv(
      sources.sources as unknown as Record<string, unknown>[],
      [
        { key: "domain", header: "Domain" },
        { key: "citation_count", header: "Citations" },
        { key: "first_seen", header: "First Seen" },
        { key: "last_seen", header: "Last Seen" },
      ],
      makeCsvFilename("sources", periodLabel),
    );
  }, [sources, periodLabel]);

  const csvAccuracy = useCallback(() => {
    if (!accuracy?.confidence_bands) return;
    exportCsv(
      accuracy.confidence_bands as unknown as Record<string, unknown>[],
      [
        { key: "range", header: "Range" },
        { key: "count", header: "Count" },
        { key: "avg_rating_score", header: "Avg Rating Score" },
      ],
      makeCsvFilename("accuracy", periodLabel),
    );
  }, [accuracy, periodLabel]);

  // ── PNG export callbacks ─────────────────────────────────────────
  const pngTimeline = useCallback(() => {
    if (timelineRef.current) exportPng(timelineRef.current, makePngFilename("timeline", periodLabel));
  }, [periodLabel]);

  const pngRating = useCallback(() => {
    if (ratingRef.current) exportPng(ratingRef.current, makePngFilename("rating_distribution", periodLabel));
  }, [periodLabel]);

  const pngTopics = useCallback(() => {
    if (topicsRef.current) exportPng(topicsRef.current, makePngFilename("topics", periodLabel));
  }, [periodLabel]);

  const pngPlatforms = useCallback(() => {
    if (platformsRef.current) exportPng(platformsRef.current, makePngFilename("platforms", periodLabel));
  }, [periodLabel]);

  const pngAccuracy = useCallback(() => {
    if (accuracyRef.current) exportPng(accuracyRef.current, makePngFilename("accuracy", periodLabel));
  }, [periodLabel]);

  // ── Widget map for DashboardGrid ─────────────────────────────────
  const widgetMap: Record<WidgetId, ReactNode> = {
    timeline: (
      <TimelineChart
        buckets={timeline?.buckets ?? []}
        loading={loading}
        onExportCsv={csvTimeline}
        onExportPng={pngTimeline}
        chartRef={timelineRef}
      />
    ),
    ratingDist: (
      <RatingDistribution
        buckets={timeline?.buckets ?? []}
        loading={loading}
        onExportCsv={csvRatingDist}
        onExportPng={pngRating}
        chartRef={ratingRef}
      />
    ),
    topics: (
      <TopTopics
        topics={topics?.topics ?? []}
        loading={loading}
        onExportCsv={csvTopics}
        onExportPng={pngTopics}
        chartRef={topicsRef}
      />
    ),
    platforms: (
      <PlatformDonut
        platforms={platforms?.platforms ?? []}
        loading={loading}
        onExportCsv={csvPlatforms}
        onExportPng={pngPlatforms}
        chartRef={platformsRef}
      />
    ),
    sources: (
      <SourcesTable
        sources={sources}
        loading={loading}
        onExportCsv={csvSources}
      />
    ),
    accuracy: (
      <AccuracyCalibration
        accuracy={accuracy}
        loading={loading}
        onExportCsv={csvAccuracy}
        onExportPng={pngAccuracy}
        chartRef={accuracyRef}
      />
    ),
  };

  const visibleOrder = order.filter((id) => visible[id]);

  return (
    <div className="min-h-screen pt-20 pb-12 px-4 sm:px-6 max-w-6xl mx-auto">

      {/* Period selector + settings */}
      <div className="flex items-center gap-2 mb-6">
        <span className="text-sm font-semibold text-text-primary mr-1">
          {t("analytics.title")}
        </span>
        <div className="flex items-center gap-2 ml-auto">
          <PeriodSelector
            period={period}
            onPeriodChange={setPeriod}
            dateRange={dateRange}
            onDateRangeChange={setDateRange}
          />
          <WidgetVisibilityToggle
            visible={visible}
            onToggle={toggleVisibility}
          />
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
          <KPICards kpis={kpis} loading={loading} />

          {isEmpty && (
            <div className="glass-card p-12 text-center text-text-tertiary text-sm mt-4">
              {t("analytics.empty")}
            </div>
          )}

          {!isEmpty && (
            <DashboardGrid order={visibleOrder} onReorder={reorder}>
              {widgetMap}
            </DashboardGrid>
          )}
        </>
      )}
    </div>
  );
}
