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
      const t = setTimeout(() => setExpanded(false), 600);
      return () => clearTimeout(t);
    }
  }, [isCompleted]);

  if (steps.length === 0 && !isCompleted) {
    return (
      <div className="flex items-center gap-3 mb-4 text-[#E6E1D8]/40">
        <Loader2 className="w-4 h-4 animate-spin text-[#638A70]" />
        <span className="text-sm font-mono font-medium">Synthesizing intelligence…</span>
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
    <div className="mb-6">
      {/* Collapsed / expanded toggle row */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-3 text-left w-full group"
      >
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-[#E6E1D8]/30 flex-shrink-0 group-hover:text-[#E6E1D8]/60 transition-colors" />
        ) : (
          <ChevronRight className="w-4 h-4 text-[#E6E1D8]/30 flex-shrink-0 group-hover:text-[#E6E1D8]/60 transition-colors" />
        )}

        {!isCompleted && !errorStep ? (
          <Loader2 className="w-4 h-4 animate-spin text-[#638A70] flex-shrink-0" />
        ) : errorStep ? (
          <X className="w-4 h-4 text-[#C26D5C] flex-shrink-0" />
        ) : (
          <Check className="w-4 h-4 text-[#638A70] flex-shrink-0" />
        )}

        <span className="text-sm font-mono font-bold text-[#E6E1D8]/50 group-hover:text-[#638A70] transition-colors uppercase tracking-wider">
          {headerLabel}
        </span>

        {isCompleted && sql && !expanded && (
          <div className="flex items-center gap-1.5 ml-2">
            <div className="w-1 h-1 rounded-full bg-[#E6E1D8]/10" />
            <span className="text-[10px] font-mono font-bold text-[#638A70]/60 uppercase tracking-widest px-2 py-0.5 bg-[#638A70]/10 rounded">SQL Generated</span>
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
            transition={{ duration: 0.25, ease: [0.23, 1, 0.32, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-4 ml-2 pl-6 border-l-2 border-white/[0.05] space-y-6">
              {/* Steps list */}
              <div className="space-y-3">
                {steps.map((step, idx) => (
                  <div key={idx} className="flex items-start gap-4">
                    <div className="mt-[5px] flex-shrink-0 w-4">
                      {step.status === "completed" && <Check className="w-4 h-4 text-[#638A70]" />}
                      {step.status === "active" && <Loader2 className="w-4 h-4 text-[#638A70] animate-spin" />}
                      {step.status === "error" && <X className="w-4 h-4 text-[#C26D5C]" />}
                      {step.status !== "completed" && step.status !== "active" && step.status !== "error" && (
                        <div className="w-2.5 h-2.5 rounded-full border border-white/20 mt-1" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-3">
                        <span className={`text-[11px] font-mono font-bold uppercase tracking-[0.15em] ${
                          step.status === "active" ? "text-[#638A70]" : "text-[#E6E1D8]/30"
                        }`}>
                          {step.node}
                        </span>
                        <span className={`text-sm leading-relaxed ${
                          step.status === "active" ? "text-[#E6E1D8]" : "text-[#E6E1D8]/40"
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
                <div className="pt-5 border-t border-white/[0.05]">
                  <div className="text-[10px] font-mono font-bold uppercase tracking-[0.2em] text-[#638A70] mb-3">
                    Optimized Query
                  </div>
                  <pre className="text-[13px] font-mono text-[#638A70]/90 bg-black/40 rounded-xl p-5 overflow-x-auto border border-white/[0.05] leading-relaxed whitespace-pre-wrap break-words shadow-inner">
                    <code>{sql}</code>
                  </pre>
                </div>
              )}

              {/* Agent thought */}
              {thought && (
                <div className="pt-5 border-t border-white/[0.05]">
                  <div className="text-[10px] font-mono font-bold uppercase tracking-[0.2em] text-[#638A70]/60 mb-3">
                    Internal Reasoning
                  </div>
                  <p className="text-sm text-[#E6E1D8]/40 font-mono leading-relaxed italic">{thought}</p>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
