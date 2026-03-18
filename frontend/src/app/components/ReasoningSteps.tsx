"use client";

import { Step } from "../lib/types";

interface ReasoningStepsProps {
  steps: Step[];
  isActive: boolean;
}

export default function ReasoningSteps({ steps, isActive }: ReasoningStepsProps) {
  if (steps.length === 0) return null;

  return (
    <div className="w-full mb-5 glass-card overflow-hidden animate-fade-in">
      {/* Status bar */}
      <div className="flex items-center gap-2.5 px-4 py-2.5 border-b border-[var(--glass-inner-border)]">
        <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${isActive ? "bg-accent animate-blink" : "bg-success"}`} />
        <span className="text-xs font-medium text-text-secondary">
          {isActive ? "Analyse läuft" : "Abgeschlossen"}
        </span>
      </div>

      {/* Log */}
      <div className="px-4 py-3 max-h-52 overflow-y-auto space-y-0.5 font-mono text-[11.5px]">
        {steps.map((step, i) => (
          <div key={step.id + i} className="animate-fade-in flex gap-2.5 text-text-secondary leading-5">
            <span className="shrink-0 text-text-tertiary select-none w-5 text-right">
              {step.phase.replace("Phase ", "")}
            </span>
            <span>
              <span className="text-text-primary">{step.agent}</span>
              <span className="text-text-tertiary"> — </span>
              {step.message}
              {step.status === "running" && (
                <span className="animate-blink text-accent ml-0.5">_</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
