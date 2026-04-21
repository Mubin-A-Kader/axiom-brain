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
    <div className="border border-[rgba(255,255,255,0.05)] rounded-lg bg-[#2A2927] overflow-hidden flex flex-col shadow-[0_4px_12px_rgba(0,0,0,0.2)] relative pb-12">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader className="bg-[#1E1E1C] sticky top-0 z-10 border-b border-[rgba(255,255,255,0.05)]">
            <TableRow className="border-0 hover:bg-transparent">
              {data.columns.map((col: string, i: number) => (
                <TableHead key={i} className="font-semibold text-[#E6E1D8] py-4 whitespace-nowrap text-xs uppercase">
                  {col}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rowsToDisplay.map((row: any[], i: number) => (
              <TableRow key={i} className="border-[rgba(255,255,255,0.05)] hover:bg-[#32312F] transition-colors">
                {row.map((cell: any, j: number) => {
                  const isNumber = typeof cell === 'number';
                  return (
                    <TableCell 
                      key={j} 
                      className={`py-3 text-[#E6E1D8] font-mono text-sm ${isNumber ? 'text-right' : ''}`}
                    >
                      {cell !== null ? String(cell) : <span className="text-[#E6E1D8]/50 italic">null</span>}
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
        <div className="absolute bottom-12 left-1/2 transform -translate-x-1/2 flex items-center gap-2 p-1.5 bg-[#1E1E1C]/80 backdrop-blur-xl border border-[rgba(255,255,255,0.1)] rounded-full shadow-[0_4px_24px_rgba(0,0,0,0.5)] z-20">
          <Sparkles className="w-3 h-3 text-[#638A70] ml-2" />
          {actionBar.map((action, idx) => (
             <button
                key={idx}
                onClick={() => onActionClick && onActionClick(action)}
                className="px-3 py-1.5 text-xs font-medium text-[#E6E1D8] bg-[#2A2927] hover:bg-[#638A70] hover:text-[#1E1E1C] rounded-full transition-colors whitespace-nowrap cursor-pointer"
             >
               {action}
             </button>
          ))}
        </div>
      )}

      <div className="bg-[#1E1E1C] px-4 py-3 border-t border-[rgba(255,255,255,0.05)] text-xs font-mono text-[#E6E1D8]/70 flex justify-between items-center absolute bottom-0 w-full">
        <div className="flex items-center gap-4">
          <span>{showAll ? data.total_count || data.rows.length : Math.min(data.rows.length, 15)} OF {data.total_count || data.rows.length} RECORD(S)</span>
          {isTruncated && !showAll && (
            <button 
              onClick={() => setShowAll(true)}
              className="text-[#638A70] hover:underline font-bold uppercase tracking-widest cursor-pointer"
            >
              Show all data
            </button>
          )}
        </div>
        <span className="flex items-center gap-2"><Activity className="w-3 h-3 text-[#638A70]" /> LIVE</span>
      </div>
    </div>
  );
}