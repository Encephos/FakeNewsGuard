"use client";

import { useI18n } from "../../lib/i18n";
import { Skeleton } from "./Skeleton";

interface WidgetContainerProps {
  title: string;
  loading: boolean;
  empty?: boolean;
  children: React.ReactNode;
  onExportCsv?: () => void;
  onExportPng?: () => void;
}

export function WidgetContainer({
  title,
  loading,
  empty,
  children,
  onExportCsv,
  onExportPng,
}: WidgetContainerProps) {
  const { t } = useI18n();
  const hasExport = onExportCsv || onExportPng;

  return (
    <div className="glass-card p-5">
      <div className="flex items-center mb-4">
        <h2 className="text-sm font-semibold text-text-secondary">{title}</h2>
        {hasExport && (
          <div className="flex items-center gap-1 ml-auto">
            {onExportCsv && (
              <button
                onClick={onExportCsv}
                title={t("analytics.export.csv")}
                className="glass-inner p-1.5 rounded-lg hover:bg-surface-hover transition-all text-text-tertiary hover:text-text-primary"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              </button>
            )}
            {onExportPng && (
              <button
                onClick={onExportPng}
                title={t("analytics.export.png")}
                className="glass-inner p-1.5 rounded-lg hover:bg-surface-hover transition-all text-text-tertiary hover:text-text-primary"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                  <circle cx="8.5" cy="8.5" r="1.5" />
                  <polyline points="21 15 16 10 5 21" />
                </svg>
              </button>
            )}
          </div>
        )}
      </div>
      {loading ? (
        <Skeleton className="h-52" />
      ) : empty ? (
        <p className="text-xs text-text-tertiary">{t("analytics.empty")}</p>
      ) : (
        children
      )}
    </div>
  );
}
