"use client";

import React from 'react';
import ReactECharts from 'echarts-for-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ZoomIn, Sparkles, Download, Maximize2 } from 'lucide-react';
import * as echarts from 'echarts';
import { sanitizeChartData } from '../lib/DataSanitizer';
import { motion } from 'framer-motion';

interface AxiomCanvasProps {
  layout: string;
  insight?: string;
  visualization?: any;
  result?: any;
}

const MarkdownComponents: any = {
  strong: ({node, ...props}: any) => <strong className="font-semibold text-emerald-400" {...props} />,
  li: ({node, ...props}: any) => (
    <li className="flex items-start gap-2 my-1">
      <Sparkles className="w-3.5 h-3.5 mt-1 text-[#638A70] flex-shrink-0" />
      <span {...props} />
    </li>
  ),
  ul: ({node, ...props}: any) => <ul className="space-y-2 my-4 list-none pl-0" {...props} />,
  p: ({node, ...props}: any) => <p className="mb-4 leading-relaxed" {...props} />
};

export function AxiomCanvas({ layout, insight, visualization, result }: AxiomCanvasProps) {
  if (!insight && !visualization) return null;

  const isAnalyticsLayout = layout === "analytics" && visualization;

  const getChartOptions = () => {
    if (!visualization) return {};
    
    let chartData = [];
    const { x_axis, y_axis, plot_type, title } = visualization;
    const yAxisArray = Array.isArray(y_axis) ? y_axis : [y_axis];

    if (result && typeof result === "string") {
      try {
        const parsed = JSON.parse(result);
        chartData = sanitizeChartData(parsed, x_axis, yAxisArray);
      } catch (e) {
        console.error("Failed to parse chart data", e);
      }
    } else if (result && typeof result === "object") {
        chartData = sanitizeChartData(result, x_axis, yAxisArray);
    }

    return {
      animationDuration: 1200,
      animationEasing: 'exponentialOut',
      backgroundColor: 'transparent',
      title: {
        text: title,
        textStyle: { 
          color: '#E6E1D8', 
          fontSize: 18, 
          fontWeight: '600',
          fontFamily: 'Inter, system-ui, sans-serif' 
        },
        left: '0',
        top: '0'
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(30, 30, 28, 0.95)',
        borderColor: 'rgba(255, 255, 255, 0.05)',
        borderWidth: 1,
        borderRadius: 8,
        padding: [12, 16],
        textStyle: { color: '#E6E1D8', fontSize: 13 },
        shadowBlur: 20,
        shadowColor: 'rgba(0, 0, 0, 0.3)',
        axisPointer: {
          type: 'cross',
          lineStyle: { color: 'rgba(99, 138, 112, 0.3)', width: 1 }
        }
      },
      legend: {
        bottom: '0',
        left: 'center',
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: '#E6E1D8', opacity: 0.6, fontSize: 11 },
      },
      grid: { 
        left: '2%', 
        right: '2%', 
        bottom: '12%', 
        top: '15%',
        containLabel: true 
      },
      xAxis: {
        type: 'category',
        axisLabel: { color: '#E6E1D8', opacity: 0.4, fontSize: 10, margin: 15 },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
        axisTick: { show: false },
        data: chartData.map((d: any) => d[x_axis]),
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: '#E6E1D8', opacity: 0.4, fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.03)', type: 'dashed' } },
      },
      series: yAxisArray.map((yCol: string, idx: number) => {
        const isTerracotta = idx % 2 !== 0;
        const mainColor = isTerracotta ? '#C26D5C' : '#638A70';
        const gradColor = isTerracotta ? 'rgba(194, 109, 92, 0.1)' : 'rgba(99, 138, 112, 0.1)';

        return {
          name: yCol,
          type: plot_type === 'histogram' ? 'bar' : (plot_type || 'bar'),
          data: chartData.map((d: any) => d[yCol]),
          smooth: 0.4,
          symbolSize: 6,
          showSymbol: false,
          emphasis: { 
            focus: 'series',
            itemStyle: { shadowBlur: 10, shadowColor: mainColor }
          },
          itemStyle: {
            color: mainColor,
            borderRadius: plot_type === 'bar' ? [4, 4, 0, 0] : 0
          },
          areaStyle: plot_type === 'area' ? {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: mainColor + '44' },
              { offset: 1, color: mainColor + '00' }
            ])
          } : undefined,
          lineStyle: { width: 3, cap: 'round' }
        };
      }),
    };
  };

  return (
    <motion.div 
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.8, ease: [0.32, 0.72, 0, 1] }}
      className={`space-y-6 ${isAnalyticsLayout ? 'grid grid-cols-1 xl:grid-cols-[65%_35%] gap-6' : ''}`}
    >
      {/* Chart Section */}
      {visualization && (
        <div className="bg-[#2A2927]/40 border border-[rgba(255,255,255,0.05)] rounded-2xl p-8 shadow-[0_20px_50px_rgba(0,0,0,0.3)] relative group backdrop-blur-xl">
          <div className="absolute top-6 right-6 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
            <button className="p-2 rounded-lg bg-[#1E1E1C] border border-white/5 text-white/40 hover:text-white transition-colors">
              <Download className="w-4 h-4" />
            </button>
            <button className="p-2 rounded-lg bg-[#1E1E1C] border border-white/5 text-white/40 hover:text-white transition-colors">
              <Maximize2 className="w-4 h-4" />
            </button>
          </div>
          
          <ReactECharts
            option={getChartOptions()}
            style={{ height: '450px', width: '100%' }}
            theme="dark"
            opts={{ renderer: 'canvas' }}
          />
        </div>
      )}

      {/* Summary Section */}
      {insight && (
        <div className="bg-[#1E1E1C]/60 border border-[rgba(255,255,255,0.05)] rounded-2xl p-8 shadow-xl backdrop-blur-md">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-8 h-8 rounded-lg bg-[#638A70]/10 flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-[#638A70]" />
            </div>
            <h3 className="text-[#E6E1D8] font-semibold text-sm tracking-widest uppercase">Axiom Insights</h3>
          </div>
          <div className="prose prose-sm prose-zinc prose-invert max-w-none text-[#E6E1D8]/80 leading-relaxed space-y-4">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
              {insight}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </motion.div>
  );
}
