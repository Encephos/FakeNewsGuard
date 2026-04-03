"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { AnalysisResult } from "../lib/types";
import ResultDisplay from "../components/ResultDisplay";

// ── Types ───────────────────────────────────────────────────────

interface ArchiveItem {
  id: string;
  created_at: number;
  input_text: string;
  source_url: string | null;
  platform: string | null;
  overall_rating: string;
  confidence: number;
  summary: string;
  title: string | null;
  claims_count: number;
  techniques_count: number;
}

interface ArchiveList {
  items: ArchiveItem[];
  total: number;
  limit: number;
  offset: number;
}

interface ArchiveDetail extends ArchiveItem {
  result: AnalysisResult;
}

interface ArchiveStats {
  enabled: boolean;
  total_entries: number;
  rating_distribution: Record<string, number>;
  average_confidence: number;
  max_entries: number;
}

// ── Helpers ─────────────────────────────────────────────────────

const RATING_STYLES: Record<string, { bg: string; text: string }> = {
  // Localized labels (legacy archive data)
  Wahr:                  { bg: "bg-success/15", text: "text-success" },
  "Größtenteils wahr":   { bg: "bg-success/15", text: "text-success" },
  Irreführend:           { bg: "bg-warning/15", text: "text-warning" },
  "Größtenteils falsch": { bg: "bg-error/15",   text: "text-error" },
  Falsch:                { bg: "bg-error/15",   text: "text-error" },
  // Enum keys
  RELIABLE:              { bg: "bg-success/15", text: "text-success" },
  MOSTLY_RELIABLE:       { bg: "bg-success/15", text: "text-success" },
  MIXED:                 { bg: "bg-warning/15", text: "text-warning" },
  MISLEADING:            { bg: "bg-warning/15", text: "text-warning" },
  HIGHLY_MISLEADING:     { bg: "bg-error/15",   text: "text-error" },
  FABRICATED:            { bg: "bg-error/15",   text: "text-error" },
};

const PLATFORM_ICONS: Record<string, string> = {
  twitter: "\uD835\uDD4F",
  threads: "\uD83E\uDDF5",
  instagram: "\uD83D\uDCF7",
  facebook: "\uD83D\uDCD8",
  youtube: "\u25B6\uFE0F",
  article: "\uD83D\uDCF0",
};

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function timeAgo(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "Gerade eben";
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} Min.`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} Std.`;
  if (diff < 604800) return `vor ${Math.floor(diff / 86400)} Tagen`;
  return formatDate(ts);
}

// ── Page Component ──────────────────────────────────────────────

export default function ArchivePage() {
  const [items, setItems] = useState<ArchiveItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [ratingFilter, setRatingFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ArchiveDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [stats, setStats] = useState<ArchiveStats | null>(null);

  const LIMIT = 20;

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(LIMIT));
      params.set("offset", String(offset));
      if (ratingFilter) params.set("rating", ratingFilter);
      if (search) params.set("search", search);

      const res = await fetch(`/api/v1/archive?${params}`);
      if (!res.ok) throw new Error("Fehler beim Laden");
      const data: ArchiveList = await res.json();
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      console.error("Archive fetch error:", err);
    } finally {
      setLoading(false);
    }
  }, [offset, ratingFilter, search]);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  // Fetch stats once on mount
  useEffect(() => {
    fetch("/api/v1/archive-stats")
      .then((res) => res.json())
      .then((data) => setStats(data))
      .catch((err) => console.error("Archive stats error:", err));
  }, []);

  // Reset offset when filter changes
  useEffect(() => {
    setOffset(0);
  }, [ratingFilter, search]);

  const fetchDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    setSelectedId(id);
    try {
      const res = await fetch(`/api/v1/archive/${id}`);
      if (!res.ok) throw new Error("Fehler beim Laden");
      const data: ArchiveDetail = await res.json();
      setDetail(data);
    } catch (err) {
      console.error("Archive detail error:", err);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const handleDelete = useCallback(async (id: string) => {
    if (!confirm("Diesen Eintrag wirklich löschen?")) return;
    try {
      await fetch(`/api/v1/archive/${id}`, { method: "DELETE" });
      if (selectedId === id) {
        setSelectedId(null);
        setDetail(null);
      }
      fetchList();
    } catch (err) {
      console.error("Archive delete error:", err);
    }
  }, [selectedId, fetchList]);

  const totalPages = Math.ceil(total / LIMIT);
  const currentPage = Math.floor(offset / LIMIT) + 1;

  // ── Detail View ─────────────────────────────────────────────
  if (selectedId && detail) {
    return (
      <div className="min-h-[calc(100vh-64px)] px-4 py-6">
        <div className="max-w-4xl mx-auto">
          {/* Back button */}
          <button
            onClick={() => { setSelectedId(null); setDetail(null); }}
            className="flex items-center gap-1.5 text-sm text-text-tertiary hover:text-text-primary transition-colors mb-5"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
            Zurück zum Archiv
          </button>

          {/* Meta info */}
          <div className="glass-card px-5 py-4 mb-4">
            <div className="flex items-start justify-between gap-4 mb-2">
              <div>
                <h2 className="font-mono text-sm font-bold text-text-primary">
                  {detail.title || detail.summary?.slice(0, 80) || "Analyse"}
                </h2>
                <p className="text-xs text-text-tertiary mt-1">
                  {formatDate(detail.created_at)}
                  {detail.platform && (
                    <span className="ml-2">
                      {PLATFORM_ICONS[detail.platform] || ""} {detail.platform}
                    </span>
                  )}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => window.open(`/api/export/pdf/${detail.id}`, "_blank")}
                  className="inline-flex items-center gap-1.5 text-xs text-text-tertiary hover:text-text-secondary transition-colors"
                  title="Als PDF exportieren"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="7 10 12 15 17 10" />
                    <line x1="12" y1="15" x2="12" y2="3" />
                  </svg>
                  PDF
                </button>
                <button
                  onClick={() => handleDelete(detail.id)}
                  className="text-xs text-text-tertiary hover:text-error transition-colors"
                >
                  Löschen
                </button>
              </div>
            </div>
            {detail.source_url && (
              <a
                href={detail.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs font-mono text-accent hover:underline break-all"
              >
                {detail.source_url}
              </a>
            )}
            {detail.input_text && (
              <p className="text-xs text-text-secondary mt-2 line-clamp-3">
                {detail.input_text}
              </p>
            )}
          </div>

          {/* Full result */}
          <ResultDisplay result={detail.result} archiveId={detail.id} sourceUrl={detail.source_url || undefined} />
        </div>
      </div>
    );
  }

  // ── List View ───────────────────────────────────────────────
  return (
    <div className="min-h-[calc(100vh-64px)] px-4 py-6">
      <div className="max-w-4xl mx-auto">

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="font-mono text-lg font-bold text-text-primary">Archiv</h1>
            <p className="text-xs text-text-tertiary mt-0.5">
              {total} vergangene Analyse{total !== 1 ? "n" : ""}
            </p>
          </div>
          <Link
            href="/"
            className="text-xs text-text-tertiary hover:text-text-primary transition-colors flex items-center gap-1"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
            Neue Analyse
          </Link>
        </div>

        {/* Stats Dashboard */}
        {stats && stats.enabled && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <div className="glass-inner rounded-xl px-4 py-3 border-l-[3px] border-l-text-tertiary/30">
              <div className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-1">
                Einträge
              </div>
              <div className="text-2xl font-semibold text-text-primary tracking-tight leading-none">
                {stats.total_entries}
                <span className="text-xs font-normal text-text-tertiary ml-1">
                  / {stats.max_entries}
                </span>
              </div>
            </div>
            <div className="glass-inner rounded-xl px-4 py-3 border-l-[3px] border-l-success">
              <div className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-1">
                Ø Konfidenz
              </div>
              <div className="text-2xl font-semibold text-text-primary tracking-tight leading-none text-success">
                {stats.average_confidence.toFixed(1)}%
              </div>
            </div>
            <div className="glass-inner rounded-xl px-4 py-3 border-l-[3px] border-l-accent col-span-2">
              <div className="text-[10px] font-medium uppercase tracking-wider text-text-tertiary mb-2">
                Verteilung
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                {Object.entries(stats.rating_distribution)
                  .sort((a, b) => b[1] - a[1])
                  .map(([rating, count]) => {
                    const rs = RATING_STYLES[rating] ?? { bg: "bg-surface-hover/50", text: "text-text-primary" };
                    return (
                      <div key={rating} className={`flex items-center gap-1.5 px-2 py-1 rounded-md ${rs.bg}`}>
                        <span className={`text-[10px] font-bold font-mono ${rs.text}`}>{rating}</span>
                        <span className="text-[10px] font-medium text-text-primary">{count}</span>
                      </div>
                    );
                  })}
              </div>
            </div>
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-col sm:flex-row gap-2 mb-5">
          <div className="flex-1">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Suche in Titel, Zusammenfassung, URL…"
              className="w-full px-3 py-2 text-sm bg-transparent glass-inner rounded-lg text-text-primary placeholder:text-text-tertiary focus:outline-none"
            />
          </div>
          <select
            value={ratingFilter}
            onChange={(e) => setRatingFilter(e.target.value)}
            className="px-3 py-2 text-sm bg-transparent glass-inner rounded-lg text-text-primary focus:outline-none cursor-pointer"
          >
            <option value="">Alle Bewertungen</option>
            <option value="Wahr">Wahr</option>
            <option value="Größtenteils wahr">Größtenteils wahr</option>
            <option value="Irreführend">Irreführend</option>
            <option value="Größtenteils falsch">Größtenteils falsch</option>
            <option value="Falsch">Falsch</option>
          </select>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-12">
            <span className="text-sm text-text-tertiary animate-pulse">Wird geladen…</span>
          </div>
        )}

        {/* Empty state */}
        {!loading && items.length === 0 && (
          <div className="glass-card px-5 py-12 text-center">
            <p className="text-sm text-text-tertiary">
              {search || ratingFilter
                ? "Keine Ergebnisse für diese Filter."
                : "Noch keine Analysen im Archiv. Starte eine neue Analyse!"}
            </p>
          </div>
        )}

        {/* Items */}
        {!loading && items.length > 0 && (
          <div className="space-y-2">
            {items.map((item) => (
              <ArchiveCard
                key={item.id}
                item={item}
                onClick={() => fetchDetail(item.id)}
                onDelete={() => handleDelete(item.id)}
                onPdfExport={() => window.open(`/api/export/pdf/${item.id}`, "_blank")}
              />
            ))}
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-3 mt-6">
            <button
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              disabled={offset === 0}
              className="px-3 py-1.5 text-xs font-mono glass-inner rounded-lg text-text-secondary hover:text-text-primary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Zurück
            </button>
            <span className="text-xs text-text-tertiary font-mono">
              {currentPage} / {totalPages}
            </span>
            <button
              onClick={() => setOffset(offset + LIMIT)}
              disabled={offset + LIMIT >= total}
              className="px-3 py-1.5 text-xs font-mono glass-inner rounded-lg text-text-secondary hover:text-text-primary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Weiter
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Archive Card Component ──────────────────────────────────────

function ArchiveCard({
  item,
  onClick,
  onDelete,
  onPdfExport,
}: {
  item: ArchiveItem;
  onClick: () => void;
  onDelete: () => void;
  onPdfExport: () => void;
}) {
  const rs = RATING_STYLES[item.overall_rating] ?? { bg: "bg-text-tertiary/10", text: "text-text-tertiary" };

  return (
    <div
      className="glass-card px-4 py-3 cursor-pointer hover:bg-surface-hover transition-colors group"
      onClick={onClick}
    >
      <div className="flex items-start gap-3">
        {/* Rating badge */}
        <div className="shrink-0 mt-0.5">
          <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold font-mono ${rs.bg} ${rs.text}`}>
            {item.overall_rating}
          </span>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-sm font-medium text-text-primary truncate">
              {item.platform && (
                <span className="mr-1.5">{PLATFORM_ICONS[item.platform] || ""}</span>
              )}
              {item.title || item.summary?.slice(0, 80) || "Analyse"}
            </h3>
            <span className="text-[10px] text-text-tertiary shrink-0 font-mono">
              {timeAgo(item.created_at)}
            </span>
          </div>

          <p className="text-xs text-text-secondary mt-1 line-clamp-2 leading-relaxed">
            {item.summary}
          </p>

          <div className="flex items-center gap-3 mt-1.5">
            <span className="text-[10px] text-text-tertiary">
              {item.confidence}% Konfidenz
            </span>
            <span className="text-[10px] text-text-tertiary">
              {item.claims_count} Claim{item.claims_count !== 1 ? "s" : ""}
            </span>
            {item.techniques_count > 0 && (
              <span className="text-[10px] text-text-tertiary">
                {item.techniques_count} Technik{item.techniques_count !== 1 ? "en" : ""}
              </span>
            )}
            {item.source_url && (
              <span className="text-[10px] text-accent truncate max-w-[200px]">
                {item.source_url.replace(/^https?:\/\/(?:www\.)?/, "").split("/")[0]}
              </span>
            )}
          </div>
        </div>

        {/* Quick actions (shown on hover) */}
        <div className="shrink-0 flex items-center gap-1">
          <button
            onClick={(e) => { e.stopPropagation(); onPdfExport(); }}
            className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity p-1 text-text-tertiary hover:text-text-secondary"
            aria-label="Als PDF exportieren"
            title="Als PDF exportieren"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity p-1 text-text-tertiary hover:text-error"
            aria-label="Löschen"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
