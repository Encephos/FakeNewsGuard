"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import ChatInput from "./components/ChatInput";
import ReasoningSteps from "./components/ReasoningSteps";
import ResultDisplay from "./components/ResultDisplay";
import LeftPanel from "./components/LeftPanel";
import RightPanel from "./components/RightPanel";
import { analyzeArticle, resumeJob } from "./lib/api";
import { AnalysisState, Step, AnalysisResult } from "./lib/types";

const HEADER_HEIGHT = 46;
const STORAGE_KEY = "fng_pending_job";

export default function Home() {
  const [state, setState] = useState<AnalysisState>({ status: "idle" });
  const resultRef = useRef<HTMLDivElement>(null);

  // On mount: if there's a saved job, resume it automatically
  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (!saved) return;
    try {
      const { jobId, submittedAt } = JSON.parse(saved) as { jobId: string; submittedAt: number };
      // Ignore jobs older than 10 minutes
      if (Date.now() - submittedAt > 10 * 60 * 1000) {
        localStorage.removeItem(STORAGE_KEY);
        return;
      }
      // Resume polling
      setState({ status: "analyzing", steps: [], currentPhase: "Analyse läuft…" });
      resumeJobById(jobId);
    } catch {
      localStorage.removeItem(STORAGE_KEY);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (state.status === "done" && resultRef.current) {
      resultRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [state.status]);

  const onStep = useCallback((step: Step) => {
    setState((prev) => {
      if (prev.status !== "analyzing") return prev;
      const steps = [...prev.steps];
      const idx = steps.findIndex((s) => s.id === step.id);
      if (idx >= 0) steps[idx] = step;
      else steps.push(step);
      return { ...prev, steps, currentPhase: step.phase };
    });
  }, []);

  const finishWithResult = useCallback((result: AnalysisResult) => {
    localStorage.removeItem(STORAGE_KEY);
    setState((prev) => ({
      status: "done",
      steps: prev.status === "analyzing" ? prev.steps : [],
      result,
    }));
  }, []);

  const finishWithError = useCallback((message: string) => {
    localStorage.removeItem(STORAGE_KEY);
    setState({ status: "error", message });
  }, []);

  // Resume an already-running job (called on mount if saved job found)
  const resumeJobById = useCallback(
    (jobId: string) => {
      resumeJob(jobId, onStep)
        .then(finishWithResult)
        .catch((err: unknown) => {
          const msg =
            err instanceof Error ? err.message : "Analyse fehlgeschlagen. Bitte erneut versuchen.";
          finishWithError(msg);
        });
    },
    [onStep, finishWithResult, finishWithError],
  );

  const handleSubmit = useCallback(
    async (text: string) => {
      setState({ status: "analyzing", steps: [], currentPhase: "Phase 1" });
      try {
        const result = await analyzeArticle(text, onStep, (jobId) => {
          // Save job_id as soon as we get it so mobile can resume
          localStorage.setItem(
            STORAGE_KEY,
            JSON.stringify({ jobId, submittedAt: Date.now() }),
          );
        });
        finishWithResult(result);
      } catch (err: unknown) {
        const message =
          err instanceof Error ? err.message : "Analyse fehlgeschlagen. Bitte erneut versuchen.";
        finishWithError(message);
      }
    },
    [onStep, finishWithResult, finishWithError],
  );

  const isAnalyzing = state.status === "analyzing";
  const isIdle = state.status === "idle";
  const steps: Step[] = (state as { steps?: Step[] }).steps ?? [];
  const result: AnalysisResult | undefined =
    state.status === "done" ? state.result : undefined;

  if (isIdle) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-46px)] px-4">
        <div className="w-full max-w-2xl animate-fade-in">
          <p className="text-sm text-text-tertiary text-center mb-6 leading-relaxed">
            Text, Artikel oder Behauptung eingeben — das System extrahiert Claims,
            prüft Fakten und analysiert Manipulationstechniken.
          </p>
          <ChatInput onSubmit={handleSubmit} disabled={false} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-[calc(100vh-46px)]">
      {/* Three-column grid */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-[180px_1fr_160px] lg:divide-x lg:divide-border">

        {/* Left panel */}
        <aside
          className="hidden lg:block bg-bg-tertiary"
          style={{ position: "sticky", top: HEADER_HEIGHT, alignSelf: "start", maxHeight: `calc(100vh - ${HEADER_HEIGHT}px)`, overflowY: "auto" }}
        >
          <LeftPanel steps={steps} result={result} isAnalyzing={isAnalyzing} />
        </aside>

        {/* Main content */}
        <div className="min-w-0 px-4 lg:px-8 py-7">
          {(state.status === "analyzing" || state.status === "done") && (
            <ReasoningSteps steps={steps} isActive={isAnalyzing} />
          )}

          {state.status === "done" && (
            <div ref={resultRef}>
              <ResultDisplay result={state.result} />
            </div>
          )}

          {state.status === "error" && (
            <div className="border border-error/40 px-4 py-3">
              <p className="text-xs font-mono text-error">{state.message}</p>
              <button
                className="mt-3 text-xs font-mono text-text-tertiary underline hover:text-text-primary"
                onClick={() => setState({ status: "idle" })}
              >
                Erneut versuchen
              </button>
            </div>
          )}
        </div>

        {/* Right panel */}
        <aside
          className="hidden lg:block bg-bg-tertiary"
          style={{ position: "sticky", top: HEADER_HEIGHT, alignSelf: "start", maxHeight: `calc(100vh - ${HEADER_HEIGHT}px)`, overflowY: "auto" }}
        >
          <RightPanel steps={steps} result={result} isAnalyzing={isAnalyzing} />
        </aside>
      </div>

      {/* Input bar – sticky at bottom */}
      <div className="sticky bottom-0 bg-bg-primary/95 backdrop-blur-sm px-4 py-2.5" style={{ boxShadow: "var(--shadow-footer)" }}>
        <div className="max-w-3xl mx-auto">
          <ChatInput onSubmit={handleSubmit} disabled={isAnalyzing} />
        </div>
      </div>
    </div>
  );
}
