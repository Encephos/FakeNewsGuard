"use client";

import { AnalysisResult, ClaimResult, FactRating, RhetoricTechnique } from "../lib/types";

const OVERALL_STYLE: Record<string, { color: string; label: string }> = {
  Wahr:                  { color: "text-success",  label: "Wahr" },
  "Größtenteils wahr":   { color: "text-success",  label: "Größtenteils wahr" },
  Irreführend:           { color: "text-warning",  label: "Irreführend" },
  "Größtenteils falsch": { color: "text-error",    label: "Größtenteils falsch" },
  Falsch:                { color: "text-error",    label: "Falsch" },
};

const CLAIM_STYLE: Record<FactRating, { color: string; border: string; label: string }> = {
  TRUE:         { color: "text-success", border: "border-success/30", label: "Wahr" },
  MOSTLY_TRUE:  { color: "text-success", border: "border-success/30", label: "Größtenteils wahr" },
  MISLEADING:   { color: "text-warning", border: "border-warning/30", label: "Irreführend" },
  MOSTLY_FALSE: { color: "text-error",   border: "border-error/30",   label: "Größtenteils falsch" },
  FALSE:        { color: "text-error",   border: "border-error/30",   label: "Falsch" },
  UNVERIFIABLE: { color: "text-text-tertiary", border: "border-[var(--glass-inner-border)]", label: "Unverif." },
};

const SEVERITY_STYLE: Record<string, { text: string; bg: string }> = {
  LOW:    { text: "text-success",  bg: "bg-success/10" },
  MEDIUM: { text: "text-warning",  bg: "bg-warning/10" },
  HIGH:   { text: "text-error",    bg: "bg-error/10" },
};

export default function ResultDisplay({ result }: { result: AnalysisResult }) {
  const rs = OVERALL_STYLE[result.overall_rating] ?? OVERALL_STYLE["Irreführend"];

  return (
    <div className="w-full space-y-3 animate-fade-in">

      {/* Verdict card */}
      <Card>
        <div className="flex items-start justify-between gap-4 mb-3">
          <span className={`font-mono text-xl font-bold leading-tight ${rs.color}`}>
            {rs.label}
          </span>
          <span className="glass-badge px-2.5 py-0.5 font-mono text-[11px] text-text-tertiary shrink-0">
            {result.confidence}% Konfidenz
          </span>
        </div>
        <p className="text-sm leading-relaxed text-text-secondary">{result.summary}</p>
      </Card>

      {/* Claims */}
      <Card title="Claims im Detail">
        <div className="space-y-2.5">
          {result.claims.map((c) => <ClaimCard key={c.id} claim={c} />)}
        </div>
      </Card>

      {/* Corrections */}
      {result.corrections.length > 0 && (
        <Card title="Korrekturen">
          <ol className="space-y-2.5">
            {result.corrections.map((c, i) => (
              <li key={i} className="flex gap-3 text-sm text-text-secondary leading-relaxed">
                <span className="font-mono text-xs text-text-tertiary shrink-0 mt-0.5">{i + 1}.</span>
                {c}
              </li>
            ))}
          </ol>
        </Card>
      )}

      {/* Rhetoric */}
      {result.rhetoric.length > 0 && (
        <Card title="Manipulationstechniken">
          <div className="space-y-3">
            {result.rhetoric.map((r, i) => <RhetoricCard key={i} technique={r} />)}
          </div>
        </Card>
      )}

      {/* Fairness */}
      {result.fairness.length > 0 && (
        <Card title="Was stimmt">
          <ul className="space-y-2">
            {result.fairness.map((f, i) => (
              <li key={i} className="flex gap-2.5 text-sm text-text-secondary">
                <span className="text-success shrink-0 font-medium">+</span>
                {f}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Sources */}
      {result.sources.length > 0 && (
        <Card title="Quellen">
          <ul className="space-y-1.5">
            {result.sources.map((s, i) => (
              <li key={i}>
                <a
                  href={s}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs font-mono text-text-secondary hover:text-accent transition-colors break-all"
                >
                  {s}
                </a>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Card({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <div className="glass-card px-5 py-4">
      {title && (
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3.5">
          {title}
        </h3>
      )}
      {children}
    </div>
  );
}

function ClaimCard({ claim }: { claim: ClaimResult }) {
  const s = CLAIM_STYLE[claim.rating];
  return (
    <div className={`glass-inner border-l-2 ${s.border} pl-3.5 pr-3 py-3`}>
      <div className="flex items-start justify-between gap-3 mb-1.5">
        <div className="flex items-start gap-2 min-w-0">
          <span className="font-mono text-[11px] text-text-tertiary shrink-0 mt-0.5">{claim.id}</span>
          <p className="text-sm text-text-primary leading-snug">{claim.text}</p>
        </div>
        <span className={`glass-badge px-2 py-0.5 font-mono text-[10px] font-semibold shrink-0 ${s.color}`}>
          {s.label}
        </span>
      </div>

      <div className="pl-5 space-y-1">
        {claim.evidence    && <Detail label="Evidenz"       text={claim.evidence} />}
        {claim.correction  && <Detail label="Korrektur"     text={claim.correction} />}
        {claim.missing_context && <Detail label="Kontext fehlt" text={claim.missing_context} />}
      </div>

      {claim.number_audit && claim.number_audit.manipulation !== "NONE" && (
        <div className="mt-2 ml-5 glass-inner border-warning/20 px-3 py-2">
          <p className="text-xs font-semibold text-warning mb-0.5">
            Zahlenmanipulation: {claim.number_audit.manipulation.replace(/_/g, " ")}
          </p>
          <p className="text-xs text-text-secondary">{claim.number_audit.calculation}</p>
        </div>
      )}
    </div>
  );
}

function RhetoricCard({ technique }: { technique: RhetoricTechnique }) {
  const s = SEVERITY_STYLE[technique.severity] ?? SEVERITY_STYLE.MEDIUM;
  return (
    <div className="flex gap-3">
      <div className="shrink-0 w-[58px] mt-0.5">
        <span className={`glass-badge h-5 px-2 text-[10px] font-bold inline-flex items-center ${s.text} ${s.bg}`}>
          {technique.severity}
        </span>
      </div>
      <div>
        <p className="text-sm font-semibold text-text-primary mb-0.5">{technique.name}</p>
        <p className="text-sm text-text-secondary leading-relaxed">{technique.description}</p>
        {technique.example && (
          <p className="text-xs text-text-tertiary mt-1 italic">&ldquo;{technique.example}&rdquo;</p>
        )}
      </div>
    </div>
  );
}

function Detail({ label, text }: { label: string; text: string }) {
  return (
    <div className="text-xs leading-relaxed text-text-secondary">
      <span className="font-semibold text-text-tertiary">{label}: </span>
      {text}
    </div>
  );
}
