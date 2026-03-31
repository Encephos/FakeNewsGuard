"use client";

import { useState, useCallback, useEffect } from "react";
import { Player, type PlayerRef } from "@remotion/player";
import { useRouter } from "next/navigation";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { Step1Welcome } from "./compositions/Step1Welcome";
import { Step2HowItWorks } from "./compositions/Step2HowItWorks";
import { Step3Results } from "./compositions/Step3Results";
import { Step4Tiers } from "./compositions/Step4Tiers";
import { Step5Start } from "./compositions/Step5Start";
import React from "react";

const STEP_DURATION = 120; // 4 seconds per step at 30fps

const steps = [
  {
    id: "welcome",
    titleDe: "Willkommen",
    titleEn: "Welcome",
    component: Step1Welcome,
  },
  {
    id: "how-it-works",
    titleDe: "Wie es funktioniert",
    titleEn: "How it works",
    component: Step2HowItWorks,
  },
  {
    id: "results",
    titleDe: "Ergebnisse",
    titleEn: "Results",
    component: Step3Results,
  },
  {
    id: "tiers",
    titleDe: "Scout-Stufen",
    titleEn: "Scout Tiers",
    component: Step4Tiers,
  },
  {
    id: "start",
    titleDe: "Los geht's",
    titleEn: "Get started",
    component: Step5Start,
  },
];

export default function OnboardingPage() {
  const [currentStep, setCurrentStep] = useState(0);
  const { user } = useAuth();
  const { locale } = useI18n();
  const router = useRouter();
  const playerRef = React.useRef<PlayerRef>(null);

  const isLast = currentStep === steps.length - 1;

  const goNext = useCallback(() => {
    if (isLast) {
      localStorage.setItem("onboarding_completed", "true");
      router.push("/");
    } else {
      setCurrentStep((s) => s + 1);
    }
  }, [isLast, router]);

  const goPrev = useCallback(() => {
    if (currentStep > 0) setCurrentStep((s) => s - 1);
  }, [currentStep]);

  const skip = useCallback(() => {
    localStorage.setItem("onboarding_completed", "true");
    router.push("/");
  }, [router]);

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === "Enter") goNext();
      if (e.key === "ArrowLeft") goPrev();
      if (e.key === "Escape") skip();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goNext, goPrev, skip]);

  const step = steps[currentStep];
  const StepComponent = step.component;

  // Build input props based on step
  const inputProps =
    step.id === "welcome"
      ? { userName: user?.display_name }
      : step.id === "tiers"
        ? { userTier: user?.tier }
        : {};

  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] px-4 py-8">
      {/* Player container — no border, seamless dark look */}
      <div className="w-full max-w-[1080px] aspect-video rounded-2xl overflow-hidden mb-8"
        style={{ boxShadow: "0 8px 48px rgba(0,0,0,0.5)" }}
      >
        <Player
          ref={playerRef}
          key={step.id}
          component={StepComponent as React.ComponentType<Record<string, unknown>>}
          inputProps={inputProps}
          durationInFrames={STEP_DURATION}
          compositionWidth={1920}
          compositionHeight={1080}
          fps={30}
          autoPlay
          style={{ width: "100%", height: "100%" }}
        />
      </div>

      {/* Step indicators */}
      <div className="flex items-center gap-3 mb-8">
        {steps.map((s, i) => (
          <button
            key={s.id}
            onClick={() => setCurrentStep(i)}
            className="group flex items-center gap-2"
          >
            <div
              className={`
                h-2 rounded-full transition-all duration-300
                ${i === currentStep
                  ? "w-8 bg-accent"
                  : i < currentStep
                    ? "w-2 bg-accent/40"
                    : "w-2 bg-border"
                }
              `}
            />
            {i === currentStep && (
              <span className="text-xs font-medium text-text-secondary">
                {locale === "de" ? s.titleDe : s.titleEn}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Navigation buttons */}
      <div className="flex items-center gap-4">
        {currentStep > 0 && (
          <button
            onClick={goPrev}
            className="px-6 py-2.5 text-sm font-medium text-text-secondary hover:text-text-primary border border-border rounded-lg transition-colors"
          >
            ← {locale === "de" ? "Zurück" : "Back"}
          </button>
        )}

        <button
          onClick={goNext}
          className="px-8 py-2.5 text-sm font-bold text-white bg-accent hover:bg-accent-hover rounded-lg transition-colors"
        >
          {isLast
            ? locale === "de"
              ? "Analyse starten"
              : "Start analyzing"
            : locale === "de"
              ? "Weiter →"
              : "Next →"}
        </button>

        {!isLast && (
          <button
            onClick={skip}
            className="px-4 py-2.5 text-xs text-text-tertiary hover:text-text-secondary transition-colors"
          >
            {locale === "de" ? "Überspringen" : "Skip"}
          </button>
        )}
      </div>

      {/* Keyboard hint */}
      <p className="mt-6 text-xs text-text-tertiary">
        {locale === "de"
          ? "← → Pfeiltasten zum Navigieren · Esc zum Überspringen"
          : "← → arrow keys to navigate · Esc to skip"}
      </p>
    </div>
  );
}
