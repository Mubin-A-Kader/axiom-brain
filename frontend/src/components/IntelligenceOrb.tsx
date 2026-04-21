"use client";

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Check, Database, Shield, Loader2, X, Terminal, 
  AlertCircle, Cpu, Activity, BarChart3, Fingerprint
} from 'lucide-react';
import { ReasoningStep } from '../types';

interface IntelligenceOrbProps {
  steps: ReasoningStep[];
  isCompleted: boolean;
  thought?: string;
  sql?: string;
  executionTime?: string;
  tokenCount?: number;
}

export function IntelligenceOrb({ 
  steps, 
  isCompleted, 
  thought, 
  sql, 
  executionTime = "0.8s", 
  tokenCount = 1240 
}: IntelligenceOrbProps) {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  const activeStep = steps.find(s => s.status === 'active') || steps[steps.length - 1];
  
  const getIcon = () => {
    if (isCompleted) return Check;
    if (!activeStep) return Cpu;
    const nodeName = activeStep.node.toLowerCase();
    if (nodeName.includes('sql')) return Database;
    if (nodeName.includes('critic') || nodeName.includes('guard')) return Shield;
    return Loader2;
  };

  const Icon = getIcon();

  return (
    <>
      <div 
        className="absolute -top-1.5 -left-3.5 z-20 cursor-pointer"
        onClick={(e) => {
          e.stopPropagation();
          setIsSidebarOpen(true);
        }}
      >
        <div className={`w-7 h-7 rounded-full flex items-center justify-center backdrop-blur-md border shadow-lg transition-all duration-300
          ${isCompleted 
            ? 'bg-[#638A70] border-[#638A70] text-[#1E1E1C]' 
            : 'bg-[#2A2927] border-[#638A70] text-[#638A70]'
          }
        `}>
          <Icon className={`w-3.5 h-3.5 ${!isCompleted && Icon === Loader2 ? 'animate-spin' : ''}`} />
        </div>
      </div>

      <AnimatePresence>
        {isSidebarOpen && (
          <>
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setIsSidebarOpen(false)}
              className="fixed inset-0 bg-black/40 z-[60]"
            />
            <motion.div 
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ type: 'tween', duration: 0.2 }}
              className="fixed top-0 right-0 h-full w-full max-w-sm bg-[#1E1E1C] border-l border-white/5 z-[70] flex flex-col overflow-hidden"
            >
              <div className="flex items-center justify-between p-6 border-b border-white/5 bg-[#2A2927]">
                <div className="flex items-center gap-3">
                  <Activity className="w-4 h-4 text-[#638A70]" />
                  <h2 className="text-[#E6E1D8] font-bold text-xs uppercase tracking-widest">Execution Trace</h2>
                </div>
                <button onClick={() => setIsSidebarOpen(false)} className="text-[#E6E1D8]/20 hover:text-white">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
                <div className="space-y-4">
                  {steps.map((step, idx) => {
                    const isActive = step.status === 'active';
                    const isDone = step.status === 'completed';
                    return (
                      <div key={idx} className="flex gap-4">
                        <div className="mt-1">
                          {isDone ? <Check className="w-3 h-3 text-[#638A70]" /> : 
                           isActive ? <Loader2 className="w-3 h-3 text-[#638A70] animate-spin" /> : 
                           <div className="w-3 h-3 rounded-full border border-white/10" />}
                        </div>
                        <div>
                          <div className={`text-[10px] font-mono font-bold uppercase ${isActive ? 'text-[#638A70]' : 'text-[#E6E1D8]/30'}`}>{step.node}</div>
                          <div className={`text-xs mt-0.5 ${isActive ? 'text-[#E6E1D8]' : 'text-[#E6E1D8]/40'}`}>{step.description}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {sql && (
                   <div className="space-y-2 pt-4 border-t border-white/5">
                     <span className="text-[#638A70] font-bold uppercase text-[9px] tracking-widest">Logical Query</span>
                     <div className="p-3 rounded-lg bg-[#2A2927] border border-white/5 overflow-x-auto">
                       <pre className="text-[10px] font-mono text-[#638A70]"><code>{sql}</code></pre>
                     </div>
                   </div>
                )}

                {thought && (
                   <div className="space-y-2 pt-4 border-t border-white/5">
                     <span className="text-[#638A70] font-bold uppercase text-[9px] tracking-widest">Analyst Thought</span>
                     <div className="text-xs text-[#E6E1D8]/50 font-mono leading-relaxed bg-[#2A2927] p-3 rounded-lg">
                       {thought}
                     </div>
                   </div>
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
