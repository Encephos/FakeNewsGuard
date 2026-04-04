"use client";

import { useState, useRef, useEffect } from "react";
import { useI18n } from "../../lib/i18n";
import { WidgetId, DEFAULT_WIDGET_ORDER } from "./types";

interface Props {
  visible: Record<WidgetId, boolean>;
  onToggle: (id: WidgetId) => void;
}

const WIDGET_I18N_KEY: Record<WidgetId, string> = {
  timeline: "analytics.widget.timeline",
  ratingDist: "analytics.widget.ratingDist",
  topics: "analytics.widget.topics",
  platforms: "analytics.widget.platforms",
  sources: "analytics.widget.sources",
  accuracy: "analytics.widget.accuracy",
};

export function WidgetVisibilityToggle({ visible, onToggle }: Props) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const visibleCount = Object.values(visible).filter(Boolean).length;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={t("analytics.layout.settings")}
        className="glass-inner p-1.5 rounded-lg hover:bg-surface-hover transition-all text-text-tertiary hover:text-text-primary"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 glass-card p-3 min-w-[180px] shadow-lg">
          <p className="text-[10px] text-text-tertiary font-medium uppercase tracking-wider mb-2">
            {t("analytics.layout.showWidget")}
          </p>
          {DEFAULT_WIDGET_ORDER.map((id) => {
            const isLast = visibleCount === 1 && visible[id];
            return (
              <label
                key={id}
                className="flex items-center gap-2 py-1 cursor-pointer text-xs text-text-secondary hover:text-text-primary"
              >
                <input
                  type="checkbox"
                  checked={visible[id]}
                  disabled={isLast}
                  onChange={() => onToggle(id)}
                  className="accent-accent rounded"
                />
                {t(WIDGET_I18N_KEY[id])}
              </label>
            );
          })}
          <p className="text-[10px] text-text-tertiary mt-2">
            {t("analytics.layout.dragHint")}
          </p>
        </div>
      )}
    </div>
  );
}
