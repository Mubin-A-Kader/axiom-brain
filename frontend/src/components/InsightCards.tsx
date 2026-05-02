"use client";

import React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

interface InsightKPI {
  label: string;
  value: string;
  trend?: "up" | "down" | "neutral";
}

export function InsightCards({ kpis }: { kpis: InsightKPI[] }) {
  if (!kpis || kpis.length === 0) return null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3 mb-6">
      {kpis.map((kpi, i) => (
        <div 
          key={i} 
          className="bg-[#1E1E1C] border border-white/5 rounded-xl p-4 flex flex-col gap-1 shadow-sm hover:border-white/10 transition-colors"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[#E6E1D8]/40 truncate">
              {kpi.label}
            </span>
            {kpi.trend === "up" && <TrendingUp className="w-3 h-3 text-[#638A70]" />}
            {kpi.trend === "down" && <TrendingDown className="w-3 h-3 text-[#C26D5C]" />}
            {kpi.trend === "neutral" && <Minus className="w-3 h-3 text-white/20" />}
          </div>
          <span className="text-xl font-medium tracking-tight text-[#E6E1D8]">
            {kpi.value}
          </span>
        </div>
      ))}
    </div>
  );
}
