"use client";

import { useI18n } from "../../lib/i18n";
import { WidgetContainer } from "./WidgetContainer";
import { SourcesData } from "./types";

interface Props {
  sources: SourcesData | null;
  loading: boolean;
  onExportCsv?: () => void;
}

export function SourcesTable({ sources, loading, onExportCsv }: Props) {
  const { t } = useI18n();

  return (
    <WidgetContainer
      title={t("analytics.sources.title")}
      loading={loading}
      empty={!sources || sources.sources.length === 0}
      onExportCsv={onExportCsv}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-tertiary border-b border-border/40">
              <th className="text-left py-2 pr-4 font-medium">{t("analytics.sources.domain")}</th>
              <th className="text-right py-2 pr-4 font-medium">{t("analytics.sources.citations")}</th>
              <th className="text-right py-2 pr-4 font-medium hidden sm:table-cell">{t("analytics.sources.firstSeen")}</th>
              <th className="text-right py-2 font-medium hidden sm:table-cell">{t("analytics.sources.lastSeen")}</th>
            </tr>
          </thead>
          <tbody>
            {sources?.sources.slice(0, 15).map((src, i) => (
              <tr
                key={i}
                className="border-b border-border/20 hover:bg-surface-hover/30 transition-colors"
              >
                <td className="py-2 pr-4 text-text-primary font-medium">{src.domain}</td>
                <td className="py-2 pr-4 text-right text-text-secondary">{src.citation_count}</td>
                <td className="py-2 pr-4 text-right text-text-tertiary hidden sm:table-cell">{src.first_seen}</td>
                <td className="py-2 text-right text-text-tertiary hidden sm:table-cell">{src.last_seen}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {sources && sources.total_unique_sources > 15 && (
          <p className="text-[10px] text-text-tertiary mt-2">
            +{sources.total_unique_sources - 15} weitere Quellen
          </p>
        )}
      </div>
    </WidgetContainer>
  );
}
