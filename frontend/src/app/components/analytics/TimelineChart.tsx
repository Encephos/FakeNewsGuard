"use client";

import { useMemo } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import { useI18n } from "../../lib/i18n";
import { WidgetContainer } from "./WidgetContainer";
import { TimelineBucket, shortLabel, TOOLTIP_STYLE } from "./types";

interface Props {
  buckets: TimelineBucket[];
  loading: boolean;
  onExportCsv?: () => void;
  onExportPng?: () => void;
  chartRef?: React.RefObject<HTMLDivElement | null>;
}

export function TimelineChart({ buckets, loading, onExportCsv, onExportPng, chartRef }: Props) {
  const { t } = useI18n();

  const chartData = useMemo(
    () =>
      buckets.map((b) => ({
        date: shortLabel(b.date),
        count: b.count,
        conf: Math.round(b.avg_confidence * 100),
        claims: b.avg_claims_per_analysis,
      })),
    [buckets],
  );

  return (
    <WidgetContainer
      title={t("analytics.timeline.title")}
      loading={loading}
      onExportCsv={onExportCsv}
      onExportPng={onExportPng}
    >
      <div ref={chartRef}>
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={chartData}>
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
      </div>
    </WidgetContainer>
  );
}
