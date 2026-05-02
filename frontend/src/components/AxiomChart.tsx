"use client";

import React from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  AreaChart,
  Area,
} from "recharts";

interface ChartManifest {
  type: "bar" | "line" | "pie" | "area" | "scatter";
  title?: string;
  data: any[];
  x_axis: string;
  y_axis: string;
  colors?: string[];
}

const DEFAULT_COLORS = ["#638A70", "#C26D5C", "#A5A58D", "#6B705C", "#B7B7A4"];

export function AxiomChart({ manifest }: { manifest: ChartManifest }) {
  if (!manifest || !manifest.data || manifest.data.length === 0) {
    return null;
  }

  const colors = manifest.colors || DEFAULT_COLORS;

  const renderChart = () => {
    switch (manifest.type) {
      case "bar":
        return (
          <BarChart data={manifest.data} layout="vertical" margin={{ left: 40, right: 20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={false} />
            <XAxis type="number" hide />
            <YAxis
              dataKey={manifest.x_axis}
              type="category"
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgba(230,225,216,0.6)", fontSize: 11 }}
              width={100}
            />
            <Tooltip
              contentStyle={{ background: "#1E1E1C", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: "12px" }}
              itemStyle={{ color: "#E6E1D8" }}
              cursor={{ fill: "rgba(255,255,255,0.03)" }}
            />
            <Bar dataKey={manifest.y_axis} fill={colors[0]} radius={[0, 4, 4, 0]} barSize={24} />
          </BarChart>
        );
      case "line":
        return (
          <LineChart data={manifest.data}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis
              dataKey={manifest.x_axis}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgba(230,225,216,0.6)", fontSize: 11 }}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgba(230,225,216,0.6)", fontSize: 11 }}
            />
            <Tooltip
              contentStyle={{ background: "#1E1E1C", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: "12px" }}
            />
            <Line type="monotone" dataKey={manifest.y_axis} stroke={colors[0]} strokeWidth={2} dot={{ r: 4, fill: colors[0] }} />
          </LineChart>
        );
      case "pie":
        return (
          <PieChart>
            <Pie
              data={manifest.data}
              dataKey={manifest.y_axis}
              nameKey={manifest.x_axis}
              cx="50%"
              cy="50%"
              outerRadius={80}
              innerRadius={60}
              stroke="none"
            >
              {manifest.data.map((_, index) => (
                <Cell key={`cell-${index}`} fill={colors[index % colors.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{ background: "#1E1E1C", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: "12px" }}
            />
          </PieChart>
        );
      case "area":
        return (
          <AreaChart data={manifest.data}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey={manifest.x_axis} hide />
            <YAxis hide />
            <Tooltip
              contentStyle={{ background: "#1E1E1C", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: "12px" }}
            />
            <Area type="monotone" dataKey={manifest.y_axis} stroke={colors[0]} fill={colors[0]} fillOpacity={0.2} />
          </AreaChart>
        );
      default:
        return <div className="text-xs text-white/40 p-8 text-center">Chart type '{manifest.type}' not yet supported.</div>;
    }
  };

  return (
    <div className="w-full h-64 mt-4 bg-black/20 rounded-xl border border-white/5 p-4 overflow-hidden">
      {manifest.title && (
        <h4 className="text-[10px] font-bold uppercase tracking-widest text-white/40 mb-4">{manifest.title}</h4>
      )}
      <ResponsiveContainer width="100%" height="100%">
        {renderChart()}
      </ResponsiveContainer>
    </div>
  );
}
