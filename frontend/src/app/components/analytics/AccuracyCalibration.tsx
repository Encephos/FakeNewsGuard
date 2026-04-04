"use client";

import { useMemo } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Cell,
} from "recharts";
import { useI18n } from "../../lib/i18n";
import { WidgetContainer } from "./WidgetContainer";
import { AccuracyData, pct, TOOLTIP_STYLE } from "./types";

interface Props {
  accuracy: AccuracyData | null;
  loading: boolean;
  onExportCsv?: () => void;
  onExportPng?: () => void;
  chartRef?: React.RefObject<HTMLDivElement | null>;
}

export function AccuracyCalibration({ accuracy, loading, onExportCsv, onExportPng, chartRef }: Props) {
  const { t } = useI18n();

  const bandData = useMemo(
    () =>
      (accuracy?.confidence_bands ?? []).map((b) => ({
        range: b.range,
        actual: b.avg_rating_score,
        count: b.count,
      })),
    [accuracy],
  );

  const isEmpty = !accuracy || accuracy.confidence_bands.every((b) => b.count === 0);

  return (
    <WidgetContainer
      title={t("analytics.accuracy.title")}
      loading={loading}
      empty={isEmpty}
      onExportCsv={onExportCsv}
      onExportPng={onExportPng}
    >
      <div className="flex flex-col sm:flex-row gap-6">
        <div className="flex-1" ref={chartRef}>
          <p className="text-[10px] text-text-tertiary mb-2">
            Konfidenzbereich → Ø Bewertungs-Score (5=Zuverlässig, 0=Falsch)
          </p>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={bandData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.5} />
              <XAxis dataKey="range" tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
              <YAxis domain={[0, 5]} tick={{ fontSize: 10, fill: "var(--text-tertiary)" }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <ReferenceLine y={2.5} stroke="var(--text-tertiary)" strokeDasharray="4 4" strokeOpacity={0.5} />
              <Bar dataKey="actual" name="Ø Rating-Score" radius={[3, 3, 0, 0]}>
                {bandData.map((entry, index) => (
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
        <div className="flex flex-col gap-3 justify-center sm:w-48">
          <div className="glass-inner p-3 rounded-xl">
            <p className="text-[10px] text-text-tertiary mb-1">Brier Score</p>
            <p className="text-xl font-bold text-text-primary">
              {accuracy?.overall_brier_score.toFixed(3) ?? "—"}
            </p>
            <p className="text-[10px] text-text-tertiary">
              {!accuracy
                ? ""
                : accuracy.overall_brier_score < 0.1
                ? "Sehr gut kalibriert"
                : accuracy.overall_brier_score < 0.2
                ? "Gut kalibriert"
                : accuracy.overall_brier_score < 0.33
                ? "Mäßig kalibriert"
                : "Schlecht kalibriert"}
            </p>
          </div>
          {accuracy && accuracy.accuracy_over_time.length > 0 && (
            <div className="glass-inner p-3 rounded-xl">
              <p className="text-[10px] text-text-tertiary mb-1">Ø Konfidenz (aktuell)</p>
              <p className="text-xl font-bold text-text-primary">
                {pct(
                  accuracy.accuracy_over_time[
                    accuracy.accuracy_over_time.length - 1
                  ]?.avg_confidence ?? 0,
                )}
              </p>
            </div>
          )}
        </div>
      </div>
    </WidgetContainer>
  );
}
