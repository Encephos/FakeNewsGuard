"use client";

import { useI18n } from "../../lib/i18n";
import { Skeleton } from "./Skeleton";
import { pct } from "./types";

interface Kpis {
  total: number;
  avgConf: number;
  topRating: string;
  trendSign: string;
}

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

export function KPICards({ kpis, loading }: { kpis: Kpis; loading: boolean }) {
  const { t } = useI18n();

  if (loading) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
      <KpiCard label={t("analytics.kpi.total")} value={kpis.total.toLocaleString()} />
      <KpiCard label={t("analytics.kpi.avgConfidence")} value={pct(kpis.avgConf)} />
      <KpiCard
        label={t("analytics.kpi.topRating")}
        value={kpis.topRating === "—" ? "—" : kpis.topRating.replace(/_/g, " ")}
      />
      <KpiCard label={t("analytics.kpi.trend")} value={kpis.trendSign} />
    </div>
  );
}
