// ── Types ────────────────────────────────────────────────────────────

export type Period = "7d" | "30d" | "90d" | "all" | "custom";

export type WidgetId =
  | "timeline"
  | "ratingDist"
  | "topics"
  | "platforms"
  | "sources"
  | "accuracy";

export interface WidgetMeta {
  id: WidgetId;
  colSpan: 1 | 2;
  hasChart: boolean;
}

export const DEFAULT_WIDGET_ORDER: WidgetId[] = [
  "timeline",
  "ratingDist",
  "topics",
  "platforms",
  "sources",
  "accuracy",
];

export const WIDGET_META: Record<WidgetId, WidgetMeta> = {
  timeline:   { id: "timeline",   colSpan: 2, hasChart: true },
  ratingDist: { id: "ratingDist", colSpan: 2, hasChart: true },
  topics:     { id: "topics",     colSpan: 1, hasChart: true },
  platforms:  { id: "platforms",  colSpan: 1, hasChart: true },
  sources:    { id: "sources",    colSpan: 2, hasChart: false },
  accuracy:   { id: "accuracy",   colSpan: 2, hasChart: true },
};

export interface DateRange {
  from: string;
  to: string;
}

export interface TimelineBucket {
  date: string;
  count: number;
  avg_confidence: number;
  rating_distribution: Record<string, number>;
  avg_claims_per_analysis: number;
}

export interface TimelineData {
  buckets: TimelineBucket[];
  period: string;
  bucket: string;
  total_analyses: number;
}

export interface Topic {
  topic: string;
  count: number;
  avg_rating_score: number;
  trend: "rising" | "stable" | "declining";
}

export interface TopicsData {
  topics: Topic[];
  period: string;
}

export interface Source {
  domain: string;
  citation_count: number;
  first_seen: string;
  last_seen: string;
}

export interface SourcesData {
  sources: Source[];
  total_unique_sources: number;
  period: string;
}

export interface AccuracyBucket {
  date: string;
  avg_confidence: number;
  high_confidence_ratio: number;
  fabricated_ratio: number;
}

export interface ConfidenceBand {
  range: string;
  count: number;
  avg_rating_score: number;
}

export interface AccuracyData {
  accuracy_over_time: AccuracyBucket[];
  overall_brier_score: number;
  confidence_bands: ConfidenceBand[];
  period: string;
}

export interface Platform {
  platform: string;
  count: number;
  avg_rating_score: number;
  avg_confidence: number;
}

export interface PlatformsData {
  platforms: Platform[];
  period: string;
}

// ── Colors ───────────────────────────────────────────────────────────

export const RATING_COLORS: Record<string, string> = {
  RELIABLE:          "#1a6b3c",
  MOSTLY_RELIABLE:   "#2d9e5f",
  MIXED:             "#a16200",
  MISLEADING:        "#c41e1e",
  HIGHLY_MISLEADING: "#8b1515",
  FABRICATED:        "#5c0000",
};

export const TREND_STYLE: Record<string, string> = {
  rising:    "text-success",
  stable:    "text-text-tertiary",
  declining: "text-error",
};

export const PLATFORM_COLORS = [
  "#c41e1e", "#1a6b3c", "#a16200", "#1e5f8b", "#6b1a6b", "#6b6b1a",
];

export const RATINGS_ORDER = [
  "RELIABLE", "MOSTLY_RELIABLE", "MIXED", "MISLEADING", "HIGHLY_MISLEADING", "FABRICATED",
];

// ── Chart tooltip style ──────────────────────────────────────────────

export const TOOLTIP_STYLE = {
  backgroundColor: "var(--surface-card, #fff)",
  border: "1px solid var(--border)",
  borderRadius: "10px",
  fontSize: "11px",
  color: "var(--text-primary)",
};

// ── Helpers ──────────────────────────────────────────────────────────

export function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

export function shortLabel(date: string): string {
  if (!date) return "";
  if (date.includes("W")) {
    return date.split("-")[1] ?? date;
  }
  const parts = date.split("-");
  if (parts.length === 2) return date;
  return parts.slice(1).join("/");
}

export function computeKpis(timeline: TimelineData | null) {
  if (!timeline || !timeline.buckets || timeline.total_analyses === 0) {
    return { total: 0, avgConf: 0, topRating: "—", trendSign: "→" };
  }
  const buckets = timeline.buckets;
  const total = timeline.total_analyses;

  const totalConf = buckets.reduce((s, b) => s + b.avg_confidence * b.count, 0);
  const avgConf = total > 0 ? totalConf / total : 0;

  const ratingTotals: Record<string, number> = {};
  for (const b of buckets) {
    for (const [r, n] of Object.entries(b.rating_distribution)) {
      ratingTotals[r] = (ratingTotals[r] ?? 0) + n;
    }
  }
  const topRating =
    Object.entries(ratingTotals).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "—";

  const mid = Math.floor(buckets.length / 2);
  const first = buckets.slice(0, mid).reduce((s, b) => s + b.count, 0);
  const second = buckets.slice(mid).reduce((s, b) => s + b.count, 0);
  const trendSign = second > first * 1.1 ? "↑" : second < first * 0.9 ? "↓" : "→";

  return { total, avgConf, topRating, trendSign };
}
