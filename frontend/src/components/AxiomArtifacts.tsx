"use client";

import React from 'react';
import { DataTable } from './DataTable';
import { NotebookArtifactViewer } from './NotebookArtifactViewer';
import { motion, AnimatePresence } from 'framer-motion';
import { ChatMessage } from '../types';

interface AxiomArtifactsProps {
  message?: ChatMessage;
  onActionClick: (action: string) => void;
}

export function AxiomArtifacts({ message, onActionClick }: AxiomArtifactsProps) {
  if (!message || (!message.metadata?.artifact && !message.metadata?.result)) return null;

  const artifact = message.metadata?.artifact;
  const hasArtifact = !!artifact;
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
          {hasArtifact && (
             <NotebookArtifactViewer artifact={artifact} />
          )}
          
          {hasResult && (
            <div className="space-y-6">
               <div className="flex items-center gap-4 px-4">
                 <div className="w-2 h-2 rounded-full bg-[#638A70] shadow-[0_0_10px_rgba(99,138,112,0.5)]" />
                 <h3 className="text-xs font-mono font-bold text-[#638A70] uppercase tracking-[0.3em]">Source Intelligence Records</h3>
               </div>
               <div className="bg-[#1E1E1C] border border-white/5 rounded-2xl overflow-hidden shadow-2xl">
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
