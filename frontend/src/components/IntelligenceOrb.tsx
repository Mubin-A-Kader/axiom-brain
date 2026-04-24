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
      <div className="flex items-center gap-2 mb-3 text-[#E6E1D8]/30">
        <Loader2 className="w-3 h-3 animate-spin text-[#638A70]" />
        <span className="text-[11px] font-mono">Thinking…</span>
      </div>
    );
  }

  if (steps.length === 0) return null;

  const activeStep = steps.find((s) => s.status === "active");
  const errorStep = steps.find((s) => s.status === "error");

  const headerLabel = errorStep
    ? "Failed"
    : !isCompleted && activeStep
    ? activeStep.description
    : `Reasoned in ${steps.length} step${steps.length !== 1 ? "s" : ""}`;

  return (
    <div className="mb-4">
      {/* Collapsed / expanded toggle row */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 text-left w-full group"
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-[#E6E1D8]/25 flex-shrink-0 group-hover:text-[#E6E1D8]/50 transition-colors" />
        ) : (
          <ChevronRight className="w-3 h-3 text-[#E6E1D8]/25 flex-shrink-0 group-hover:text-[#E6E1D8]/50 transition-colors" />
        )}

        {!isCompleted && !errorStep ? (
          <Loader2 className="w-3 h-3 animate-spin text-[#638A70] flex-shrink-0" />
        ) : errorStep ? (
          <X className="w-3 h-3 text-[#C26D5C] flex-shrink-0" />
        ) : (
          <Check className="w-3 h-3 text-[#638A70] flex-shrink-0" />
        )}

        <span className="text-[11px] font-mono text-[#E6E1D8]/40 group-hover:text-[#E6E1D8]/60 transition-colors">
          {headerLabel}
        </span>

        {isCompleted && sql && !expanded && (
          <span className="text-[10px] font-mono text-[#638A70]/40 ml-1">· SQL</span>
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
            transition={{ duration: 0.18, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            <div className="mt-3 ml-2 pl-4 border-l border-white/[0.06] space-y-4">
              {/* Steps list */}
              <div className="space-y-2">
                {steps.map((step, idx) => (
                  <div key={idx} className="flex items-start gap-3">
                    <div className="mt-[3px] flex-shrink-0 w-3">
                      {step.status === "completed" && <Check className="w-3 h-3 text-[#638A70]" />}
                      {step.status === "active" && <Loader2 className="w-3 h-3 text-[#638A70] animate-spin" />}
                      {step.status === "error" && <X className="w-3 h-3 text-[#C26D5C]" />}
                      {step.status !== "completed" && step.status !== "active" && step.status !== "error" && (
                        <div className="w-2 h-2 rounded-full border border-white/10 mt-0.5" />
                      )}
                    </div>
                    <div className="min-w-0">
                      <span className={`text-[10px] font-mono font-bold uppercase tracking-wider ${
                        step.status === "active" ? "text-[#638A70]" : "text-[#E6E1D8]/25"
                      }`}>
                        {step.node}
                      </span>
                      <span className={`text-[11px] ml-2 ${
                        step.status === "active" ? "text-[#E6E1D8]/70" : "text-[#E6E1D8]/30"
                      }`}>
                        {step.description}
                      </span>
                    </div>
                  </div>
                ))}
              </div>

              {/* SQL source */}
              {sql && (
                <div className="pt-3 border-t border-white/[0.06]">
                  <div className="text-[9px] font-mono font-bold uppercase tracking-widest text-[#638A70]/50 mb-2">
                    Generated SQL
                  </div>
                  <pre className="text-[11px] font-mono text-[#638A70]/80 bg-black/20 rounded-lg p-3 overflow-x-auto border border-white/[0.06] leading-relaxed whitespace-pre-wrap break-words">
                    <code>{sql}</code>
                  </pre>
                </div>
              )}

              {/* Agent thought */}
              {thought && (
                <div className="pt-3 border-t border-white/[0.06]">
                  <div className="text-[9px] font-mono font-bold uppercase tracking-widest text-[#638A70]/50 mb-2">
                    Reasoning
                  </div>
                  <p className="text-[11px] text-[#E6E1D8]/35 font-mono leading-relaxed">{thought}</p>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
