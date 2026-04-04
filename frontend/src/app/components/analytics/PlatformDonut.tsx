"use client";

import { useMemo } from "react";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Tooltip,
} from "recharts";
import { useI18n } from "../../lib/i18n";
import { WidgetContainer } from "./WidgetContainer";
import { Platform, PLATFORM_COLORS, TOOLTIP_STYLE } from "./types";

interface Props {
  platforms: Platform[];
  loading: boolean;
  onExportCsv?: () => void;
  onExportPng?: () => void;
  chartRef?: React.RefObject<HTMLDivElement | null>;
}

export function PlatformDonut({ platforms, loading, onExportCsv, onExportPng, chartRef }: Props) {
  const { t } = useI18n();

  const chartData = useMemo(
    () => platforms.map((p) => ({ name: p.platform, value: p.count })),
    [platforms],
  );

  return (
    <WidgetContainer
      title={t("analytics.platforms.title")}
      loading={loading}
      empty={chartData.length === 0}
      onExportCsv={onExportCsv}
      onExportPng={onExportPng}
    >
      <div ref={chartRef}>
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie
              data={chartData}
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
              {chartData.map((_entry, index) => (
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
      </div>
    </WidgetContainer>
  );
}
