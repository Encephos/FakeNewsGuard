"use client";

import { Step, AnalysisResult, FactRating } from "../lib/types";

const OVERALL_COLOR: Record<string, string> = {
  Wahr:                  "text-success",
  "Größtenteils wahr":   "text-success",
  Irreführend:           "text-warning",
  "Größtenteils falsch": "text-error",
  Falsch:                "text-error",
};

const RATING_GROUPS: { label: string; ratings: FactRating[]; dot: string }[] = [
  { label: "Wahr",        ratings: ["TRUE", "MOSTLY_TRUE"],   dot: "bg-success" },
  { label: "Irreführend", ratings: ["MISLEADING"],            dot: "bg-warning" },
  { label: "Unverif.",    ratings: ["UNVERIFIABLE"],          dot: "bg-text-tertiary" },
  { label: "Falsch",      ratings: ["MOSTLY_FALSE", "FALSE"], dot: "bg-error" },
];

interface RightPanelProps {
  steps: Step[];
  result?: AnalysisResult;
  isAnalyzing: boolean;
}

export default function RightPanel({ steps, result, isAnalyzing }: RightPanelProps) {
  if (isAnalyzing) {
    const done = steps.filter((s) => s.status === "done").length;
    const total = steps.length;
    const running = steps.findLast?.((s) => s.status === "running");

    return (
      <div className="px-4 py-5">
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Fortschritt
        </h4>
        <p className="text-sm text-text-primary font-medium tabular-nums">
          {done}<span className="text-text-tertiary font-normal"> / {total || "—"}</span>
        </p>
        <p className="text-xs text-text-tertiary mt-0.5">Schritte</p>
        {running && (
          <p className="text-xs text-text-secondary mt-3 font-mono">{running.phase}</p>
        )}
      </div>
    );
  }

  if (!result) return null;

  const ratingColor = OVERALL_COLOR[result.overall_rating] ?? "text-warning";

  return (
    <div className="px-4 py-5 space-y-5">
      {/* Verdict */}
      <section>
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
          Urteil
        </h4>
        <p className={`text-sm font-bold leading-snug font-mono ${ratingColor}`}>
          {result.overall_rating}
        </p>
        <p className="text-xs text-text-tertiary mt-0.5">
          {result.confidence}% Konfidenz
        </p>
      </section>

      <div className="border-t border-[var(--glass-inner-border)]" />

      {/* Stats */}
      <section>
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Überblick
        </h4>
        <div className="space-y-2">
          <Stat label="Claims"      value={result.claims.length} />
          <Stat label="Techniken"   value={result.rhetoric.length} />
          <Stat label="Korrekturen" value={result.corrections.length} />
          <Stat label="Quellen"     value={result.sources.length} />
        </div>
      </section>

      <div className="border-t border-[var(--glass-inner-border)]" />

      {/* Distribution */}
      <section>
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Verteilung
        </h4>
        <div className="space-y-2">
          {RATING_GROUPS.map(({ label, ratings, dot }) => {
            const n = result.claims.filter((c) => ratings.includes(c.rating)).length;
            if (n === 0) return null;
            return (
              <div key={label} className="flex items-center gap-2">
                <span className={`shrink-0 h-1.5 w-1.5 rounded-full ${dot}`} />
                <span className="text-xs text-text-secondary flex-1">{label}</span>
                <span className="font-mono text-xs text-text-primary font-medium tabular-nums">{n}</span>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-text-secondary">{label}</span>
      <span className="font-mono text-xs text-text-primary font-medium tabular-nums">{value}</span>
    </div>
  );
}
