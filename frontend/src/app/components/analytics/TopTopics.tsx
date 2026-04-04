"use client";

import { useMemo } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
} from "recharts";
import { useI18n } from "../../lib/i18n";
import { WidgetContainer } from "./WidgetContainer";
import { Topic, TOOLTIP_STYLE } from "./types";

interface Props {
  topics: Topic[];
  loading: boolean;
  onExportCsv?: () => void;
  onExportPng?: () => void;
  chartRef?: React.RefObject<HTMLDivElement | null>;
}

export function TopTopics({ topics, loading, onExportCsv, onExportPng, chartRef }: Props) {
  const { t } = useI18n();

  const chartData = useMemo(
    () =>
      topics
        .slice(0, 10)
        .map((tp) => ({ name: tp.topic, count: tp.count, trend: tp.trend })),
    [topics],
  );

  return (
    <WidgetContainer
      title={t("analytics.topics.title")}
      loading={loading}
      empty={chartData.length === 0}
      onExportCsv={onExportCsv}
      onExportPng={onExportPng}
    >
      <div ref={chartRef}>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={chartData} layout="vertical" margin={{ left: 8, right: 8 }}>
            <XAxis type="number" tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
              width={90}
            />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Bar dataKey="count" radius={[0, 3, 3, 0]} name="Häufigkeit">
              {chartData.map((entry, index) => (
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
      </div>
    </WidgetContainer>
  );
}
