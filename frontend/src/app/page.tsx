"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import ChatInput from "./components/ChatInput";
import ReasoningSteps from "./components/ReasoningSteps";
import ResultDisplay from "./components/ResultDisplay";
import NeuralBrain from "./components/NeuralBrain";
import LeftPanel from "./components/LeftPanel";
import RightPanel from "./components/RightPanel";
import { analyzeArticle, resumeJob, setAuthToken, AnalysisJobResult } from "./lib/api";
import { useAuth } from "./lib/auth";
import { AnalysisState, Step, AnalysisResult, ExtractedContent, ScoutTier } from "./lib/types";
import { useI18n } from "./lib/i18n";

const HEADER_HEIGHT = 64;
const STORAGE_KEY = "fng_pending_job";
const TIER_STORAGE_KEY = "fng_scout_tier";
const CONSENT_STORAGE_KEY = "fng_consent";

export default function Home() {
  const [state, setState] = useState<AnalysisState>({ status: "idle" });
  const [archiveId, setArchiveId] = useState<string | undefined>(undefined);
  const [sourceUrl, setSourceUrl] = useState<string | undefined>(undefined);
  const [tier, setTier] = useState<ScoutTier>("max");
  const [consent, setConsent] = useState<boolean>(false);
  const [isMobileHeaderOpen, setIsMobileHeaderOpen] = useState(true);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
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

  const finishWithResult = useCallback((jobResult: AnalysisJobResult) => {
    localStorage.removeItem(STORAGE_KEY);
    setArchiveId(jobResult.archiveId);
    setState((prev) => ({
      status: "done",
      steps: prev.status === "analyzing" ? prev.steps : [],
      result: jobResult.result,
      extractedContent: (prev as { extractedContent?: ExtractedContent }).extractedContent,
    }));
    setIsMobileHeaderOpen(false);
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
      setSourceUrl(url);
      setArchiveId(undefined);
      setState({ status: "analyzing", steps: [], currentPhase: url ? "Phase 0" : "Phase 1" });
      setIsMobileHeaderOpen(true);
      try {
        const jobResult = await analyzeArticle(
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
        finishWithResult(jobResult);
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
          {/* Mobile Collapsible Header */}
          {(state.status === "analyzing" || state.status === "done") && (
            <div className="lg:hidden glass-card mb-5 overflow-hidden animate-fade-in relative">
              <button 
                onClick={() => setIsMobileHeaderOpen((o) => !o)} 
                className="w-full px-4 py-3 flex items-center justify-between text-xs font-semibold text-text-secondary hover:text-text-primary transition-colors bg-white/5 active:bg-white/10"
              >
                <span>{state.status === "analyzing" ? "Analyse läuft..." : "Resultate & Metriken"}</span>
                <span className="text-[10px] transform transition-transform duration-200" style={{ transform: isMobileHeaderOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>
                  ▼
                </span>
              </button>
              
              {isMobileHeaderOpen && (
                <div className="border-t border-[var(--glass-inner-border)] animate-fade-in">
                  {isAnalyzing && <NeuralBrain />}
                  <RightPanel steps={steps} result={result} isAnalyzing={isAnalyzing} />
                </div>
              )}
            </div>
          )}

          {(state.status === "analyzing" || state.status === "done") && (
            <div className="hidden lg:block">
              <ReasoningSteps steps={steps} isActive={isAnalyzing} />
            </div>
          )}

          <div className="hidden lg:block">
            {isAnalyzing && <NeuralBrain />}
          </div>

          {state.status === "done" && (
            <div ref={resultRef}>
              <ResultDisplay result={state.result} archiveId={archiveId} sourceUrl={sourceUrl} />
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
      <div className="sticky bottom-3 mx-4 z-40">
        {(state.status === "analyzing" || state.status === "done") && (
          <div className="flex justify-center mb-3 lg:hidden">
            <button
              onClick={() => setIsDrawerOpen(true)}
              className="glass-button rounded-full px-5 py-2.5 shadow-lg flex items-center gap-2.5 text-xs font-medium border border-[var(--glass-inner-border)] backdrop-blur-md"
            >
              <span className={`h-2 w-2 rounded-full ${state.status === "analyzing" ? "bg-warning animate-pulse-dot" : "bg-success"}`} />
              {state.status === "analyzing" ? "Fortschritt anzeigen" : "Analyse-Details ansehen"}
            </button>
          </div>
        )}
        <div className="glass-bar rounded-2xl px-4 py-2.5 shadow-lg">
          <div className="max-w-3xl mx-auto">
            <ChatInput onSubmit={handleSubmit} disabled={isAnalyzing} tier={tier} onTierChange={handleTierChange} />
          </div>
        </div>
      </div>

      {/* Mobile Drawer (Bottom Sheet) */}
      {isDrawerOpen && (
        <div className="fixed inset-0 z-[100] lg:hidden flex flex-col justify-end animate-fade-in">
          {/* Backdrop */}
          <div 
            className="absolute inset-0 bg-black/60 backdrop-blur-sm" 
            onClick={() => setIsDrawerOpen(false)}
          />
          {/* Sheet */}
          <div className="relative bg-[#111] border-t border-[var(--glass-inner-border)] rounded-t-3xl w-full max-h-[85vh] flex flex-col shadow-2xl">
            {/* Handle for visual dragging metaphor */}
            <div className="w-full flex justify-center py-3 cursor-pointer" onClick={() => setIsDrawerOpen(false)}>
              <div className="w-12 h-1.5 rounded-full bg-text-tertiary/40" />
            </div>
            
            <div className="px-5 pb-4 border-b border-[var(--glass-inner-border)] flex justify-between items-center">
              <h3 className="text-sm font-semibold tracking-wide text-text-primary">Analyse-Details</h3>
              <button onClick={() => setIsDrawerOpen(false)} className="p-2 -mr-2 text-text-tertiary hover:text-text-primary transition-colors">
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M1 1L13 13M1 13L13 1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            </div>
            
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <div className="glass-card rounded-2xl overflow-hidden bg-white/5">
                <LeftPanel steps={steps} result={result} isAnalyzing={isAnalyzing} />
              </div>
              <ReasoningSteps steps={steps} isActive={isAnalyzing} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
