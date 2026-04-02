"use client";

import { Step, AnalysisResult, FactRating, CostSummary } from "../lib/types";

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

const MAIN_PHASES = ["Phase 1", "Phase 2", "Phase 3", "Phase 4"];

export default function RightPanel({ steps, result, isAnalyzing }: RightPanelProps) {
  if (isAnalyzing) {
    // Count phases by checking which main phases have all their steps completed
    const phasesDone = MAIN_PHASES.filter((phaseId) => {
      const phaseSteps = steps.filter((s) => s.phase === phaseId);
      return phaseSteps.length > 0 && phaseSteps.every((s) => s.status === "done");
    }).length;
    const totalPhases = MAIN_PHASES.length;
    const running = steps.findLast?.((s) => s.status === "running");

    return (
      <div className="px-4 py-5">
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Fortschritt
        </h4>
        <p className="text-sm text-text-primary font-medium tabular-nums">
          {phasesDone}<span className="text-text-tertiary font-normal"> / {totalPhases}</span>
        </p>
        <p className="text-xs text-text-tertiary mt-0.5">Phasen</p>
        {running && (
          <p className="text-xs text-text-secondary mt-3 font-mono">{running.agent} · {running.phase}</p>
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

      {/* Impact */}
      {result.cost_summary && <ImpactSection cost={result.cost_summary} />}
    </div>
  );
}

function ImpactSection({ cost }: { cost: CostSummary }) {
  const co2 = cost.estimated_co2_grams;
  // Vergleichswerte: eine Google-Suche ~ 0.2g CO2, eine E-Mail ~ 4g CO2
  const searchEquiv = co2 > 0 ? Math.round(co2 / 0.2) : 0;

  return (
    <>
      <div className="border-t border-[var(--glass-inner-border)]" />

      <section>
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Ressourcen
        </h4>
        <div className="space-y-2">
          <Stat label="Tokens" value={formatTokens(cost.total_tokens)} />
          <Stat label="LLM-Aufrufe" value={String(cost.call_count)} />
          <Stat label="Websuchen" value={String(cost.search_query_count)} />
        </div>
      </section>

      <div className="border-t border-[var(--glass-inner-border)]" />

      <section>
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Fussabdruck
        </h4>
        <div className="space-y-2">
          <Stat label="Kosten" value={`$${cost.estimated_cost_usd.toFixed(4)}`} />
          <Stat label="CO2" value={formatCO2(co2)} />
        </div>
        {searchEquiv > 0 && (
          <p className="text-[10px] text-text-tertiary mt-2 leading-relaxed">
            Entspricht ~{searchEquiv} Google-Suche{searchEquiv !== 1 ? "n" : ""}
          </p>
        )}
      </section>
    </>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatCO2(grams: number): string {
  if (grams >= 1000) return `${(grams / 1000).toFixed(2)} kg`;
  if (grams >= 1) return `${grams.toFixed(2)} g`;
  return `${(grams * 1000).toFixed(1)} mg`;
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-text-secondary">{label}</span>
      <span className="font-mono text-xs text-text-primary font-medium tabular-nums">{value}</span>
    </div>
  );
}
