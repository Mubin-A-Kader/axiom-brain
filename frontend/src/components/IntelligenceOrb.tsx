"use client";

import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Check, ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import { ReasoningStep } from "../types";

interface IntelligenceOrbProps {
  steps: ReasoningStep[];
  isCompleted: boolean;
  thought?: string;
  sql?: string;
}

export function IntelligenceOrb({ steps, isCompleted, thought, sql }: IntelligenceOrbProps) {
  // Open while running, collapse once done
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    if (!isCompleted) {
      setExpanded(true);
    } else {
      const t = setTimeout(() => setExpanded(false), 800);
      return () => clearTimeout(t);
    }
  }, [isCompleted]);

  if (steps.length === 0 && !isCompleted) {
    return (
      <div className="flex items-center gap-4 mb-6 text-tactile-text/30">
        <Loader2 className="w-4 h-4 animate-spin text-tactile-primary" />
        <span className="text-sm font-mono font-bold tracking-[0.2em] uppercase">Synthesizing intelligence…</span>
      </div>
    );
  }

  const activeStep = steps.find((s) => s.status === "active");
  const errorStep = steps.find((s) => s.status === "error");

  const headerLabel = errorStep
    ? "Execution Failed"
    : !isCompleted && activeStep
    ? activeStep.description
    : steps.length > 0 
      ? `Synthesized in ${steps.length} cognitive step${steps.length !== 1 ? "s" : ""}`
      : "Analysis Completed";

  return (
    <div className="mb-8">
      {/* Collapsed / expanded toggle row */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-4 text-left w-full group py-1"
      >
        <div className="flex items-center justify-center w-5 h-5">
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-tactile-text/20 group-hover:text-tactile-text/50 transition-colors" />
          ) : (
            <ChevronRight className="w-4 h-4 text-tactile-text/20 group-hover:text-tactile-text/50 transition-colors" />
          )}
        </div>

        <div className="flex items-center justify-center w-5 h-5">
          {!isCompleted && !errorStep ? (
            <Loader2 className="w-4 h-4 animate-spin text-tactile-primary" />
          ) : errorStep ? (
            <X className="w-4 h-4 text-tactile-warning" />
          ) : (
            <Check className="w-4 h-4 text-tactile-primary" />
          )}
        </div>

        <span className="text-[11px] font-mono font-bold text-tactile-text/40 group-hover:text-tactile-primary transition-colors uppercase tracking-[0.2em]">
          {headerLabel}
        </span>

        {isCompleted && sql && !expanded && (
          <div className="flex items-center gap-3 ml-4 animate-in fade-in slide-in-from-left-2 duration-500">
            <div className="w-1 h-1 rounded-full bg-tactile-primary/40 shadow-[0_0_8px_rgba(125,163,139,0.4)]" />
            <span className="text-[10px] font-mono font-bold text-tactile-primary uppercase tracking-[0.2em] px-2.5 py-1 bg-tactile-primary/10 rounded-lg border border-tactile-primary/20 shadow-tactile-inner">Artifact_Ready</span>
          </div>
        )}
      </button>

      {/* Expandable body */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.23, 1, 0.32, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-6 ml-2.5 pl-8 border-l border-tactile-border space-y-8">
              {/* Steps list */}
              <div className="space-y-4">
                {steps.map((step, idx) => (
                  <div key={idx} className="flex items-start gap-5 group/step">
                    <div className="mt-[6px] flex-shrink-0 w-4 flex justify-center">
                      {step.status === "completed" && <Check className="w-3.5 h-3.5 text-tactile-primary shadow-[0_0_8px_rgba(125,163,139,0.2)]" />}
                      {step.status === "active" && <Loader2 className="w-3.5 h-3.5 text-tactile-primary animate-spin" />}
                      {step.status === "error" && <X className="w-3.5 h-3.5 text-tactile-warning" />}
                      {step.status !== "completed" && step.status !== "active" && step.status !== "error" && (
                        <div className="w-2 h-2 rounded-full border border-tactile-border mt-1" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-4">
                        <span className={`text-[10px] font-mono font-bold uppercase tracking-[0.2em] transition-colors ${
                          step.status === "active" ? "text-tactile-primary" : "text-tactile-text/20 group-hover/step:text-tactile-text/40"
                        }`}>
                          {step.node}
                        </span>
                        <span className={`text-sm leading-relaxed transition-colors ${
                          step.status === "active" ? "text-tactile-text font-medium" : "text-tactile-text/40"
                        }`}>
                          {step.description}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {/* SQL source */}
              {sql && (
                <div className="pt-8 border-t border-tactile-border">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-1.5 h-1.5 rounded-full bg-tactile-primary" />
                    <div className="text-[10px] font-mono font-bold uppercase tracking-[0.3em] text-tactile-primary">
                      Generated_Query
                    </div>
                  </div>
                  <pre className="text-[13px] font-mono text-tactile-primary/90 bg-tactile-base border border-tactile-border rounded-2xl p-6 overflow-x-auto leading-relaxed whitespace-pre-wrap break-words shadow-tactile-inner">
                    <code>{sql}</code>
                  </pre>
                </div>
              )}

              {/* Agent thought */}
              {thought && (
                <div className="pt-8 border-t border-tactile-border">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-1.5 h-1.5 rounded-full bg-tactile-text/20" />
                    <div className="text-[10px] font-mono font-bold uppercase tracking-[0.3em] text-tactile-text/30">
                      Reasoning_Log
                    </div>
                  </div>
                  <p className="text-sm text-tactile-text/40 font-sans leading-relaxed italic px-2">{thought}</p>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
