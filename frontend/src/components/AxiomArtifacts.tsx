"use client";

import React from 'react';
import { AxiomCanvas } from './AxiomCanvas';
import { DataTable } from './DataTable';
import { motion, AnimatePresence } from 'framer-motion';

interface AxiomArtifactsProps {
  message: any;
  onActionClick: (action: string) => void;
}

export function AxiomArtifacts({ message, onActionClick }: AxiomArtifactsProps) {
  if (!message || (!message.metadata?.visualization && !message.metadata?.result)) return null;

  const hasVisualization = !!message.metadata?.visualization;
  const hasResult = !!message.metadata?.result;

  return (
    <div className="h-full flex flex-col space-y-8 overflow-y-auto pr-2 custom-scrollbar pb-20">
      <AnimatePresence mode="wait">
        <motion.div
          key={message.id}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.3 }}
          className="space-y-12"
        >
          {hasVisualization && (
             <AxiomCanvas 
               layout="default"
               insight={message.metadata.insight}
               visualization={message.metadata.visualization}
               result={message.metadata.result}
             />
          )}
          
          {hasResult && (
            <div className="space-y-4">
               <div className="flex items-center gap-3 px-2">
                 <div className="w-1.5 h-1.5 rounded-full bg-[#638A70]" />
                 <h3 className="text-[10px] font-mono font-bold text-[#E6E1D8]/30 uppercase tracking-[0.2em]">Source Records</h3>
               </div>
               <div className="bg-[#1E1E1C] border border-white/5 rounded-xl overflow-hidden shadow-xl">
                 <DataTable 
                   result={message.metadata.result} 
                   actionBar={message.metadata.action_bar}
                   onActionClick={onActionClick}
                 />
               </div>
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
