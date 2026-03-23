"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import ChatInput from "./components/ChatInput";
import ReasoningSteps from "./components/ReasoningSteps";
import ResultDisplay from "./components/ResultDisplay";
import NeuralBrain from "./components/NeuralBrain";
import LeftPanel from "./components/LeftPanel";
import RightPanel from "./components/RightPanel";
import { analyzeArticle, resumeJob, setAuthToken } from "./lib/api";
import { useAuth } from "./lib/auth";
import { AnalysisState, Step, AnalysisResult, ExtractedContent, ScoutTier } from "./lib/types";
import { useI18n } from "./lib/i18n";

const HEADER_HEIGHT = 64;
const STORAGE_KEY = "fng_pending_job";
const TIER_STORAGE_KEY = "fng_scout_tier";
const CONSENT_STORAGE_KEY = "fng_consent";

export default function Home() {
  const [state, setState] = useState<AnalysisState>({ status: "idle" });
  const [tier, setTier] = useState<ScoutTier>("max");
  const [consent, setConsent] = useState<boolean>(false);
  const resultRef = useRef<HTMLDivElement>(null);
  const { user, token } = useAuth();
  const { t } = useI18n();

  // Restore consent from localStorage or user profile
  useEffect(() => {
    if (typeof window !== "undefined") {
      if (user?.consent) {
        setConsent(true);
        localStorage.setItem(CONSENT_STORAGE_KEY, "1");
      } else {
        setConsent(localStorage.getItem(CONSENT_STORAGE_KEY) === "1");
      }
    }
  }, [user]);

  const handleConsent = useCallback(async () => {
    setConsent(true);
    localStorage.setItem(CONSENT_STORAGE_KEY, "1");
    // Persist consent to backend for logged-in users
    if (token) {
      try {
        await fetch("/api/auth/consent", {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        });
      } catch { /* ignore – localStorage already set */ }
    }
  }, [token]);

  // Sync auth token to API module
  useEffect(() => {
    setAuthToken(token);
  }, [token]);

  // Sync tier to user's plan when logged in
  useEffect(() => {
    if (user) {
      setTier(user.tier as ScoutTier);
      localStorage.setItem(TIER_STORAGE_KEY, user.tier);
    }
  }, [user]);

  // Restore tier preference from localStorage
  useEffect(() => {
    const saved = localStorage.getItem(TIER_STORAGE_KEY);
    if (saved && (saved === "lite" || saved === "pro" || saved === "max")) {
      setTier(saved);
    }
  }, []);

  const handleTierChange = useCallback((newTier: ScoutTier) => {
    setTier(newTier);
    localStorage.setItem(TIER_STORAGE_KEY, newTier);
  }, []);

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

  const onExtractedContent = useCallback((content: ExtractedContent) => {
    setState((prev) => {
      if (prev.status !== "analyzing") return prev;
      return { ...prev, extractedContent: content };
    });
  }, []);

  const finishWithResult = useCallback((result: AnalysisResult) => {
    localStorage.removeItem(STORAGE_KEY);
    setState((prev) => ({
      status: "done",
      steps: prev.status === "analyzing" ? prev.steps : [],
      result,
      extractedContent: (prev as { extractedContent?: ExtractedContent }).extractedContent,
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
    async (text: string, url?: string, selectedTier?: ScoutTier) => {
      const useTier = selectedTier ?? tier;
      setState({ status: "analyzing", steps: [], currentPhase: url ? "Phase 0" : "Phase 1" });
      try {
        const result = await analyzeArticle(
          text,
          onStep,
          (jobId) => {
            localStorage.setItem(
              STORAGE_KEY,
              JSON.stringify({ jobId, submittedAt: Date.now() }),
            );
          },
          onExtractedContent,
          url,
          useTier,
        );
        finishWithResult(result);
      } catch (err: unknown) {
        const message =
          err instanceof Error ? err.message : "Analyse fehlgeschlagen. Bitte erneut versuchen.";
        finishWithError(message);
      }
    },
    [tier, onStep, onExtractedContent, finishWithResult, finishWithError],
  );

  const isAnalyzing = state.status === "analyzing";
  const isIdle = state.status === "idle";
  const steps: Step[] = (state as { steps?: Step[] }).steps ?? [];
  const result: AnalysisResult | undefined =
    state.status === "done" ? state.result : undefined;

  if (isIdle) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] px-4">
        <div className="w-full max-w-2xl animate-fade-in">
          <p className="text-sm text-text-tertiary text-center mb-6 leading-relaxed">
            Text, Artikel, Behauptung oder Link einfügen — das System extrahiert Claims,
            prüft Fakten und analysiert Manipulationstechniken.
          </p>

          {!consent ? (
            <div className="glass-card border-accent/20 px-5 py-4 mb-4 text-center">
              <p className="text-xs text-text-secondary mb-3 leading-relaxed">
                {t("consent.notice")}
              </p>
              <button
                onClick={handleConsent}
                className="px-4 py-1.5 text-xs font-mono rounded-lg bg-accent/20 text-accent hover:bg-accent/30 transition-colors"
              >
                {t("consent.accept")}
              </button>
            </div>
          ) : (
            <p className="text-[10px] text-text-tertiary/50 text-center mb-4">
              {t("consent.notice")}
            </p>
          )}

          <ChatInput onSubmit={handleSubmit} disabled={!consent} tier={tier} onTierChange={handleTierChange} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-[calc(100vh-64px)]">
      {/* Three-column grid */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-[180px_1fr_160px] gap-3 lg:gap-0 px-3 lg:px-0">

        {/* Left panel */}
        <aside
          className="hidden lg:block glass-panel rounded-2xl ml-3 mt-3"
          style={{ position: "sticky", top: HEADER_HEIGHT + 12, alignSelf: "start", maxHeight: `calc(100vh - ${HEADER_HEIGHT + 24}px)`, overflowY: "auto" }}
        >
          <LeftPanel steps={steps} result={result} isAnalyzing={isAnalyzing} />
        </aside>

        {/* Main content */}
        <div className="min-w-0 px-1 lg:px-8 py-5">
          {(state.status === "analyzing" || state.status === "done") && (
            <ReasoningSteps steps={steps} isActive={isAnalyzing} />
          )}

          {isAnalyzing && <NeuralBrain />}

          {state.status === "done" && (
            <div ref={resultRef}>
              <ResultDisplay result={state.result} />
            </div>
          )}

          {state.status === "error" && (
            <div className="glass-card border-error/30 px-5 py-4">
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
          className="hidden lg:block glass-panel rounded-2xl mr-3 mt-3"
          style={{ position: "sticky", top: HEADER_HEIGHT + 12, alignSelf: "start", maxHeight: `calc(100vh - ${HEADER_HEIGHT + 24}px)`, overflowY: "auto" }}
        >
          <RightPanel steps={steps} result={result} isAnalyzing={isAnalyzing} />
        </aside>
      </div>

      {/* Input bar – floating glass at bottom */}
      <div className="sticky bottom-3 mx-4 glass-bar rounded-2xl px-4 py-2.5 shadow-lg">
        <div className="max-w-3xl mx-auto">
          <ChatInput onSubmit={handleSubmit} disabled={isAnalyzing} tier={tier} onTierChange={handleTierChange} />
        </div>
      </div>
    </div>
  );
}
