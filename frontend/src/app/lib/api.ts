import { Step, AnalysisResult, ExtractedContent, ScoutTier, GraphStats, GraphSearchResult, GraphNodeDetail } from "./types";

export interface AnalysisJobResult {
  result: AnalysisResult;
  archiveId?: string;
}

import { BACKEND_URL as BASE_URL, POLL_INTERVAL_MS, MAX_POLL_ATTEMPTS, SSE_MAX_RECONNECT_FAILURES } from "@/config";


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
  ["youtube", /https?:\/\/(?:www\.)?(?:youtube\.com\/(?:watch|shorts\/|live\/|embed\/|v\/)|youtu\.be\/)/i],
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
  "commander-pro": "Commander Pro",
  "commander-max": "Commander Max",
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
 * SSE-based job streaming. Connects to /api/jobs/{jobId}/stream and
 * receives events in real-time (~500ms latency instead of 2s polling).
 */
function resumeJobSSE(
  jobId: string,
  onStep: (step: Step) => void,
  onExtractedContent?: (content: ExtractedContent) => void,
): Promise<AnalysisJobResult> {
  return new Promise((resolve, reject) => {
    const url = `${BASE_URL}/jobs/${jobId}/stream`;
    const es = new EventSource(url);
    let settled = false;
    let reconnectFailures = 0;

    function close(fn: () => void) {
      if (settled) return;
      settled = true;
      es.close();
      fn();
    }

    es.addEventListener("step", (e: MessageEvent) => {
      reconnectFailures = 0;  // successful message resets counter
      try { onStep(JSON.parse(e.data) as Step); } catch { /* ignore parse errors */ }
    });

    es.addEventListener("extracted_content", (e: MessageEvent) => {
      reconnectFailures = 0;
      try { onExtractedContent?.(JSON.parse(e.data) as ExtractedContent); } catch { /* ignore */ }
    });

    es.addEventListener("done", (e: MessageEvent) => {
      close(() => {
        try {
          const data = JSON.parse(e.data);
          if (!data.result) return reject(new Error("Kein Ergebnis vom Server erhalten."));
          resolve({
            result: data.result as AnalysisResult,
            archiveId: data.archive_id as string | undefined,
          });
        } catch { reject(new Error("Ungueltige Server-Antwort.")); }
      });
    });

    es.addEventListener("error", (e: MessageEvent) => {
      close(() => {
        try {
          const data = JSON.parse(e.data);
          reject(new Error(data.error ?? "Analyse fehlgeschlagen."));
        } catch { reject(new Error("Analyse fehlgeschlagen.")); }
      });
    });

    es.addEventListener("timeout", () => {
      close(() => reject(new Error("Zeitueberschreitung: Analyse dauert zu lange.")));
    });

    // Connection-level errors (network failure, server down)
    es.onerror = () => {
      reconnectFailures++;
      if (reconnectFailures >= SSE_MAX_RECONNECT_FAILURES) {
        // EventSource auto-reconnects, but after too many failures we give up
        // and let the caller fall back to polling
        close(() => reject(new Error("__SSE_FALLBACK__")));
      }
      // Otherwise let EventSource auto-reconnect (it sends Last-Event-ID)
    };
  });
}

/**
 * Polling fallback: polls /api/jobs/{jobId} until done or error.
 * Used when SSE is unavailable or fails.
 */
async function resumeJobPolling(
  jobId: string,
  onStep: (step: Step) => void,
  onExtractedContent?: (content: ExtractedContent) => void,
): Promise<AnalysisJobResult> {
  const seenStepIds = new Set<string>();
  let extractedContentEmitted = false;

  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
    await sleep(POLL_INTERVAL_MS);

    const res = await fetch(`${BASE_URL}/jobs/${jobId}`);
    if (res.status === 404) throw new Error("Job nicht gefunden — moeglicherweise abgelaufen.");
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
      return {
        result: data.result as AnalysisResult,
        archiveId: data.archive_id as string | undefined,
      };
    }
    if (data.status === "error") {
      throw new Error(data.error ?? "Analyse fehlgeschlagen.");
    }
    // status "pending" | "running" → keep polling
  }

  throw new Error("Zeitueberschreitung: Analyse dauert zu lange.");
}

/**
 * Resume a job: tries SSE first, falls back to polling on failure.
 */
export async function resumeJob(
  jobId: string,
  onStep: (step: Step) => void,
  onExtractedContent?: (content: ExtractedContent) => void,
): Promise<AnalysisJobResult> {
  try {
    return await resumeJobSSE(jobId, onStep, onExtractedContent);
  } catch (err) {
    // SSE failed — fall back to polling
    const msg = err instanceof Error ? err.message : "";
    if (msg === "__SSE_FALLBACK__" || msg.includes("EventSource")) {
      return resumeJobPolling(jobId, onStep, onExtractedContent);
    }
    // Real application error (job error, timeout) — rethrow
    throw err;
  }
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
): Promise<AnalysisJobResult> {
  const jobId = await submitJob(text, url, tier);
  onJobId?.(jobId);
  return resumeJob(jobId, onStep, onExtractedContent);
}

// ── Graph API ────────────────────────────────────────────────────

export async function fetchGraphStats(): Promise<GraphStats> {
  const res = await fetch(`${BASE_URL}/graph/stats`);
  if (!res.ok) throw new Error(`Graph stats error: ${res.status}`);
  return res.json();
}

export async function fetchGraphSearch(
  type?: string,
  q?: string,
  limit = 50,
  includeEdges = false,
): Promise<GraphSearchResult> {
  const params = new URLSearchParams();
  if (type) params.set("type", type);
  if (q) params.set("q", q);
  params.set("limit", String(limit));
  if (includeEdges) params.set("include_edges", "true");
  const res = await fetch(`${BASE_URL}/graph/search?${params}`);
  if (!res.ok) throw new Error(`Graph search error: ${res.status}`);
  return res.json();
}

export async function fetchGraphNode(nodeId: string): Promise<GraphNodeDetail> {
  const res = await fetch(`${BASE_URL}/graph/node/${encodeURIComponent(nodeId)}`);
  if (!res.ok) throw new Error(`Graph node error: ${res.status}`);
  return res.json();
}

export async function fetchGraphActor(
  actorName: string,
): Promise<{ actor: string; claims: { id: string; text: string; rating: string }[] }> {
  const res = await fetch(`${BASE_URL}/graph/actor/${encodeURIComponent(actorName)}`);
  if (!res.ok) throw new Error(`Graph actor error: ${res.status}`);
  return res.json();
}

export async function fetchGraphSource(
  domain: string,
): Promise<{ domain: string; total_references: number; claims: { claim: string; relation: string; rating: string }[] }> {
  const res = await fetch(`${BASE_URL}/graph/source/${encodeURIComponent(domain)}`);
  if (!res.ok) throw new Error(`Graph source error: ${res.status}`);
  return res.json();
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
