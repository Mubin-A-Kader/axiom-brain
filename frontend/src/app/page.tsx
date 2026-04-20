"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { 
  Database, Check, X, Copy, 
  Terminal, Activity, LayoutDashboard, Settings, 
  CornerDownLeft, Loader2, AlertCircle, ChevronRight, ChevronDown, Search, Network, Sparkles, Cpu
} from "lucide-react";
import { useAxiomChat } from "../hooks/useAxiomChat";
import { createClient } from "@/lib/supabase/client";
import { Source } from "../types";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// Soft, tactile button
function TactileButton({ children, onClick, className = "", disabled = false, variant = "primary" }: any) {
  const baseStyles = "px-5 py-2.5 rounded-md font-semibold text-sm transition-all duration-200 ease-out flex items-center justify-center cursor-pointer select-none";
  const variants = {
    primary: "bg-[#638A70] text-[#1E1E1C] hover:bg-[#729E81] shadow-[0_4px_12px_rgba(0,0,0,0.2)]",
    destructive: "bg-[#C26D5C] text-white hover:bg-[#D47E6D] shadow-[0_4px_12px_rgba(0,0,0,0.2)]",
    outline: "bg-transparent border border-[rgba(255,255,255,0.05)] text-[#E6E1D8] hover:bg-white/5"
  };

  return (
    <button
      onClick={(e) => {
        e.preventDefault();
        if (!disabled && onClick) onClick();
      }}
      disabled={disabled}
      className={`${baseStyles} ${variants[variant as keyof typeof variants]} ${disabled ? 'opacity-50 cursor-not-allowed shadow-none' : 'hover:-translate-y-[1px] hover:shadow-lg active:translate-y-0 active:shadow-md'} ${className}`}
    >
      {children}
    </button>
  );
}

function SqlBlock({ sql }: { sql: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(sql);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="border border-[rgba(255,255,255,0.05)] rounded-lg overflow-hidden bg-[#2A2927] shadow-[0_4px_12px_rgba(0,0,0,0.2)] transition-all">
      <button 
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-3 bg-[#2A2927] hover:bg-[#32312F] transition-colors text-sm font-medium text-[#E6E1D8] cursor-pointer"
      >
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-[#638A70]" />
          <span>View Generated SQL</span>
        </div>
        {isOpen ? <ChevronDown className="w-4 h-4 text-[#E6E1D8]/50" /> : <ChevronRight className="w-4 h-4 text-[#E6E1D8]/50" />}
      </button>
      
      {isOpen && (
        <div className="relative p-4 border-t border-[rgba(255,255,255,0.05)] bg-[#1E1E1C]">
          <button 
            onClick={handleCopy}
            className="absolute top-3 right-3 text-[#E6E1D8]/50 hover:text-white transition-colors cursor-pointer"
            title="Copy SQL"
          >
            {copied ? <Check className="w-4 h-4 text-[#638A70]" /> : <Copy className="w-4 h-4" />}
          </button>
          <pre className="text-sm font-mono text-[#E6E1D8] overflow-x-auto pr-8">
            <code>{sql}</code>
          </pre>
        </div>
      )}
    </div>
  );
}

function DataTable({ result }: { result: any }) {
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
  
  return (
    <div className="border border-[rgba(255,255,255,0.05)] rounded-lg bg-[#2A2927] overflow-hidden flex flex-col shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
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
            {data.rows.map((row: any[], i: number) => (
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
      <div className="bg-[#1E1E1C] px-4 py-2 border-t border-[rgba(255,255,255,0.05)] text-xs font-mono text-[#E6E1D8]/70 flex justify-between items-center">
        <span>{data.rows.length} RECORD(S)</span>
        <span className="flex items-center gap-2"><Activity className="w-3 h-3 text-[#638A70]" /> LIVE</span>
      </div>
    </div>
  );
}

function VisualizationRenderer({ visualization, result }: { visualization: any, result: any }) {
  const data = typeof result === 'string' ? JSON.parse(result) : result;
  const { plot_type, x_axis, y_axis, title } = visualization;

  if (!data || !data.rows || data.rows.length === 0) return null;

  // Handle Indicator (Scalar Value)
  if (plot_type === 'indicator') {
    const val = data.rows[0][data.columns.indexOf(y_axis)];
    return (
      <div className="bg-[#2A2927] border border-[#638A70]/20 rounded-lg p-8 shadow-[0_4px_12px_rgba(0,0,0,0.2)] flex flex-col items-center justify-center text-center">
        <Sparkles className="w-5 h-5 text-[#638A70] mb-4" />
        <h3 className="text-sm font-mono text-[#E6E1D8]/50 uppercase tracking-widest mb-2">{title}</h3>
        <div className="text-5xl font-heading font-bold text-[#E6E1D8] tracking-tighter">
          {typeof val === 'number' ? val.toLocaleString() : val}
        </div>
      </div>
    );
  }

  // Common data extraction for charts
  const xIdx = data.columns.indexOf(x_axis);
  const yIdx = Array.isArray(y_axis) ? data.columns.indexOf(y_axis[0]) : data.columns.indexOf(y_axis);
  
  const chartData = data.rows.slice(0, 10).map((row: any[]) => ({
    label: String(row[xIdx] || ''),
    value: Number(row[yIdx] || 0)
  }));

  const maxValue = Math.max(...chartData.map((d: any) => d.value), 1);

  return (
    <div className="bg-[#2A2927] border border-[#638A70]/20 rounded-lg p-6 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
      <div className="flex items-center gap-2 mb-8">
        <Sparkles className="w-4 h-4 text-[#638A70]" />
        <h3 className="text-lg font-heading font-semibold text-[#E6E1D8] tracking-tight">
          {title}
        </h3>
      </div>

      <div className="h-72 w-full relative flex items-end gap-2 px-2 pb-14">
        {plot_type === 'bar' && chartData.map((d: any, i: number) => (
          <div key={i} className="flex-1 flex flex-col items-center gap-2 group h-full justify-end relative">
            <div 
              className="w-full bg-[#638A70]/80 rounded-t-sm transition-all duration-500 hover:bg-[#638A70] relative"
              style={{ height: `${(d.value / maxValue) * 100}%` }}
            >
              <div className="absolute -top-6 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-[10px] font-mono text-[#E6E1D8] bg-[#1E1E1C] px-1.5 py-0.5 rounded border border-white/5 whitespace-nowrap z-10">
                {d.value.toLocaleString()}
              </div>
            </div>
            {/* Rotated & Truncated Labels - Fixed Positioning */}
            <div className="absolute top-full w-full flex justify-center h-14">
              <div 
                className="text-[10px] font-mono text-[#E6E1D8]/50 uppercase tracking-tighter whitespace-nowrap overflow-hidden text-ellipsis max-w-[80px] text-right rotate-[-45deg] origin-top-right translate-x-[-50%] mt-2"
                title={d.label}
              >
                {d.label}
              </div>
            </div>
          </div>
        ))}

        {plot_type === 'line' && (
          <div className="w-full h-full relative">
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-full">
              <path 
                d={`M ${chartData.map((d: any, i: number) => `${(i / (chartData.length - 1)) * 100},${100 - (d.value / maxValue) * 100}`).join(' L ')}`}
                fill="none"
                stroke="#638A70"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {chartData.map((d: any, i: number) => (
                <circle 
                  key={i}
                  cx={(i / (chartData.length - 1)) * 100} 
                  cy={100 - (d.value / maxValue) * 100} 
                  r="1.5" 
                  fill="#E6E1D8" 
                  className="hover:r-3 transition-all cursor-crosshair"
                />
              ))}
            </svg>
            <div className="absolute bottom-0 left-0 right-0 flex justify-between pt-2">
              {chartData.filter((_: any, i: number) => i % 2 === 0).map((d: any, i: number) => (
                <div key={i} className="text-[9px] font-mono text-[#E6E1D8]/40 uppercase tracking-tighter">
                  {d.label}
                </div>
              ))}
            </div>
          </div>
        )}

        {(plot_type !== 'bar' && plot_type !== 'line') && (
          <div className="flex-1 flex items-center justify-center bg-[#1E1E1C] rounded border border-[rgba(255,255,255,0.05)] text-[#E6E1D8]/40 text-sm italic font-mono">
             [ {plot_type.toUpperCase()} CHART ]
          </div>
        )}
      </div>
    </div>
  );
}

export default function AxiomBrainUI() {
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState<string>("");
  const [mounted, setMounted] = useState(false);
  const [isVerifying, setIsVerifying] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const router = useRouter();

  const checkTenant = async () => {
    setIsVerifying(true);
    setConnectionError(null);
    
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    
    if (!session) {
      router.push("/login");
      return;
    }

    try {
      const hostname = typeof window !== 'undefined' ? window.location.hostname : '127.0.0.1';
      const API_URL = process.env.NEXT_PUBLIC_API_URL || `http://${hostname}:8080`;
      
      // Use AbortController for a 5-second timeout
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000);
      
      const res = await fetch(`${API_URL}/api/tenant`, {
        headers: {
          "Authorization": `Bearer ${session.access_token}`
        },
        signal: controller.signal
      });
      
      clearTimeout(timeoutId);

      if (res.ok) {
        const tenant = await res.json();
        if (tenant) {
          setTenantId(tenant.id);
        } else {
          router.push("/onboard");
        }
      } else {
        router.push("/onboard");
      }
    } catch (err: any) {
      console.error("Auth check failed", err);
      if (err.name === 'AbortError') {
        setConnectionError("Connection timed out. Ensure the backend server is running.");
      } else {
        setConnectionError("Unable to reach the Axiom Orchestrator. Check your network connection.");
      }
    } finally {
      setIsVerifying(false);
    }
  };

  useEffect(() => {
    setMounted(true);
    checkTenant();
  }, [router]);

  useEffect(() => {
    if (tenantId) {
      import("@/lib/api").then(({ fetchSources }) => {
        fetchSources(tenantId).then((data) => {
          setSources(data);
          if (data.length > 0 && !selectedSourceId) {
            setSelectedSourceId(data[0].source_id);
          }
        });
      });
    }
  }, [tenantId, selectedSourceId]);

  const { messages, isLoading, sendMessage, handleApprove, selectedModel, setSelectedModel } = useAxiomChat(tenantId || "default", selectedSourceId);
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    sendMessage(input.trim());
    setInput("");
  };

  if (!mounted || isVerifying || !tenantId) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-[#1E1E1C]">
        <div className="flex flex-col items-center gap-6 max-w-xs text-center">
          {connectionError ? (
            <>
              <div className="w-12 h-12 rounded-full bg-[rgba(194,109,92,0.1)] flex items-center justify-center text-[#C26D5C] mb-2">
                <AlertCircle className="w-6 h-6" />
              </div>
              <div className="space-y-2">
                <p className="text-sm font-mono text-[#E6E1D8] uppercase tracking-widest font-bold">Orchestrator Offline</p>
                <p className="text-xs text-[#E6E1D8]/50 leading-relaxed">{connectionError}</p>
              </div>
              <TactileButton variant="outline" onClick={checkTenant} className="mt-2">
                <RefreshCw className="w-3.5 h-3.5 mr-2" />
                Retry Connection
              </TactileButton>
            </>
          ) : (
            <>
              <Loader2 className="w-8 h-8 animate-spin text-[#638A70]" />
              <p className="text-sm font-mono text-[#E6E1D8]/30 uppercase tracking-widest">Securing Connection...</p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col relative overflow-hidden">
      {/* Header with Source Selector */}
      <header className="h-16 border-b border-[rgba(255,255,255,0.05)] flex items-center justify-between px-8 bg-[#1E1E1C]/50 backdrop-blur-md sticky top-0 z-20">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 px-3 py-1.5 bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded-md">
            <Database className="w-3.5 h-3.5 text-[#638A70]" />
            <select 
              className="bg-transparent border-0 outline-none text-xs font-mono font-semibold text-[#E6E1D8] cursor-pointer appearance-none pr-4"
              value={selectedSourceId}
              onChange={(e) => setSelectedSourceId(e.target.value)}
            >
              {sources.length === 0 ? (
                <option value="">No Sources Onboarded</option>
              ) : (
                sources.map(s => (
                  <option key={s.source_id} value={s.source_id}>{s.name} ({s.source_id})</option>
                ))
              )}
            </select>
            <ChevronDown className="w-3 h-3 text-[#E6E1D8]/30 -ml-3 pointer-events-none" />
          </div>

          <div className="flex items-center gap-2 px-3 py-1.5 bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded-md">
            <Cpu className="w-3.5 h-3.5 text-[#638A70]" />
            <select 
              className="bg-transparent border-0 outline-none text-xs font-mono font-semibold text-[#E6E1D8] cursor-pointer appearance-none pr-4"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              <option value="">Default (Settings)</option>
              <option value="gemini-1.5-flash">Gemini 1.5 Flash</option>
              <option value="gpt-4o-mini">GPT-4o Mini</option>
            </select>
            <ChevronDown className="w-3 h-3 text-[#E6E1D8]/30 -ml-3 pointer-events-none" />
          </div>

          <div className="h-4 w-px bg-[rgba(255,255,255,0.05)]" />
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-[#638A70] animate-pulse" />
            <span className="text-[10px] font-mono text-[#638A70] uppercase tracking-widest font-bold">Orchestrator Active</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <TactileButton 
            variant="outline" 
            className="h-9 px-3 border-0 bg-transparent hover:bg-white/5"
            onClick={() => router.push("/data-sources")}
          >
            <Settings className="w-4 h-4 mr-2" />
            Connectors
          </TactileButton>
          <div className="h-4 w-px bg-[rgba(255,255,255,0.05)]" />
          <div className="text-[11px] font-mono text-[#E6E1D8]/30 uppercase tracking-tighter">
            User_ID: {tenantId?.slice(0, 8)}...
          </div>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto scroll-smooth custom-scrollbar">
        <div className="max-w-4xl mx-auto px-8 py-12 pb-40">
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center mt-32">
              <div className="w-16 h-16 rounded-xl bg-[#2A2927] border border-[rgba(255,255,255,0.05)] flex items-center justify-center mb-6 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
                <Network className="w-8 h-8 text-[#638A70]" />
              </div>
              <h1 className="text-3xl font-heading font-semibold text-[#E6E1D8] mb-3 tracking-tight">
                Data Intelligence
              </h1>
              <p className="text-[#E6E1D8]/70 max-w-md mx-auto text-base leading-relaxed">
                Query your enterprise databases using natural language. Axiom will generate, verify, and execute the SQL.
              </p>
            </div>
          ) : (
            <div className="space-y-12">
              {messages.map((msg) => (
                <div key={msg.id} className="relative">
                  {msg.role === "user" && (
                    <div className="mb-6 flex items-start gap-4">
                      <div className="mt-2 w-2 h-2 rounded-full bg-[#638A70] shadow-sm flex-shrink-0" />
                      <h2 className="text-2xl font-heading font-semibold text-[#E6E1D8] tracking-tight leading-snug">
                        {msg.content}
                      </h2>
                    </div>
                  )}

                  {msg.role === "agent" && (
                    <div className="pl-6 border-l border-[rgba(255,255,255,0.1)] space-y-6 relative">
                      {msg.isError && (
                        <div className="bg-[rgba(194,109,92,0.08)] border-l-[4px] border-l-[#C26D5C] rounded-r-md p-4 flex items-start gap-3 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
                          <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5 text-[#C26D5C]" />
                          <div className="leading-relaxed font-medium">
                            {msg.content}
                          </div>
                        </div>
                      )}

                      {msg.status === "loading" && !msg.isError && (
                        <div className="flex items-center gap-3 text-sm font-medium text-[#E6E1D8]/70 py-2">
                          <Loader2 className="w-4 h-4 animate-spin text-[#638A70]" />
                          <span>Computing orchestration path...</span>
                        </div>
                      )}

                      {msg.status === "pending_approval" && (
                        <div className="bg-[rgba(194,109,92,0.08)] border-l-[4px] border-l-[#C26D5C] rounded-r-md p-5 flex flex-col gap-4 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
                          <div className="flex items-center gap-3">
                            <AlertCircle className="w-5 h-5 text-[#C26D5C]" />
                            <h3 className="text-sm font-semibold text-[#C26D5C] uppercase tracking-wider">Override Required</h3>
                          </div>
                          
                          <p className="text-sm text-[#E6E1D8]/90 leading-relaxed">
                            System halted. Proposed operation crosses security threshold. Verify execution path.
                          </p>
                          
                          {msg.metadata?.sql && (
                            <div className="p-4 rounded-md bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] overflow-x-auto shadow-inner">
                              <pre className="text-sm font-mono text-[#E6E1D8]">
                                <code>{msg.metadata.sql}</code>
                              </pre>
                            </div>
                          )}
                          
                          <div className="flex gap-3 pt-2">
                            <TactileButton 
                              variant="primary"
                              onClick={() => handleApprove(true, msg.metadata!.thread_id!)}
                              disabled={isLoading}
                            >
                              <Check className="w-4 h-4 mr-2" />
                              Approve & Execute
                            </TactileButton>
                            <TactileButton 
                              variant="outline"
                              onClick={() => handleApprove(false, msg.metadata!.thread_id!)}
                              disabled={isLoading}
                            >
                              <X className="w-4 h-4 mr-2 text-[#C26D5C]" />
                              Reject
                            </TactileButton>
                          </div>
                        </div>
                      )}

                      {msg.status === "completed" && !msg.isError && (
                        <div className="space-y-6">
                          {msg.metadata?.visualization && msg.metadata?.result && (
                             <VisualizationRenderer visualization={msg.metadata.visualization} result={msg.metadata.result} />
                          )}
                          {msg.metadata?.sql && (
                            <SqlBlock sql={msg.metadata.sql} />
                          )}
                          {msg.metadata?.result && (
                            <div className="pt-2">
                              <DataTable result={msg.metadata.result} />
                            </div>
                          )}
                          {msg.content && !msg.metadata?.result && (
                            <div className="text-sm text-[#E6E1D8]/90 bg-[#2A2927] px-5 py-4 rounded-lg border border-[rgba(255,255,255,0.05)] shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
                              {msg.content}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              <div ref={messagesEndRef} className="h-px w-full" />
            </div>
          )}
        </div>
      </div>

      {/* Floating Command Bar */}
      <div className="absolute bottom-8 left-0 right-0 px-8 pointer-events-none z-30">
        <div className="max-w-3xl mx-auto pointer-events-auto">
          <form 
            onSubmit={handleSubmit} 
            className="relative flex items-center bg-[#2A2927] rounded-lg shadow-[0_4px_12px_rgba(0,0,0,0.2)] border border-[rgba(255,255,255,0.05)] transition-all focus-within:border-[#638A70]/50 focus-within:ring-1 focus-within:ring-[#638A70]/50"
          >
            <div className="pl-5 pr-3 text-[#638A70]">
              <Terminal className="w-5 h-5" />
            </div>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything about your data..."
              className="flex-1 py-4 h-14 text-base font-medium bg-transparent border-0 outline-none px-2 text-[#E6E1D8] placeholder:text-[#E6E1D8]/40"
              disabled={isLoading}
            />
            <div className="pr-3 flex items-center gap-3">
              <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1.5 bg-[#1E1E1C] rounded-md text-xs font-mono text-[#E6E1D8]/50 border border-[rgba(255,255,255,0.05)] shadow-inner">
                <CornerDownLeft className="w-3 h-3" />
                <span>Return</span>
              </div>
              <button 
                type="submit" 
                disabled={!input.trim() || isLoading}
                className="h-10 w-10 flex items-center justify-center rounded-md bg-[#638A70] text-[#1E1E1C] hover:bg-[#729E81] transition-colors shadow-[0_4px_12px_rgba(0,0,0,0.2)] disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none hover:-translate-y-[1px] active:translate-y-0 cursor-pointer"
              >
                <Search className="w-5 h-5" />
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
