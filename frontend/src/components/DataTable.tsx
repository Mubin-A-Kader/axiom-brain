import React, { useState } from "react";
import { Activity, Sparkles } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function DataTable({ result, actionBar, onActionClick }: { result: any; actionBar?: string[]; onActionClick?: (action: string) => void }) {
  const [showAll, setShowAll] = useState(false);
  let data = result;
  if (typeof result === 'string') {
    try {
      data = JSON.parse(result);
    } catch (e) {
      return (
        <div className="bg-tactile-warning/10 border-l-4 border-tactile-warning text-tactile-text p-4 rounded-r-lg shadow-tactile font-medium">
          Failed to parse result data.
        </div>
      );
    }
  }

  if (!data.columns || !data.rows || data.rows.length === 0) {
    return (
      <div className="px-8 py-12 tactile-card text-center text-sm text-tactile-text/50">
        <Activity className="w-8 h-8 text-tactile-primary/20 mx-auto mb-4" />
        Query returned no results.
      </div>
    );
  }

  const rowsToDisplay = showAll ? data.rows : data.rows.slice(0, 15);
  const isTruncated = data.is_truncated || (data.rows.length > 15 && !showAll);

  return (
    <div className="tactile-card overflow-hidden flex flex-col relative pb-16">
      <div className="overflow-x-auto custom-scrollbar">
        <Table>
          <TableHeader className="bg-tactile-base sticky top-0 z-10 border-b border-tactile-border">
            <TableRow className="border-0 hover:bg-transparent">
              {data.columns.map((col: string, i: number) => (
                <TableHead key={i} className="font-bold text-tactile-text py-5 px-6 whitespace-nowrap text-[10px] uppercase tracking-[0.3em]">
                  {col}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rowsToDisplay.map((row: any[], i: number) => (
              <TableRow key={i} className="border-tactile-border hover:bg-white/[0.015] transition-colors">
                {row.map((cell: any, j: number) => {
                  const isNumber = typeof cell === 'number';
                  return (
                    <TableCell 
                      key={j} 
                      className={`py-4 px-6 text-tactile-text/80 font-mono text-sm leading-relaxed ${isNumber ? 'text-right text-tactile-primary font-bold' : ''}`}
                    >
                      {cell !== null ? String(cell) : <span className="text-tactile-text/10 italic">null</span>}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Floating Action Bar */}
      {actionBar && actionBar.length > 0 && (
        <div className="absolute bottom-20 left-1/2 transform -translate-x-1/2 flex items-center gap-4 p-2.5 bg-tactile-base/90 backdrop-blur-md border border-tactile-primary/30 rounded-2xl shadow-2xl z-20 animate-in slide-in-from-bottom-4 duration-500">
          <div className="bg-tactile-primary/10 p-2.5 rounded-xl ml-1 shadow-tactile-inner">
            <Sparkles className="w-4 h-4 text-tactile-primary shadow-[0_0_10px_rgba(125,163,139,0.4)]" />
          </div>
          <div className="flex gap-2 pr-1">
            {actionBar.map((action, idx) => (
               <button
                  key={idx}
                  onClick={() => onActionClick && onActionClick(action)}
                  className="px-5 py-2 text-[10px] font-bold uppercase tracking-widest text-tactile-text bg-tactile-surface hover:bg-tactile-primary hover:text-tactile-base border border-tactile-border hover:border-tactile-primary rounded-xl transition-all whitespace-nowrap cursor-pointer active:scale-95 shadow-tactile"
               >
                 {action}
               </button>
            ))}
          </div>
        </div>
      )}

      <div className="bg-tactile-base px-8 py-5 border-t border-tactile-border text-[10px] font-mono font-bold text-tactile-text/30 flex justify-between items-center absolute bottom-0 w-full uppercase tracking-[0.2em]">
        <div className="flex items-center gap-8">
          <span className="text-tactile-primary/60">{showAll ? data.total_count || data.rows.length : Math.min(data.rows.length, 15)} / {data.total_count || data.rows.length} RECORDS</span>
          {isTruncated && !showAll && (
            <button 
              onClick={() => setShowAll(true)}
              className="text-tactile-text hover:text-tactile-primary transition-colors cursor-pointer border-b border-tactile-text/20 hover:border-tactile-primary"
            >
              Show all data
            </button>
          )}
        </div>
        <div className="flex items-center gap-4">
          <div className="w-2 h-2 rounded-full bg-tactile-primary animate-pulse shadow-[0_0_8px_rgba(125,163,139,0.4)]" />
          <span className="tracking-[0.1em]">INTELLIGENT_VIEW_READY</span>
        </div>
      </div>
    </div>
  );
}