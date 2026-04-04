"use client";

import { useState } from "react";
import { useI18n } from "../../lib/i18n";
import { Period, DateRange } from "./types";

const PRESET_PERIODS: Period[] = ["7d", "30d", "90d", "all"];

interface Props {
  period: Period;
  onPeriodChange: (p: Period) => void;
  dateRange: DateRange | null;
  onDateRangeChange: (r: DateRange | null) => void;
}

export function PeriodSelector({ period, onPeriodChange, dateRange, onDateRangeChange }: Props) {
  const { t } = useI18n();
  const [validationError, setValidationError] = useState<string | null>(null);

  const today = new Date().toISOString().split("T")[0];

  function handlePreset(p: Period) {
    setValidationError(null);
    onDateRangeChange(null);
    onPeriodChange(p);
  }

  function handleCustom() {
    setValidationError(null);
    onPeriodChange("custom");
    if (!dateRange) {
      const thirtyDaysAgo = new Date(Date.now() - 30 * 86400 * 1000)
        .toISOString()
        .split("T")[0];
      onDateRangeChange({ from: thirtyDaysAgo, to: today });
    }
  }

  function handleDateChange(field: "from" | "to", value: string) {
    const next = { ...(dateRange ?? { from: "", to: today }), [field]: value };
    if (next.from && next.to && next.from > next.to) {
      setValidationError(t("analytics.period.customError"));
    } else {
      setValidationError(null);
    }
    onDateRangeChange(next);
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex items-center gap-1">
        {PRESET_PERIODS.map((p) => (
          <button
            key={p}
            onClick={() => handlePreset(p)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              period === p
                ? "bg-accent text-white"
                : "glass-inner text-text-secondary hover:text-text-primary"
            }`}
          >
            {t(`analytics.period.${p}`)}
          </button>
        ))}
        <button
          onClick={handleCustom}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
            period === "custom"
              ? "bg-accent text-white"
              : "glass-inner text-text-secondary hover:text-text-primary"
          }`}
        >
          {t("analytics.period.custom")}
        </button>
      </div>

      {period === "custom" && (
        <div className="flex items-center gap-2 text-xs">
          <label className="text-text-tertiary">{t("analytics.period.customFrom")}</label>
          <input
            type="date"
            max={today}
            value={dateRange?.from ?? ""}
            onChange={(e) => handleDateChange("from", e.target.value)}
            className="glass-inner px-2 py-1 rounded-lg text-xs text-text-primary bg-transparent border border-border/40 focus:outline-none focus:border-accent"
          />
          <label className="text-text-tertiary">{t("analytics.period.customTo")}</label>
          <input
            type="date"
            max={today}
            value={dateRange?.to ?? ""}
            onChange={(e) => handleDateChange("to", e.target.value)}
            className="glass-inner px-2 py-1 rounded-lg text-xs text-text-primary bg-transparent border border-border/40 focus:outline-none focus:border-accent"
          />
          {validationError && (
            <span className="text-error text-[10px]">{validationError}</span>
          )}
        </div>
      )}
    </div>
  );
}
