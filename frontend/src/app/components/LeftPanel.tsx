"use client";

import { Step, AnalysisResult, FactRating } from "../lib/types";

const PHASES = [
  { id: "Phase 1", label: "Extraktion" },
  { id: "Phase 2", label: "Fact-Check" },
  { id: "Phase 3", label: "Rhetorik" },
  { id: "Phase 4", label: "Synthese" },
];

const CLAIM_DOT: Record<FactRating, string> = {
  TRUE:         "bg-success",
  MOSTLY_TRUE:  "bg-success",
  MISLEADING:   "bg-warning",
  UNVERIFIABLE: "bg-text-tertiary",
  MOSTLY_FALSE: "bg-error",
  FALSE:        "bg-error",
};

const CLAIM_LABEL: Record<FactRating, string> = {
  TRUE:         "Wahr",
  MOSTLY_TRUE:  "Größtenteils w.",
  MISLEADING:   "Irreführend",
  UNVERIFIABLE: "Unverif.",
  MOSTLY_FALSE: "Größtenteils f.",
  FALSE:        "Falsch",
};

function getPhaseStatus(phase: string, steps: Step[]): "pending" | "running" | "done" {
  const ps = steps.filter((s) => s.phase === phase);
  if (ps.length === 0) return "pending";
  if (ps.some((s) => s.status === "running")) return "running";
  return "done";
}

interface LeftPanelProps {
  steps: Step[];
  result?: AnalysisResult;
  isAnalyzing: boolean;
}

export default function LeftPanel({ steps, result, isAnalyzing }: LeftPanelProps) {
  return (
    <div className="px-4 py-5 space-y-6">
      {/* Phase tracker */}
      <section>
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Phasen
        </h4>
        <div className="space-y-2">
          {PHASES.map(({ id, label }) => {
            const status = getPhaseStatus(id, steps);
            return (
              <div key={id} className="flex items-center gap-2.5">
                <span
                  className={`shrink-0 text-xs font-mono ${
                    status === "done"    ? "text-success"
                    : status === "running" ? "text-accent animate-blink"
                    : "text-border"
                  }`}
                >
                  {status === "done" ? "✓" : status === "running" ? "●" : "○"}
                </span>
                <span className={`text-xs ${status === "pending" ? "text-text-tertiary" : "text-text-primary"}`}>
                  {label}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      {/* Claim index */}
      {!isAnalyzing && result && result.claims.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
            {result.claims.length} Claims
          </h4>
          <div className="space-y-3">
            {result.claims.map((claim) => (
              <div key={claim.id} className="flex items-start gap-2">
                <span className={`mt-1.5 shrink-0 h-1.5 w-1.5 rounded-full ${CLAIM_DOT[claim.rating]}`} />
                <div className="min-w-0">
                  <div className="flex items-baseline gap-1.5 flex-wrap">
                    <span className="font-mono text-[11px] text-text-tertiary">{claim.id}</span>
                    <span className="text-[11px] text-text-secondary">{CLAIM_LABEL[claim.rating]}</span>
                  </div>
                  <p className="text-[11px] text-text-tertiary leading-snug line-clamp-2 mt-0.5">
                    {claim.text}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
