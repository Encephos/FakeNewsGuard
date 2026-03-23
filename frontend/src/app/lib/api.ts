import { Step, AnalysisResult, ExtractedContent, ScoutTier } from "./types";

const BASE_URL = "/api";
const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 960; // 960 × 2 s = 32 min (backend hard cap at 30 min)

// ── Auth token injection ────────────────────────────────────────
let _authToken: string | null = null;

export function setAuthToken(token: string | null) {
  _authToken = token;
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (_authToken) headers["Authorization"] = `Bearer ${_authToken}`;
  return headers;
}

/** URL detection regex (matches http/https URLs) */
const URL_REGEX =
  /https?:\/\/[^\s<>"')\]]+/i;

/** Detect known social media / news platforms */
const PLATFORM_PATTERNS: [string, RegExp][] = [
  ["twitter", /https?:\/\/(?:www\.)?(?:twitter\.com|x\.com)\/\w+\/status\/\d+/i],
  ["threads", /https?:\/\/(?:www\.)?threads\.net\//i],
  ["instagram", /https?:\/\/(?:www\.)?instagram\.com\/(?:p|reel|tv)\//i],
  ["facebook", /https?:\/\/(?:www\.|m\.)?facebook\.com\//i],
  ["youtube", /https?:\/\/(?:www\.)?(?:youtube\.com\/watch|youtu\.be\/)/i],
];

export function detectUrl(text: string): string | null {
  const match = text.match(URL_REGEX);
  return match ? match[0] : null;
}

export function detectPlatform(url: string): string {
  for (const [name, pattern] of PLATFORM_PATTERNS) {
    if (pattern.test(url)) return name;
  }
  return "article";
}

export function isUrl(text: string): boolean {
  const trimmed = text.trim();
  const url = detectUrl(trimmed);
  if (!url) return false;
  // Text is "essentially" a URL if removing the URL leaves < 20 chars
  return trimmed.length - url.length < 20;
}

export function getPlatformLabel(platform: string): string {
  const labels: Record<string, string> = {
    twitter: "Twitter / X",
    threads: "Threads",
    instagram: "Instagram",
    facebook: "Facebook",
    youtube: "YouTube",
    article: "Artikel",
  };
  return labels[platform] || "Link";
}

export function getPlatformIcon(platform: string): string {
  const icons: Record<string, string> = {
    twitter: "𝕏",
    threads: "🧵",
    instagram: "📷",
    facebook: "📘",
    youtube: "▶",
    article: "📰",
  };
  return icons[platform] || "🔗";
}

/**
 * Extract content from a URL without analysis (preview only).
 */
export async function extractContent(url: string): Promise<ExtractedContent & { text: string }> {
  const res = await fetch(`${BASE_URL}/extract`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Extraktion fehlgeschlagen: ${res.status}`);
  }
  return res.json();
}

/** Map tier id to agent name sent to backend */
const TIER_AGENT_MAP: Record<ScoutTier, string> = {
  lite: "Scout Lite",
  pro: "Scout Pro",
  max: "Scout Max",
};

/**
 * Submits text (and optionally a URL) for analysis and returns a job_id immediately.
 */
async function submitJob(text: string, url?: string, tier: ScoutTier = "max"): Promise<string> {
  const body: Record<string, string> = {
    text,
    agent: TIER_AGENT_MAP[tier],
    tier,
  };
  if (url) body.url = url;

  const res = await fetch(`${BASE_URL}/analyze`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`API-Fehler: ${res.status} ${res.statusText}`);
  }
  const data = await res.json();
  if (!data.job_id) throw new Error("Keine Job-ID vom Server erhalten.");
  return data.job_id as string;
}

/**
 * Polls /api/jobs/{jobId} until done or error.
 * Calls onStep with new steps as they appear.
 */
export async function resumeJob(
  jobId: string,
  onStep: (step: Step) => void,
  onExtractedContent?: (content: ExtractedContent) => void,
): Promise<AnalysisResult> {
  const seenStepIds = new Set<string>();
  let extractedContentEmitted = false;

  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
    await sleep(POLL_INTERVAL_MS);

    const res = await fetch(`${BASE_URL}/jobs/${jobId}`);
    if (res.status === 404) throw new Error("Job nicht gefunden — möglicherweise abgelaufen.");
    if (!res.ok) throw new Error(`Poll-Fehler: ${res.status} ${res.statusText}`);

    const data = await res.json();

    // Emit extracted content once available
    if (data.extracted_content && !extractedContentEmitted && onExtractedContent) {
      onExtractedContent(data.extracted_content);
      extractedContentEmitted = true;
    }

    // Emit new steps in order
    for (const step of (data.steps ?? []) as Step[]) {
      if (!seenStepIds.has(step.id)) {
        seenStepIds.add(step.id);
        onStep(step);
      }
    }

    if (data.status === "done") {
      if (!data.result) throw new Error("Kein Ergebnis vom Server erhalten.");
      return data.result as AnalysisResult;
    }
    if (data.status === "error") {
      throw new Error(data.error ?? "Analyse fehlgeschlagen.");
    }
    // status "pending" | "running" → keep polling
  }

  throw new Error("Zeitüberschreitung: Analyse dauert zu lange.");
}

/**
 * Main entry point used by the page.
 * Submits text, calls onJobId with the job_id once known (for persistence),
 * then polls until done.
 */
export async function analyzeArticle(
  text: string,
  onStep: (step: Step) => void,
  onJobId?: (jobId: string) => void,
  onExtractedContent?: (content: ExtractedContent) => void,
  url?: string,
  tier: ScoutTier = "max",
): Promise<AnalysisResult> {
  const jobId = await submitJob(text, url, tier);
  onJobId?.(jobId);
  return resumeJob(jobId, onStep, onExtractedContent);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
