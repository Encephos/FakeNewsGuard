"use client";

import { useState } from "react";
import Link from "next/link";

// ── Types ──────────────────────────────────────────────────────────────────

interface ShareData {
  token: string;
  title: string | null;
  overall_rating: string | null;
  confidence: number | null;
  summary: string | null;
  claims_count: number | null;
  techniques_count: number | null;
  source_url: string | null;
  platform: string | null;
  created_at: number | null;
  allow_embed: boolean;
  view_count: number;
  claims?: Array<{
    id: string;
    text: string;
    type: string;
    rating: string;
    evidence: string;
    correction: string;
    missing_context: string;
    sources: string[];
  }>;
  rhetoric?: Array<{
    name: string;
    severity: string;
    description: string;
    example: string;
  }>;
  key_corrections?: string[];
  fairness_notes?: string[];
}

// ── Constants ──────────────────────────────────────────────────────────────

const RATING_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  Wahr:                  { bg: "bg-success/15", text: "text-success", label: "Wahr" },
  "Größtenteils wahr":   { bg: "bg-success/15", text: "text-success", label: "Größtenteils wahr" },
  Irreführend:           { bg: "bg-warning/15", text: "text-warning", label: "Irreführend" },
  "Größtenteils falsch": { bg: "bg-error/15",   text: "text-error",   label: "Größtenteils falsch" },
  Falsch:                { bg: "bg-error/15",   text: "text-error",   label: "Falsch" },
  TRUE:                  { bg: "bg-success/15", text: "text-success", label: "Wahr" },
  MOSTLY_TRUE:           { bg: "bg-success/15", text: "text-success", label: "Größtenteils wahr" },
  MISLEADING:            { bg: "bg-warning/15", text: "text-warning", label: "Irreführend" },
  MOSTLY_FALSE:          { bg: "bg-error/15",   text: "text-error",   label: "Größtenteils falsch" },
  FALSE:                 { bg: "bg-error/15",   text: "text-error",   label: "Falsch" },
  UNVERIFIABLE:          { bg: "bg-text-tertiary/10", text: "text-text-tertiary", label: "Nicht prüfbar" },
};

const FACT_RATING_LABEL: Record<string, string> = {
  TRUE: "Wahr",
  MOSTLY_TRUE: "Größtenteils wahr",
  MISLEADING: "Irreführend",
  MOSTLY_FALSE: "Größtenteils falsch",
  FALSE: "Falsch",
  UNVERIFIABLE: "Nicht prüfbar",
};

const SEVERITY_STYLES: Record<string, string> = {
  LOW:    "text-text-tertiary",
  MEDIUM: "text-warning",
  HIGH:   "text-error",
};

// ── Sub-Components ─────────────────────────────────────────────────────────

function RatingBadge({ rating, large }: { rating: string; large?: boolean }) {
  const rs = RATING_STYLES[rating] ?? { bg: "bg-text-tertiary/10", text: "text-text-tertiary", label: rating };
  return (
    <span
      className={`inline-flex items-center px-2.5 py-1 rounded-lg font-bold font-mono ${rs.bg} ${rs.text} ${large ? "text-sm" : "text-[11px]"}`}
    >
      {rs.label}
    </span>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 70 ? "bg-success" : value >= 40 ? "bg-warning" : "bg-error";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-surface-hover overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs font-mono text-text-secondary w-8 text-right">{value}%</span>
    </div>
  );
}

function ShareButtons({ token }: { token: string }) {
  const [copied, setCopied] = useState(false);
  const shareUrl = typeof window !== "undefined" ? `${window.location.origin}/share/${token}` : `/share/${token}`;

  const handleCopy = () => {
    navigator.clipboard.writeText(shareUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleNativeShare = () => {
    if (navigator.share) {
      navigator.share({ url: shareUrl }).catch(() => {});
    }
  };

  const twitterUrl = `https://twitter.com/intent/tweet?url=${encodeURIComponent(shareUrl)}&text=${encodeURIComponent("Faktencheck via FakeNewsGuard")}`;
  const waUrl = `https://wa.me/?text=${encodeURIComponent(`Faktencheck via FakeNewsGuard: ${shareUrl}`)}`;

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <button
        onClick={handleCopy}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono glass-inner text-text-secondary hover:text-text-primary transition-colors"
      >
        {copied ? (
          <>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12" /></svg>
            Kopiert
          </>
        ) : (
          <>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
            Link kopieren
          </>
        )}
      </button>
      <a
        href={twitterUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono glass-inner text-text-secondary hover:text-text-primary transition-colors"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.746l7.73-8.835L1.254 2.25H8.08l4.253 5.622 5.91-5.622zm-1.161 17.52h1.833L7.084 4.126H5.117z" /></svg>
        X / Twitter
      </a>
      <a
        href={waUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono glass-inner text-text-secondary hover:text-text-primary transition-colors"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.570-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z" /><path d="M20.526 3.449C18.242 1.17 15.232 0 12.044 0 5.447 0 .062 5.385.062 11.982c0 2.109.55 4.168 1.594 5.985L0 24l6.197-1.624a11.997 11.997 0 0 0 5.847 1.49c6.597 0 11.982-5.385 11.982-11.982 0-3.2-1.246-6.212-3.5-8.435z" /></svg>
        WhatsApp
      </a>
      {typeof navigator !== "undefined" && "share" in navigator && (
        <button
          onClick={handleNativeShare}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono glass-inner text-text-secondary hover:text-text-primary transition-colors"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><line x1="8.59" y1="13.51" x2="15.42" y2="17.49" /><line x1="15.41" y1="6.51" x2="8.59" y2="10.49" /></svg>
          Teilen
        </button>
      )}
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────

export default function SharePageClient({ data, token }: { data: ShareData; token: string }) {
  return (
    <div className="min-h-[calc(100vh-64px)] px-4 py-8 max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-text-tertiary uppercase tracking-widest">Faktencheck</span>
        </div>
        <span className="text-[10px] text-text-tertiary font-mono">
          {data.view_count} Aufrufe
        </span>
      </div>

      {/* Main card */}
      <div className="glass-card px-5 py-5 mb-4">
        {/* Rating + Confidence */}
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            {data.overall_rating && (
              <RatingBadge rating={data.overall_rating} large />
            )}
          </div>
          {data.confidence != null && (
            <div className="w-36">
              <div className="text-[10px] text-text-tertiary mb-1.5 text-right">Konfidenz</div>
              <ConfidenceBar value={data.confidence} />
            </div>
          )}
        </div>

        {/* Title */}
        {data.title && (
          <h1 className="text-base font-bold font-mono text-text-primary mb-2 leading-snug">
            {data.title}
          </h1>
        )}

        {/* Source URL */}
        {data.source_url && (
          <a
            href={data.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] font-mono text-accent hover:underline break-all block mb-2"
          >
            {data.source_url.replace(/^https?:\/\/(?:www\.)?/, "").split("/")[0]}
          </a>
        )}

        {/* Summary */}
        {data.summary && (
          <p className="text-sm text-text-secondary leading-relaxed mt-3">
            {data.summary}
          </p>
        )}

        {/* Stats */}
        <div className="flex items-center gap-4 mt-3 pt-3 border-t border-border/30">
          {data.claims_count != null && (
            <span className="text-[10px] text-text-tertiary">
              {data.claims_count} Claim{data.claims_count !== 1 ? "s" : ""}
            </span>
          )}
          {data.techniques_count != null && data.techniques_count > 0 && (
            <span className="text-[10px] text-text-tertiary">
              {data.techniques_count} Manipulationstechnik{data.techniques_count !== 1 ? "en" : ""}
            </span>
          )}
          {data.created_at != null && (
            <span className="text-[10px] text-text-tertiary">
              {new Date(data.created_at * 1000).toLocaleDateString("de-DE", {
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
              })}
            </span>
          )}
        </div>
      </div>

      {/* Claims */}
      {data.claims && data.claims.length > 0 && (
        <div className="glass-card px-5 py-4 mb-4">
          <h2 className="text-xs font-bold font-mono uppercase tracking-wider text-text-tertiary mb-3">
            Behauptungen ({data.claims.length})
          </h2>
          <div className="space-y-3">
            {data.claims.map((claim, i) => {
              const rs = RATING_STYLES[claim.rating] ?? { bg: "bg-text-tertiary/10", text: "text-text-tertiary", label: claim.rating };
              return (
                <div key={claim.id || i} className="glass-inner rounded-xl px-4 py-3">
                  <div className="flex items-start gap-2 mb-2">
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold font-mono shrink-0 mt-0.5 ${rs.bg} ${rs.text}`}>
                      {FACT_RATING_LABEL[claim.rating] ?? claim.rating}
                    </span>
                    <p className="text-xs text-text-primary leading-relaxed">{claim.text}</p>
                  </div>
                  {claim.evidence && (
                    <p className="text-[11px] text-text-secondary leading-relaxed">{claim.evidence}</p>
                  )}
                  {claim.correction && (
                    <p className="text-[11px] text-warning leading-relaxed mt-1">
                      Korrektur: {claim.correction}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Manipulation techniques */}
      {data.rhetoric && data.rhetoric.length > 0 && (
        <div className="glass-card px-5 py-4 mb-4">
          <h2 className="text-xs font-bold font-mono uppercase tracking-wider text-text-tertiary mb-3">
            Manipulationstechniken
          </h2>
          <div className="space-y-2">
            {data.rhetoric.map((t, i) => (
              <div key={i} className="glass-inner rounded-xl px-4 py-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-[11px] font-bold font-mono ${SEVERITY_STYLES[t.severity] ?? "text-text-secondary"}`}>
                    {t.name}
                  </span>
                  <span className="text-[9px] font-mono text-text-tertiary uppercase">{t.severity}</span>
                </div>
                {t.description && (
                  <p className="text-[11px] text-text-secondary leading-relaxed">{t.description}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Share buttons */}
      <div className="glass-card px-5 py-4 mb-4">
        <h2 className="text-xs font-bold font-mono uppercase tracking-wider text-text-tertiary mb-3">
          Teilen
        </h2>
        <ShareButtons token={token} />
      </div>

      {/* Embed code */}
      {data.allow_embed && (
        <div className="glass-card px-5 py-4 mb-4">
          <h2 className="text-xs font-bold font-mono uppercase tracking-wider text-text-tertiary mb-3">
            Embed-Code
          </h2>
          <code className="block text-[11px] font-mono text-text-secondary bg-surface-hover rounded-lg px-3 py-2 break-all">
            {`<iframe src="${typeof window !== "undefined" ? window.location.origin : ""}/share/${token}/embed" width="100%" height="400" frameborder="0" style="border:none;border-radius:8px;"></iframe>`}
          </code>
        </div>
      )}

      {/* Footer */}
      <div className="text-center py-4">
        <Link href="/" className="text-xs text-text-tertiary hover:text-text-secondary transition-colors">
          Vollständige Analyse auf FakeNewsGuard
        </Link>
      </div>
    </div>
  );
}
