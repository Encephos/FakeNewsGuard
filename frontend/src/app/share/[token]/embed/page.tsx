import { INTERNAL_BACKEND_URL } from "@/config";

interface EmbedData {
  token: string;
  title: string | null;
  overall_rating: string | null;
  confidence: number | null;
  summary: string | null;
  claims_count: number | null;
  source_url: string | null;
  share_url: string;
}

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

async function fetchEmbed(token: string): Promise<EmbedData | null | "forbidden"> {
  try {
    const res = await fetch(`${INTERNAL_BACKEND_URL}/api/v1/share/${token}/embed`, {
      cache: "no-store",
    });
    if (res.status === 403) return "forbidden";
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export default async function EmbedPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const data = await fetchEmbed(token);

  if (data === "forbidden") {
    return (
      <div className="flex items-center justify-center h-screen px-4 -mt-16">
        <div className="text-center">
          <p className="text-sm text-text-tertiary font-mono">Einbetten nicht erlaubt.</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center h-screen px-4 -mt-16">
        <div className="text-center">
          <p className="text-sm text-text-tertiary font-mono">Link nicht gefunden oder abgelaufen.</p>
        </div>
      </div>
    );
  }

  const rs = data.overall_rating
    ? (RATING_STYLES[data.overall_rating] ?? { bg: "bg-text-tertiary/10", text: "text-text-tertiary", label: data.overall_rating })
    : null;
  const conf = data.confidence ?? 0;
  const barColor = conf >= 70 ? "bg-success" : conf >= 40 ? "bg-warning" : "bg-error";
  const shareHref = data.share_url;

  return (
    <div className="flex flex-col justify-between px-4 py-4 -mt-16 min-h-screen max-h-[400px] overflow-hidden">
      {/* Logo */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-mono text-text-tertiary uppercase tracking-widest">
          FakeNewsGuard
        </span>
        {rs && (
          <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold font-mono ${rs.bg} ${rs.text}`}>
            {rs.label}
          </span>
        )}
      </div>

      {/* Title */}
      {data.title && (
        <h1 className="text-sm font-bold font-mono text-text-primary leading-snug mb-2 line-clamp-2">
          {data.title}
        </h1>
      )}

      {/* Confidence bar */}
      {data.confidence != null && (
        <div className="flex items-center gap-2 mb-2">
          <div className="flex-1 h-1.5 rounded-full bg-surface-hover overflow-hidden">
            <div
              className={`h-full rounded-full ${barColor}`}
              style={{ width: `${data.confidence}%` }}
            />
          </div>
          <span className="text-[10px] font-mono text-text-secondary w-8 text-right">
            {data.confidence}%
          </span>
        </div>
      )}

      {/* Summary */}
      {data.summary && (
        <p className="text-xs text-text-secondary leading-relaxed line-clamp-3 mb-3">
          {data.summary}
        </p>
      )}

      {/* CTA */}
      <div className="mt-auto">
        <a
          href={shareHref}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs font-mono text-accent hover:underline"
        >
          Mehr erfahren
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
            <polyline points="15 3 21 3 21 9" />
            <line x1="10" y1="14" x2="21" y2="3" />
          </svg>
        </a>
      </div>
    </div>
  );
}
