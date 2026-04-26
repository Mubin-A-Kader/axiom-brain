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
      return <div className="tactile-warning">Failed to parse result data.</div>;
    }
  }

  if (!data.columns || !data.rows || data.rows.length === 0) {
    return (
      <div className="px-6 py-8 border border-[rgba(255,255,255,0.05)] rounded-lg bg-[#2A2927] text-center text-sm text-[#E6E1D8]/70 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
        Query returned no results.
      </div>
    );
  }

  const rowsToDisplay = showAll ? data.rows : data.rows.slice(0, 15);
  const isTruncated = data.is_truncated || (data.rows.length > 15 && !showAll);
  
  return (
    <div className="border border-white/5 rounded-2xl bg-[#2A2927] overflow-hidden flex flex-col shadow-2xl relative pb-14">
      <div className="overflow-x-auto custom-scrollbar">
        <Table>
          <TableHeader className="bg-[#1E1E1C] sticky top-0 z-10 border-b-2 border-white/5">
            <TableRow className="border-0 hover:bg-transparent">
              {data.columns.map((col: string, i: number) => (
                <TableHead key={i} className="font-bold text-[#E6E1D8] py-5 whitespace-nowrap text-[11px] uppercase tracking-[0.2em]">
                  {col}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rowsToDisplay.map((row: any[], i: number) => (
              <TableRow key={i} className="border-white/5 hover:bg-white/[0.02] transition-colors">
                {row.map((cell: any, j: number) => {
                  const isNumber = typeof cell === 'number';
                  return (
                    <TableCell 
                      key={j} 
                      className={`py-4 px-6 text-[#E6E1D8]/90 font-mono text-base ${isNumber ? 'text-right text-[#638A70]' : ''}`}
                    >
                      {cell !== null ? String(cell) : <span className="text-[#E6E1D8]/20 italic">null</span>}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Floating Action Bar - Pandas Middlewware triggers */}
      {actionBar && actionBar.length > 0 && (
        <div className="absolute bottom-16 left-1/2 transform -translate-x-1/2 flex items-center gap-3 p-2 bg-[#1E1E1C] border border-[#638A70]/20 rounded-2xl shadow-2xl z-20">
          <div className="bg-[#638A70]/10 p-2 rounded-xl ml-1">
            <Sparkles className="w-4 h-4 text-[#638A70]" />
          </div>
          <div className="flex gap-2 pr-1">
            {actionBar.map((action, idx) => (
               <button
                  key={idx}
                  onClick={() => onActionClick && onActionClick(action)}
                  className="px-4 py-2 text-xs font-bold uppercase tracking-widest text-[#E6E1D8] bg-[#2A2927] hover:bg-[#638A70] hover:text-[#1E1E1C] rounded-xl transition-all whitespace-nowrap cursor-pointer active:scale-95"
               >
                 {action}
               </button>
            ))}
          </div>
        </div>
      )}

      <div className="bg-[#1E1E1C] px-6 py-4 border-t border-white/5 text-[11px] font-mono font-bold text-[#E6E1D8]/40 flex justify-between items-center absolute bottom-0 w-full uppercase tracking-widest">
        <div className="flex items-center gap-6">
          <span>{showAll ? data.total_count || data.rows.length : Math.min(data.rows.length, 15)} / {data.total_count || data.rows.length} RECORDS</span>
          {isTruncated && !showAll && (
            <button 
              onClick={() => setShowAll(true)}
              className="text-[#638A70] hover:text-[#729E81] transition-colors cursor-pointer"
            >
              Show all data
            </button>
          )}
        </div>
        <div className="flex items-center gap-3">
          <div className="w-1.5 h-1.5 rounded-full bg-[#638A70] animate-pulse" />
          <span>Real-time Source View</span>
        </div>
      </div>
    </div>
  );
  }