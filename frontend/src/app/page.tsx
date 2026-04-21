"use client";

import { DataTable } from "../components/DataTable";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { 
  Database, Check, X, Copy, 
  Terminal, Activity, LayoutDashboard, Settings, 
  CornerDownLeft, Loader2, AlertCircle, ChevronRight, ChevronDown, Search, Network, Sparkles, Cpu, RefreshCw,
  PanelRightClose, PanelRightOpen, ArrowRight, Zap, ShieldCheck
} from "lucide-react";
import { ChatProvider, useChat } from "../hooks/useAxiomChatContext";
import { Sidebar } from "../components/Sidebar";
import { createClient } from "@/lib/supabase/client";
import { Source } from "../types";
import { IntelligenceOrb } from "../components/IntelligenceOrb";
import { AxiomArtifacts } from "../components/AxiomArtifacts";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Soft, tactile button
function TactileButton({ children, onClick, className = "", disabled = false, variant = "primary" }: any) {
  const baseStyles = "px-4 py-2 rounded-lg font-semibold text-sm transition-all duration-200 flex items-center justify-center cursor-pointer select-none border";
  const variants = {
    primary: "bg-[#638A70] border-[#638A70] text-[#1E1E1C] hover:bg-[#729E81]",
    destructive: "bg-[#C26D5C] border-[#C26D5C] text-white hover:bg-[#D47E6D]",
    outline: "bg-transparent border-white/10 text-[#E6E1D8] hover:bg-white/5"
  };

  return (
    <button
      onClick={(e) => {
        e.preventDefault();
        if (!disabled && onClick) onClick();
      }}
      disabled={disabled}
      className={`${baseStyles} ${variants[variant as keyof typeof variants]} ${disabled ? 'opacity-40 cursor-not-allowed' : 'active:scale-95'} ${className}`}
    >
      {children}
    </button>
  );
}

function ChatInner({ tenantId, sources, selectedSourceId, setSelectedSourceId }: any) {
  const { messages, isLoading, sendMessage, handleApprove, markAsWrong, selectedModel, setSelectedModel } = useChat();
  const [input, setInput] = useState("");
  const [showArtifacts, setShowArtifacts] = useState(true);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const router = useRouter();

  const latestArtifactMsg = [...messages].reverse().find((m: any) => m.metadata?.result || m.metadata?.visualization);
  
  // Logic: Prefer manually selected artifact, otherwise fall back to the latest one
  const activeArtifactMsg = selectedArtifactId 
    ? messages.find((m: any) => m.id === selectedArtifactId) 
    : latestArtifactMsg;

  useEffect(() => {
    if (latestArtifactMsg?.id && !selectedArtifactId) {
      setShowArtifacts(true);
    }
  }, [latestArtifactMsg?.id, selectedArtifactId]);

  const isArtifactActive = !!activeArtifactMsg && showArtifacts;

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

  const MarkdownComponents: any = {
    strong: ({node, ...props}: any) => <strong className="font-bold text-[#638A70]" {...props} />,
    li: ({node, ...props}: any) => (
      <li className="flex items-start gap-3 my-2">
        <div className="w-1 h-1 rounded-full bg-[#638A70] mt-2.5" />
        <span className="text-[#E6E1D8]/80" {...props} />
      </li>
    ),
    ul: ({node, ...props}: any) => <ul className="space-y-1 my-4 list-none pl-0" {...props} />,
    p: ({node, ...props}: any) => <p className="mb-4 leading-relaxed text-[#E6E1D8]/90" {...props} />
  };

  return (
    <div className="h-full flex flex-col bg-[#1E1E1C] overflow-hidden">
      {/* Header */}
      <header className="h-14 border-b border-white/5 flex items-center justify-between px-6 bg-[#2A2927] flex-shrink-0 z-20">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 px-3 py-1 bg-[#1E1E1C] border border-white/5 rounded-md">
            <Database className="w-3 h-3 text-[#638A70]" />
            <select 
              className="bg-transparent border-0 outline-none text-[10px] font-mono font-bold uppercase tracking-wider text-[#E6E1D8]/60 cursor-pointer appearance-none pr-4"
              value={selectedSourceId}
              onChange={(e) => setSelectedSourceId(e.target.value)}
            >
              {sources.length === 0 ? (
                <option value="">No Sources</option>
              ) : (
                sources.map((s: any) => (
                  <option key={s.source_id} value={s.source_id}>{s.name}</option>
                ))
              )}
            </select>
          </div>

          <div className="flex items-center gap-2 px-3 py-1 bg-[#1E1E1C] border border-white/5 rounded-md">
            <Zap className="w-3 h-3 text-[#638A70]" />
            <select 
              className="bg-transparent border-0 outline-none text-[10px] font-mono font-bold uppercase tracking-wider text-[#E6E1D8]/60 cursor-pointer appearance-none pr-4"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              <option value="gemini-1.5-flash">Gemini 1.5</option>
              <option value="gpt-4o-mini">GPT-4o Mini</option>
            </select>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {!!activeArtifactMsg && !showArtifacts && (
            <button 
              onClick={() => setShowArtifacts(true)}
              className="flex items-center gap-2 px-3 py-1.5 bg-[#638A70]/10 text-[#638A70] rounded-md border border-[#638A70]/20 text-[10px] font-bold uppercase tracking-widest hover:bg-[#638A70]/20 transition-all"
            >
              <PanelRightOpen className="w-3.5 h-3.5" />
              Show Workspace
            </button>
          )}
          <button 
            className="p-1.5 rounded-md bg-[#1E1E1C] border border-white/5 text-[#E6E1D8]/40 hover:text-white"
            onClick={() => router.push("/data-sources")}
          >
            <Settings className="w-4 h-4" />
          </button>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* Main Chat Area */}
        <div className="flex-1 flex flex-col min-w-0 bg-[#1E1E1C] relative">
          <div className="flex-1 overflow-y-auto scroll-smooth custom-scrollbar px-6 py-10 pb-32">
            <div className="max-w-3xl mx-auto space-y-12">
              {messages.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-center mt-20">
                  <Network className="w-12 h-12 text-[#638A70]/30 mb-6" />
                  <h1 className="text-2xl font-heading font-semibold text-[#E6E1D8] mb-2">
                    Axiom Intelligence
                  </h1>
                  <p className="text-[#E6E1D8]/30 text-sm max-w-sm mx-auto">
                    Analyze your data infrastructure through natural language.
                  </p>
                </div>
              ) : (
                messages.map((msg: any) => (
                  <div key={msg.id} className="relative">
                    {msg.role === "user" && (
                      <div className="flex flex-col items-start border-l-2 border-[#638A70] pl-6 py-1">
                        <h2 className="text-xl font-heading font-semibold text-[#E6E1D8] tracking-tight">
                          {msg.content}
                        </h2>
                      </div>
                    )}

                        {msg.role === "agent" && (
                      <div 
                        className={`relative pt-6 px-4 -mx-4 rounded-xl transition-all cursor-pointer group/msg ${
                          activeArtifactMsg?.id === msg.id && isArtifactActive 
                          ? 'bg-[#2A2927]/30 border border-white/5 shadow-inner' 
                          : 'hover:bg-white/5 border border-transparent'
                        }`}
                        onClick={() => {
                          if (msg.metadata?.result || msg.metadata?.visualization) {
                            setSelectedArtifactId(msg.id);
                            setShowArtifacts(true);
                          }
                        }}
                      >
                        {/* Vertical connector line */}
                        <div className="absolute top-0 left-0 w-px h-full bg-[#638A70]/10 ml-[-2px]" />

                        <IntelligenceOrb 
                          steps={msg.reasoning_steps || []} 
                          isCompleted={msg.status === "completed"} 
                          thought={msg.metadata?.thought} 
                          sql={msg.metadata?.sql}
                        />

                        {/* Proactive Probing Comparison Card (Shows during pause) */}
                        {msg.metadata?.probing_options && msg.metadata.probing_options.length > 0 && (
                           <div className="bg-[#2A2927] border border-[#638A70]/30 rounded-xl p-6 space-y-6 shadow-2xl mt-4 relative z-30">
                              <div className="flex items-center gap-3">
                                 <Search className="w-5 h-5 text-[#638A70]" />
                                 <h3 className="text-sm font-bold text-[#E6E1D8] uppercase tracking-widest">Verify Business Intent</h3>
                              </div>
                              <p className="text-xs text-[#E6E1D8]/60 leading-relaxed">
                                I found multiple tables that could answer your question. Which one matches your logic?
                              </p>
                              
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                {msg.metadata.probing_options.map((opt: any) => (
                                  <div 
                                    key={opt.id}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const prevMsg = messages[messages.findIndex((m: any) => m.id === msg.id) - 1];
                                      const userQ = prevMsg ? prevMsg.content : "";
                                      sendMessage(`CONFIRMED_SOURCE: Use the '${opt.table_name}' table to answer my question about '${userQ}'.`);
                                    }}
                                    className="bg-[#1E1E1C] border border-white/5 rounded-lg p-4 cursor-pointer hover:border-[#638A70] transition-all group"
                                  >
                                    <div className="flex items-center justify-between mb-2">
                                      <span className="text-[10px] font-bold text-[#638A70] uppercase">{opt.business_name}</span>
                                      <div className="w-4 h-4 rounded-full border border-[#638A70]/20 group-hover:bg-[#638A70]/20" />
                                    </div>
                                    <p className="text-[10px] text-[#E6E1D8]/40 mb-4 h-8 overflow-hidden">{opt.description}</p>
                                    
                                    <div className="bg-black/20 rounded p-2 overflow-hidden">
                                       <span className="text-[8px] font-mono text-[#638A70]/50 block mb-1 uppercase">Sample Data</span>
                                       <pre className="text-[9px] font-mono text-[#E6E1D8]/30">
                                          {opt.sample_data && opt.sample_data[0] 
                                            ? JSON.stringify(opt.sample_data[0], null, 2).slice(0, 80) 
                                            : "No sample data available"}...
                                       </pre>
                                    </div>
                                  </div>
                                ))}
                              </div>
                           </div>
                        )}

                        {/* Click indicator for messages with artifacts */}
                        {(msg.metadata?.result || msg.metadata?.visualization) && (
                          <div className={`absolute top-2 right-4 flex items-center gap-2 px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-widest transition-all ${
                            activeArtifactMsg?.id === msg.id && isArtifactActive
                            ? 'bg-[#638A70] text-[#1E1E1C]'
                            : 'bg-[#2A2927] text-[#E6E1D8]/20 group-hover/msg:text-[#638A70]'
                          }`}>
                            <LayoutDashboard className="w-3 h-3" />
                            {activeArtifactMsg?.id === msg.id && isArtifactActive ? 'In Workspace' : 'View In Workspace'}
                          </div>
                        )}

                        {msg.isError && (
                          <div className="bg-[#C26D5C]/5 border border-[#C26D5C]/20 rounded-lg p-4 flex items-start gap-3 mt-4">
                            <AlertCircle className="w-5 h-5 flex-shrink-0 text-[#C26D5C]" />
                            <div className="text-sm text-[#E6E1D8]/80 font-medium">{msg.content}</div>
                          </div>
                        )}

                        {msg.status === "pending_approval" && (!msg.metadata?.probing_options || msg.metadata.probing_options.length === 0) && (
                          <div className="bg-[#C26D5C]/5 border border-[#C26D5C]/30 rounded-xl p-6 flex flex-col gap-4 mt-6">
                            <div className="flex items-center gap-2 text-[#C26D5C]">
                              <ShieldCheck className="w-5 h-5" />
                              <h3 className="text-[10px] font-bold uppercase tracking-[0.2em]">Safety Gate</h3>
                            </div>
                            <p className="text-sm text-[#E6E1D8]/70">Manual verification of the execution plan is required.</p>
                            <div className="flex gap-3">
                              <TactileButton variant="primary" onClick={() => handleApprove(true, msg.metadata!.thread_id!)} disabled={isLoading} className="flex-1">
                                Approve
                              </TactileButton>
                              <TactileButton variant="outline" onClick={() => handleApprove(false, msg.metadata!.thread_id!)} disabled={isLoading}>
                                <X className="w-4 h-4" />
                              </TactileButton>
                            </div>
                          </div>
                        )}

                        {msg.status === "completed" && !msg.isError && (
                          <div className="mt-4 space-y-6">
                            {msg.content && (
                              <div className="prose prose-sm prose-zinc prose-invert max-w-none text-[#E6E1D8]/80 leading-relaxed">
                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
                                  {msg.content}
                                </ReactMarkdown>
                              </div>
                            )}

                            {/* Feedback & Actions */}
                            <div className="flex items-center gap-4 pt-2">
                               <button 
                                 onClick={(e) => {
                                   e.stopPropagation();
                                   const comment = prompt("Why is this wrong? (e.g. wrong table, incorrect filter)");
                                   if (comment !== null) markAsWrong(msg.id, comment);
                                 }}
                                 className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-[#E6E1D8]/20 hover:text-[#C26D5C] transition-colors"
                               >
                                 <AlertCircle className="w-3 h-3" />
                                 Mark as Wrong
                               </button>
                            </div>

                            {!isArtifactActive && (
                               <div className="pt-4 border-t border-white/5">
                                 <AxiomArtifacts 
                                   message={msg}
                                   onActionClick={(action) => {
                                     setInput(action);
                                     sendMessage(action);
                                   }}
                                 />
                               </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))
              )}
              <div ref={messagesEndRef} className="h-px w-full" />
            </div>
          </div>
          
          {/* Simple Bottom Chat Input */}
          <div className="p-6 bg-gradient-to-t from-[#1E1E1C] via-[#1E1E1C] to-transparent flex-shrink-0">
             <div className="max-w-3xl mx-auto relative">
                <form 
                  onSubmit={handleSubmit} 
                  className="relative flex items-center bg-[#2A2927] rounded-xl border border-white/5 p-1 pl-4 shadow-xl focus-within:border-[#638A70]/40"
                >
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Ask Axiom..."
                    className="flex-1 py-3 text-sm font-medium bg-transparent border-0 outline-none text-[#E6E1D8] placeholder:text-[#E6E1D8]/20"
                    disabled={isLoading}
                  />
                  <button 
                    type="submit" 
                    disabled={!input.trim() || isLoading}
                    className="h-10 w-10 flex items-center justify-center rounded-lg bg-[#638A70] text-[#1E1E1C] hover:bg-[#729E81] transition-all disabled:opacity-20 cursor-pointer"
                  >
                    {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
                  </button>
                </form>
             </div>
          </div>
        </div>

        {/* Foldable Sidebar Workspace (Right side) */}
        <AnimatePresence>
          {isArtifactActive && (
            <motion.div 
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: '50%', opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.3, ease: "easeInOut" }}
              className="h-full bg-[#2A2927]/50 border-l border-white/5 relative flex flex-col min-w-0"
            >
              <div className="h-14 border-b border-white/5 flex items-center justify-between px-6 flex-shrink-0">
                 <span className="text-[10px] font-mono font-bold text-[#638A70] uppercase tracking-widest">Active Workspace</span>
                 <button 
                    onClick={() => setShowArtifacts(false)}
                    className="p-1.5 rounded-md hover:bg-white/5 text-[#E6E1D8]/20 hover:text-white transition-all"
                  >
                    <PanelRightClose className="w-4 h-4" />
                  </button>
              </div>
              <div className="flex-1 overflow-hidden p-8">
                <AxiomArtifacts 
                  message={activeArtifactMsg}
                  onActionClick={(action) => {
                    setInput(action);
                    sendMessage(action);
                  }}
                />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function SidebarConsumer() {
  const { threads, isThreadsLoading, activeThreadId, switchThread, startNewThread } = useChat();
  return (
    <Sidebar 
      threads={threads} 
      isThreadsLoading={isThreadsLoading}
      activeThreadId={activeThreadId} 
      onThreadSelect={switchThread} 
      onNewThread={startNewThread} 
    />
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
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 8000);
      const res = await fetch(`${API_URL}/api/tenant`, {
        headers: { "Authorization": `Bearer ${session.access_token}` },
        signal: controller.signal
      });
      clearTimeout(timeoutId);

      if (res.ok) {
        const tenant = await res.json();
        if (tenant) setTenantId(tenant.id);
        else router.push("/onboard");
      } else router.push("/onboard");
    } catch (err: any) {
      setConnectionError("Orchestrator timeout. Verify backend availability.");
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
          if (data.length > 0 && !selectedSourceId) setSelectedSourceId(data[0].source_id);
        });
      });
    }
  }, [tenantId, selectedSourceId]);

  if (!mounted || isVerifying || !tenantId) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-[#1E1E1C]">
        <Loader2 className="w-6 h-6 animate-spin text-[#638A70]" />
      </div>
    );
  }

  return (
    <ChatProvider 
      tenantId={tenantId} 
      selectedSourceId={selectedSourceId}
      onSourceRestored={(sourceId) => setSelectedSourceId(sourceId)}
    >
      <div className="flex w-full h-screen overflow-hidden bg-[#1E1E1C]">
        <SidebarConsumer />
        <main className="flex-1 h-full overflow-hidden">
          <ChatInner 
            tenantId={tenantId} 
            sources={sources} 
            selectedSourceId={selectedSourceId} 
            setSelectedSourceId={setSelectedSourceId} 
          />
        </main>
      </div>
    </ChatProvider>
  );
}
