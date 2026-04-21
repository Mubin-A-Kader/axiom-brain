import React, { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, Loader2, AlertCircle } from 'lucide-react';
import { ReasoningStep } from '../types';
import { motion, AnimatePresence } from 'framer-motion';

interface ReasoningStreamProps {
  steps: ReasoningStep[];
  isCompleted: boolean;
  thought?: string;
  sql?: string;
  executionTime?: string;
  tokenCount?: number;
}

export function ReasoningStream({ steps, isCompleted, thought, sql, executionTime = "1.2s", tokenCount = 1450 }: ReasoningStreamProps) {
  const [isExpanded, setIsExpanded] = useState(!isCompleted);

  // If completed but expanded is false, show summary
  if (isCompleted && !isExpanded) {
    return (
      <div 
        onClick={() => setIsExpanded(true)}
        className="flex items-center justify-between text-xs font-mono text-[#E6E1D8]/50 hover:text-[#638A70] transition-colors cursor-pointer mb-4 select-none bg-zinc-950/40 p-3 rounded-lg border border-[rgba(255,255,255,0.05)] shadow-sm"
      >
        <div className="flex items-center gap-2">
          <ChevronRight className="w-3.5 h-3.5" />
          <span>Analyzed {steps.length} operation steps in Orchestrator. Click to expand.</span>
        </div>
        <div className="flex items-center gap-4 text-[#E6E1D8]/30">
          <span>{executionTime}</span>
          <span>{tokenCount} tokens</span>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-zinc-950/40 backdrop-blur-2xl border-l-[3px] border-[#638A70]/30 rounded-r-lg p-5 shadow-inner mb-6 relative overflow-hidden transition-all">
      {/* Background glow for active thought */}
      {!isCompleted && (
        <div className="absolute top-0 right-0 w-32 h-32 bg-[#638A70]/10 blur-[40px] rounded-full animate-pulse" />
      )}
      
      <div 
        className="flex items-center justify-between cursor-pointer mb-4 select-none"
        onClick={() => isCompleted && setIsExpanded(false)}
      >
        <div className="flex items-center gap-2 text-xs font-bold font-mono tracking-widest uppercase text-[#E6E1D8]/70">
          <span className="w-1.5 h-1.5 rounded-full bg-[#638A70] animate-pulse" />
          Reasoning Stream
        </div>
        {isCompleted && <ChevronDown className="w-4 h-4 text-[#E6E1D8]/40" />}
      </div>

      <div className="space-y-4 relative z-10">
        <AnimatePresence>
          {steps.map((step, idx) => {
            const isActive = step.status === 'active';
            const isError = step.status === 'error';
            const isDone = step.status === 'completed';

            return (
              <motion.div 
                key={idx}
                initial={{ opacity: 0, y: 10, filter: 'blur(4px)' }}
                animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                className="flex items-start gap-3"
              >
                <div className="mt-0.5">
                  {isDone ? (
                    <CheckCircle2 className="w-4 h-4 text-[#638A70]" />
                  ) : isError ? (
                    <AlertCircle className="w-4 h-4 text-[#C26D5C]" />
                  ) : (
                    <Loader2 className="w-4 h-4 text-[#E6E1D8]/50 animate-spin" />
                  )}
                </div>
                <div>
                  <div className={`text-xs font-mono font-bold tracking-tight uppercase ${isActive ? 'text-[#E6E1D8]' : 'text-[#E6E1D8]/50'}`}>
                    {step.node}
                  </div>
                  <div className={`text-sm ${isActive ? 'text-[#E6E1D8]/90' : 'text-[#E6E1D8]/40'}`}>
                    {step.description}
                  </div>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>

        {thought && isCompleted && (
           <motion.div 
             initial={{ opacity: 0 }}
             animate={{ opacity: 1 }}
             className="mt-6 pt-4 border-t border-[rgba(255,255,255,0.05)] text-sm text-[#E6E1D8]/80 font-mono whitespace-pre-wrap leading-relaxed"
           >
             <span className="text-[#638A70] font-bold block mb-2 uppercase text-xs tracking-widest">Internal Monologue</span>
             {thought}
           </motion.div>
        )}

        {sql && isCompleted && (
           <motion.div 
             initial={{ opacity: 0 }}
             animate={{ opacity: 1 }}
             className="mt-4 pt-4 border-t border-[rgba(255,255,255,0.05)] text-sm font-mono"
           >
             <span className="text-[#638A70] font-bold block mb-2 uppercase text-xs tracking-widest">Generated SQL</span>
             <div className="p-3 rounded-md bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] overflow-x-auto shadow-inner text-[#E6E1D8]">
               <code>{sql}</code>
             </div>
           </motion.div>
        )}
      </div>
    </div>
  );
}