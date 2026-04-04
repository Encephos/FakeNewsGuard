"use client";

import { useMemo } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { useI18n } from "../../lib/i18n";
import { WidgetContainer } from "./WidgetContainer";
import {
  TimelineBucket,
  shortLabel,
  RATINGS_ORDER,
  RATING_COLORS,
  TOOLTIP_STYLE,
} from "./types";

interface Props {
  buckets: TimelineBucket[];
  loading: boolean;
  onExportCsv?: () => void;
  onExportPng?: () => void;
  chartRef?: React.RefObject<HTMLDivElement | null>;
}

export function RatingDistribution({ buckets, loading, onExportCsv, onExportPng, chartRef }: Props) {
  const { t } = useI18n();

  const areaData = useMemo(
    () =>
      buckets.map((b) => {
        const total = b.count || 1;
        const row: Record<string, number | string> = { date: shortLabel(b.date) };
        for (const r of RATINGS_ORDER) {
          row[r] = Math.round(((b.rating_distribution[r] ?? 0) / total) * 100);
        }
        return row;
      }),
    [buckets],
  );

  return (
    <WidgetContainer
      title={t("analytics.ratingTrend.title")}
      loading={loading}
      onExportCsv={onExportCsv}
      onExportPng={onExportPng}
    >
      <div ref={chartRef}>
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={areaData}>
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
      </div>
    </WidgetContainer>
  );
}
