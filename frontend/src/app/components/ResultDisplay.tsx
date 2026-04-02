"use client";

import { useState } from "react";
import { AnalysisResult, ClaimResult, CostSummary, FactRating, RhetoricTechnique } from "../lib/types";

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

interface ResultDisplayProps {
  result: AnalysisResult;
  archiveId?: string;
  sourceUrl?: string;
}

export default function ResultDisplay({ result, archiveId, sourceUrl }: ResultDisplayProps) {
  const rs = OVERALL_STYLE[result.overall_rating] ?? OVERALL_STYLE["Irreführend"];
  const [pdfLoading, setPdfLoading] = useState(false);

  const handlePdfExport = async () => {
    setPdfLoading(true);
    try {
      if (archiveId) {
        // Archive-based export — simple GET, browser handles download
        window.open(`/api/export/pdf/${archiveId}`, "_blank");
      } else {
        // Direct result export via POST
        const res = await fetch("/api/export/pdf", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            result,
            title: result.summary?.slice(0, 80) || "Faktencheck-Report",
            source_url: sourceUrl || "",
          }),
        });
        if (!res.ok) throw new Error("PDF-Export fehlgeschlagen");
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "faktencheck_report.pdf";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.error("PDF export error:", err);
    } finally {
      setPdfLoading(false);
    }
  };

  return (
    <div className="w-full space-y-3 animate-fade-in">

      {/* Verdict card */}
      <Card>
        <div className="flex items-start justify-between gap-4 mb-3">
          <span className={`font-mono text-xl font-bold leading-tight ${rs.color}`}>
            {rs.label}
          </span>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handlePdfExport}
              disabled={pdfLoading}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono text-text-tertiary border border-[var(--glass-inner-border)] rounded-lg hover:bg-text-tertiary/10 hover:text-text-secondary transition-all disabled:opacity-40 disabled:cursor-wait"
              title="Als PDF exportieren"
            >
              {pdfLoading ? (
                <svg className="animate-spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" strokeDasharray="32" strokeDashoffset="12" />
                </svg>
              ) : (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              )}
              PDF
            </button>
            <span className="glass-badge px-2.5 py-0.5 font-mono text-[11px] text-text-tertiary">
              {result.confidence}% Konfidenz
            </span>
          </div>
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

      {/* Impact footer */}
      {result.cost_summary && <ImpactFooter cost={result.cost_summary} />}
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

function ImpactFooter({ cost }: { cost: CostSummary }) {
  const co2 = cost.estimated_co2_grams;
  const agents = Object.entries(cost.tokens_per_agent);
  const models = Object.entries(cost.co2_per_model);

  return (
    <div className="glass-card px-5 py-4">
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3.5">
        Analyse-Fussabdruck
      </h3>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
        <MiniStat label="Tokens gesamt" value={formatTokens(cost.total_tokens)} />
        <MiniStat label="LLM-Aufrufe" value={String(cost.call_count)} />
        <MiniStat label="Kosten" value={`$${cost.estimated_cost_usd.toFixed(4)}`} />
        <MiniStat label="CO2" value={formatCO2(co2)} />
      </div>

      {/* Agent token breakdown bar */}
      {agents.length > 1 && cost.total_tokens > 0 && (
        <div className="mb-3">
          <p className="text-[10px] text-text-tertiary mb-1.5">Token-Verteilung nach Agent</p>
          <div className="flex h-2 rounded-full overflow-hidden gap-px">
            {agents.map(([agent, tokens]) => (
              <div
                key={agent}
                className="h-full bg-accent/60 first:rounded-l-full last:rounded-r-full"
                style={{ flex: tokens / cost.total_tokens }}
                title={`${agent}: ${formatTokens(tokens)}`}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5">
            {agents.map(([agent, tokens]) => (
              <span key={agent} className="text-[10px] text-text-tertiary font-mono">
                {agent} {formatTokens(tokens)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* CO2 context */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-text-tertiary">
        {models.map(([model, grams]) => (
          <span key={model} className="font-mono">
            {model.split("/").pop()}: {formatCO2(grams)}
          </span>
        ))}
        {cost.search_query_count > 0 && (
          <span className="font-mono">
            {cost.search_query_count} Websuche{cost.search_query_count !== 1 ? "n" : ""}: {formatCO2(cost.search_co2_grams)}
          </span>
        )}
      </div>

      <p className="text-[10px] text-text-tertiary mt-2 leading-relaxed opacity-70">
        Schaetzwerte basierend auf TokenPowerBench-Skalierung, globalem Durchschnitts-Strommix (~475 gCO2/kWh)
        und ~0.2 g CO2 pro Websuche.
      </p>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-mono text-sm text-text-primary font-medium tabular-nums">{value}</p>
      <p className="text-[10px] text-text-tertiary">{label}</p>
    </div>
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
