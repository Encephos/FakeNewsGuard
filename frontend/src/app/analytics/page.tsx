"use client";

import { useState, useEffect, useMemo } from "react";
import { useI18n } from "../lib/i18n";
import {
  KPICards,
  TimelineChart,
  RatingDistribution,
  TopTopics,
  PlatformDonut,
  SourcesTable,
  AccuracyCalibration,
} from "../components/analytics";
import {
  Period,
  TimelineData,
  TopicsData,
  SourcesData,
  AccuracyData,
  PlatformsData,
  computeKpis,
} from "../components/analytics/types";

const PERIODS: Period[] = ["7d", "30d", "90d", "all"];

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

  const isEmpty = !loading && timeline?.total_analyses === 0;

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
          <KPICards kpis={kpis} loading={loading} />

          {isEmpty && (
            <div className="glass-card p-12 text-center text-text-tertiary text-sm mt-4">
              {t("analytics.empty")}
            </div>
          )}

          {!isEmpty && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="lg:col-span-2">
                <TimelineChart
                  buckets={timeline?.buckets ?? []}
                  loading={loading}
                />
              </div>

              <div className="lg:col-span-2">
                <RatingDistribution
                  buckets={timeline?.buckets ?? []}
                  loading={loading}
                />
              </div>

              <TopTopics
                topics={topics?.topics ?? []}
                loading={loading}
              />

              <PlatformDonut
                platforms={platforms?.platforms ?? []}
                loading={loading}
              />

              <div className="lg:col-span-2">
                <SourcesTable
                  sources={sources}
                  loading={loading}
                />
              </div>

              <div className="lg:col-span-2">
                <AccuracyCalibration
                  accuracy={accuracy}
                  loading={loading}
                />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
