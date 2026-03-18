import { Step, AnalysisResult } from "./types";

const BASE_URL = "/api";
const POLL_INTERVAL_MS = 1500;
const MAX_POLL_ATTEMPTS = 300; // 300 × 1.5 s = 7.5 min timeout

/**
 * Submits text for analysis and returns a job_id immediately.
 */
async function submitJob(text: string): Promise<string> {
  const res = await fetch(`${BASE_URL}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
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
): Promise<AnalysisResult> {
  const seenStepIds = new Set<string>();

  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
    await sleep(POLL_INTERVAL_MS);

    const res = await fetch(`${BASE_URL}/jobs/${jobId}`);
    if (res.status === 404) throw new Error("Job nicht gefunden — möglicherweise abgelaufen.");
    if (!res.ok) throw new Error(`Poll-Fehler: ${res.status} ${res.statusText}`);

    const data = await res.json();

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
): Promise<AnalysisResult> {
  const jobId = await submitJob(text);
  onJobId?.(jobId);
  return resumeJob(jobId, onStep);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
