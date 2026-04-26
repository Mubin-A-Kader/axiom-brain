"use client";

import { DataTable } from "../components/DataTable";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Database, Check, X, Copy,
  Terminal, Activity, LayoutDashboard, Settings,
  CornerDownLeft, Loader2, AlertCircle, ChevronRight, ChevronDown, Search, Network, Sparkles, Cpu, RefreshCw,
  PanelRightClose, PanelRightOpen, ArrowRight, Zap, ShieldCheck, MessageSquare, Plus, Waves,
  Mail, Server, Globe
} from "lucide-react";
import { ChatProvider, useChat } from "../hooks/useAxiomChatContext";
import { Sidebar } from "../components/Sidebar";
import { createClient } from "@/lib/supabase/client";
import { IntelligenceOrb } from "../components/IntelligenceOrb";
import { AxiomArtifacts } from "../components/AxiomArtifacts";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Soft, tactile button
function TactileButton({ children, onClick, className = "", disabled = false, variant = "primary" }: any) {
  const variants = {
    primary: "tactile-button",
    destructive: "bg-tactile-warning border border-tactile-warning text-tactile-base hover:bg-tactile-warning/90 font-bold",
    outline: "tactile-button-outline"
  };

  return (
    <button
      onClick={(e) => {
        e.preventDefault();
        if (!disabled && onClick) onClick();
      }}
      disabled={disabled}
      className={`${variants[variant as keyof typeof variants]} ${className}`}
    >
      {children}
    </button>
  );
}

const SOURCE_ICONS: Record<string, any> = {
  postgresql: Database,
  mysql: Database,
  mongodb: Server,
  gmail: Mail,
  mcp: Terminal,
};

function SourceMiniCard({ source }: { source: any }) {
  const Icon = SOURCE_ICONS[source.db_type] || Globe;
  const statusClass =
    source.status === "active"
      ? "bg-tactile-primary"
      : source.status === "syncing"
      ? "bg-amber-400 animate-pulse"
      : "bg-tactile-warning";
  return (
    <div className="flex items-center gap-4 bg-tactile-surface border border-tactile-border rounded-xl px-5 py-4 transition-all hover:border-tactile-primary/30 group">
      <div className="p-2 bg-tactile-base rounded-lg border border-tactile-border group-hover:text-tactile-primary transition-colors">
        <Icon className="w-4 h-4 shrink-0" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-bold text-tactile-text truncate tracking-tight">{source.source_id}</p>
        <p className="text-[9px] font-mono text-tactile-text/20 uppercase tracking-[0.2em]">{source.db_type}</p>
      </div>
      <div className={`w-1.5 h-1.5 rounded-full shrink-0 shadow-[0_0_8px_rgba(0,0,0,0.5)] ${statusClass}`} />
    </div>
  );
}

function ChatInner({ tenantId, lakes, selectedLakeId, setSelectedLakeId }: any) {
  const { messages, isLoading, sendMessage, handleApprove, markAsWrong, selectedModel, setSelectedModel, threads, activeThreadId, switchThread, startNewThread, isThreadsLoading } = useChat();
  const [input, setInput] = useState("");
  const [showArtifacts, setShowArtifacts] = useState(true);
  const [showHistory, setShowHistory] = useState(false);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [sources, setSources] = useState<any[]>([]);
  const [lakeSources, setLakeSources] = useState<string[] | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const router = useRouter();

  const latestArtifactMsg = [...messages].reverse().find((m: any) => m.metadata?.result || m.metadata?.artifact);
  
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
    if (!tenantId) return;
    import("@/lib/api").then(({ fetchSources }) => {
      fetchSources(tenantId).then(setSources).catch(() => {});
    });
  }, [tenantId]);

  useEffect(() => {
    if (!selectedLakeId) { setLakeSources(null); return; }
    import("@/lib/api").then(({ fetchLakeSources }) => {
      fetchLakeSources(selectedLakeId)
        .then((d: any) => setLakeSources(d.sources.map((s: any) => s.source_id)))
        .catch(() => setLakeSources(null));
    });
  }, [selectedLakeId]);

  const displayedSources = lakeSources === null
    ? sources
    : sources.filter((s) => lakeSources.includes(s.source_id));

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
    strong: ({node, ...props}: any) => <strong className="font-bold text-tactile-primary text-base" {...props} />,
    li: ({node, ...props}: any) => (
      <li className="flex items-start gap-3 my-2 text-base">
        <div className="w-1.5 h-1.5 rounded-full bg-tactile-primary mt-2" />
        <span className="text-tactile-text/90 leading-relaxed" {...props} />
      </li>
    ),
    ul: ({node, ...props}: any) => <ul className="space-y-1.5 my-4 list-none pl-0" {...props} />,
    p: ({node, ...props}: any) => <p className="mb-4 leading-relaxed text-tactile-text text-base" {...props} />
  };

  return (
    <div className="h-full flex flex-col bg-tactile-base overflow-hidden relative">
      {/* History Drawer Overlay */}
      <AnimatePresence>
        {showHistory && (
          <>
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setShowHistory(false)}
              className="absolute inset-0 bg-black/70 z-40 backdrop-blur-md"
            />
            <motion.div 
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ type: "spring", damping: 30, stiffness: 200 }}
              className="absolute top-0 left-0 bottom-0 w-96 bg-tactile-surface z-50 shadow-2xl border-r border-tactile-border flex flex-col"
            >
              <div className="p-8 border-b border-tactile-border flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 rounded-xl bg-tactile-base border border-tactile-border flex items-center justify-center text-tactile-primary shadow-tactile-inner">
                    <MessageSquare className="w-5 h-5" />
                  </div>
                  <span className="font-bold text-xl text-tactile-text tracking-tight">Intelligence History</span>
                </div>
                <button onClick={() => setShowHistory(false)} className="p-2.5 hover:bg-white/5 rounded-xl text-tactile-text/20 hover:text-white transition-all">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-6 space-y-3 custom-scrollbar">
                {isThreadsLoading && threads.length === 0 ? (
                  <div className="space-y-4 p-2">
                    {[1, 2, 3, 4, 5, 6].map((i) => (
                      <div key={i} className="h-16 w-full bg-tactile-base animate-pulse rounded-2xl opacity-40" />
                    ))}
                  </div>
                ) : threads.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-64 text-center p-8">
                    <MessageSquare className="w-10 h-10 text-tactile-text/5 mb-4" />
                    <p className="text-sm text-tactile-text/20 font-medium">No recent analysis history found.</p>
                  </div>
                ) : (
                  threads.map((thread) => (
                    <button
                      key={thread.thread_id}
                      onClick={() => {
                        switchThread(thread.thread_id);
                        setShowHistory(false);
                      }}
                      className={`w-full text-left px-5 py-4 rounded-2xl text-sm transition-all border group relative ${
                        activeThreadId === thread.thread_id
                        ? "bg-tactile-primary/10 text-tactile-primary border-tactile-primary/30 shadow-tactile-inner"
                        : "text-tactile-text/60 hover:bg-white/5 hover:text-tactile-text border-transparent"
                      }`}
                    >
                      <div className="font-bold truncate mb-1.5 tracking-tight">{thread.last_question}</div>
                      <div className="text-[10px] uppercase tracking-[0.2em] opacity-30 font-mono font-bold">
                        {new Date(thread.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      </div>
                    </button>
                  ))
                )}
              </div>
              <div className="p-8 border-t border-tactile-border">
                <button 
                  onClick={() => {
                    startNewThread();
                    setShowHistory(false);
                  }}
                  className="tactile-button w-full flex items-center justify-center gap-3 py-4 text-base tracking-tight"
                >
                  <Plus className="w-5 h-5" />
                  New Analysis
                </button>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Header */}
      <header className="h-20 border-b border-tactile-border flex items-center justify-between px-8 bg-tactile-surface/80 backdrop-blur-xl flex-shrink-0 z-20">
        <div className="flex items-center gap-5">
          <button 
            onClick={() => setShowHistory(true)}
            className="flex items-center gap-3 px-5 py-2.5 bg-tactile-base hover:bg-tactile-elevated border border-tactile-border rounded-xl text-tactile-text transition-all group shadow-tactile"
          >
            <MessageSquare className="w-4 h-4 text-tactile-primary transition-transform group-hover:scale-110" />
            <span className="text-[11px] font-bold uppercase tracking-[0.2em]">History</span>
            <div className="ml-1 px-2 py-0.5 bg-tactile-primary/15 text-tactile-primary rounded-lg text-[10px] font-mono font-bold shadow-tactile-inner">
              {threads.length}
            </div>
          </button>

          <div className="h-8 w-px bg-tactile-border mx-2" />

          <div className="flex items-center gap-3 px-5 py-2.5 bg-tactile-base border border-tactile-border rounded-xl shadow-tactile group">
            <Waves className="w-4 h-4 text-tactile-primary transition-transform group-hover:rotate-12" />
            <select 
              className="bg-transparent border-0 outline-none text-[11px] font-mono font-bold uppercase tracking-[0.2em] text-tactile-text/70 cursor-pointer appearance-none pr-6"
              value={selectedLakeId}
              onChange={(e) => setSelectedLakeId(e.target.value)}
            >
              <option value="">Full Data Lake</option>
              {lakes.map((l: any) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-3 px-5 py-2.5 bg-tactile-base border border-tactile-border rounded-xl shadow-tactile group">
            <Zap className="w-4 h-4 text-tactile-primary transition-transform group-hover:scale-110" />
            <select 
              className="bg-transparent border-0 outline-none text-[11px] font-mono font-bold uppercase tracking-[0.2em] text-tactile-text/70 cursor-pointer appearance-none pr-6"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              <optgroup label="Claude Systems">
                <option value="claude-sonnet">Claude Sonnet ★</option>
                <option value="claude-opus">Claude Opus</option>
              </optgroup>
              <optgroup label="OpenAI Systems">
                <option value="gpt-4o">GPT-4o</option>
                <option value="gpt-4o-mini">GPT-4o Mini</option>
              </optgroup>
              <optgroup label="Gemini Systems">
                <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
                <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
              </optgroup>
              <optgroup label="DeepSeek Systems">
                <option value="deepseek-chat">DeepSeek Chat</option>
                <option value="deepseek-reasoner">DeepSeek Reasoner</option>
              </optgroup>
            </select>
          </div>
        </div>

        <div className="flex items-center gap-4">
          {!!activeArtifactMsg && !showArtifacts && (
            <button 
              onClick={() => setShowArtifacts(true)}
              className="flex items-center gap-3 px-6 py-2.5 bg-tactile-primary/15 text-tactile-primary rounded-xl border border-tactile-primary/25 text-[11px] font-bold uppercase tracking-[0.2em] hover:bg-tactile-primary/25 transition-all shadow-tactile animate-pulse"
            >
              <PanelRightOpen className="w-4 h-4" />
              Open Artifact Workspace
            </button>
          )}
          <button 
            className="p-3 rounded-xl bg-tactile-base border border-tactile-border text-tactile-text/30 hover:text-tactile-primary hover:bg-tactile-elevated transition-all shadow-tactile"
            onClick={() => router.push("/data-sources")}
          >
            <Settings className="w-5 h-5" />
          </button>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* Main Chat Area */}
        <div className="flex-1 flex flex-col min-w-0 bg-tactile-base relative">
          <div className="flex-1 overflow-y-auto scroll-smooth custom-scrollbar px-10 py-12 pb-40">
            <div className="max-w-4xl mx-auto space-y-20">
              {messages.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-center mt-24">
                  <div className="relative mb-12">
                    <div className="absolute inset-0 bg-tactile-primary/10 blur-3xl rounded-full scale-150" />
                    <div className="w-24 h-24 rounded-3xl bg-tactile-surface border border-tactile-border flex items-center justify-center text-tactile-primary shadow-2xl relative z-10 transition-transform hover:scale-105 duration-500">
                      <Network className="w-12 h-12" />
                    </div>
                  </div>
                  <h1 className="text-5xl font-heading font-bold text-tactile-text mb-5 tracking-tighter">
                    Axiom Intelligence
                  </h1>
                  <p className="text-tactile-text/40 text-xl max-w-lg mx-auto leading-relaxed mb-16 font-medium">
                    Bridge your natural language with complex data infrastructure.
                  </p>

                  {displayedSources.length > 0 && (
                    <div className="w-full max-w-3xl text-left animate-in fade-in slide-in-from-bottom-4 duration-700">
                      <div className="flex items-center justify-between mb-5 px-2">
                        <span className="text-[10px] font-mono font-bold uppercase tracking-[0.3em] text-tactile-text/20 flex items-center gap-3">
                          <div className="w-1.5 h-1.5 rounded-full bg-tactile-primary/40" />
                          {selectedLakeId ? "Lake Active Clusters" : "Connected Core Sources"} &middot; {displayedSources.length}
                        </span>
                        <button
                          onClick={() => router.push("/data-sources")}
                          className="text-[10px] font-mono uppercase tracking-[0.2em] text-tactile-primary/40 hover:text-tactile-primary transition-colors font-bold"
                        >
                          Manage Infrastructure →
                        </button>
                      </div>
                      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                        {displayedSources.map((src: any) => (
                          <SourceMiniCard key={src.source_id} source={src} />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                messages.map((msg: any) => (
                  <div key={msg.id} className="relative animate-in fade-in duration-500">
                    {msg.role === "user" && (
                      <div className="flex flex-col items-start border-l-[3px] border-tactile-primary pl-10 py-2 mb-4">
                        <h2 className="text-2xl font-heading font-bold text-tactile-text tracking-tight leading-tight">
                          {msg.content}
                        </h2>
                      </div>
                    )}

                    {msg.role === "agent" && (
                      <div 
                        className={`relative pt-10 px-8 -mx-8 rounded-3xl transition-all duration-300 cursor-pointer group/msg ${
                          activeArtifactMsg?.id === msg.id && isArtifactActive 
                          ? 'bg-tactile-surface/60 border border-tactile-primary/10 shadow-2xl' 
                          : 'hover:bg-tactile-surface/30 border border-transparent'
                        }`}
                        onClick={() => {
                          if (msg.metadata?.result || msg.metadata?.artifact) {
                            setSelectedArtifactId(msg.id);
                            setShowArtifacts(true);
                          }
                        }}
                      >
                        {/* Vertical connector line */}
                        <div className="absolute top-0 left-0 w-[1px] h-full bg-tactile-border ml-[-4px]" />

                        <div className="mt-2">
                          <IntelligenceOrb
                            steps={msg.reasoning_steps || []}
                            isCompleted={msg.status === "completed"}
                            thought={msg.metadata?.thought}
                            sql={msg.metadata?.sql}
                          />
                        </div>

                        {/* Proactive Probing Comparison Card */}
                        {msg.metadata?.probing_options && msg.metadata.probing_options.length > 0 && (
                           <div className="bg-tactile-surface border border-tactile-primary/30 rounded-3xl p-10 space-y-8 shadow-2xl mt-10 relative z-30 animate-in zoom-in-95 duration-500">
                              <div className="flex items-center gap-5">
                                 <div className="w-12 h-12 rounded-2xl bg-tactile-base border border-tactile-border flex items-center justify-center text-tactile-primary shadow-tactile-inner">
                                   <Search className="w-6 h-6" />
                                 </div>
                                 <div>
                                   <h3 className="text-lg font-bold text-tactile-text tracking-tight">Verify Business Intent</h3>
                                   <p className="text-xs font-mono text-tactile-text/30 uppercase tracking-widest mt-1">Ambiguous Schema Mapping Detected</p>
                                 </div>
                              </div>
                              <p className="text-lg text-tactile-text/70 leading-relaxed font-medium">
                                I discovered multiple entities that could resolve this query. Which business logic should be applied?
                              </p>
                              
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                {msg.metadata.probing_options.map((opt: any) => (
                                  <div 
                                    key={opt.id}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const prevMsg = messages[messages.findIndex((m: any) => m.id === msg.id) - 1];
                                      const userQ = prevMsg ? prevMsg.content : "";
                                      sendMessage(`CONFIRMED_SOURCE: Use the '${opt.table_name}' table to answer my question about '${userQ}'.`);
                                    }}
                                    className="bg-tactile-base border border-tactile-border rounded-2xl p-8 cursor-pointer hover:border-tactile-primary hover:bg-tactile-primary/5 transition-all group/opt shadow-tactile hover:shadow-2xl"
                                  >
                                    <div className="flex items-center justify-between mb-5">
                                      <span className="text-xs font-bold text-tactile-primary uppercase tracking-[0.2em]">{opt.business_name}</span>
                                      <div className="w-6 h-6 rounded-full border-2 border-tactile-border group-hover/opt:border-tactile-primary transition-all shadow-inner" />
                                    </div>
                                    <p className="text-sm text-tactile-text/50 mb-8 h-12 overflow-hidden leading-relaxed font-medium">{opt.description}</p>
                                    
                                    <div className="bg-black/40 rounded-xl p-5 overflow-hidden border border-tactile-border shadow-tactile-inner">
                                       <div className="flex items-center gap-2 mb-3">
                                          <div className="w-1.5 h-1.5 rounded-full bg-tactile-primary/40" />
                                          <span className="text-[9px] font-mono text-tactile-primary uppercase tracking-[0.3em] font-bold">Schema_Preview</span>
                                       </div>
                                       <pre className="text-[10px] font-mono text-tactile-text/30 leading-tight">
                                          {opt.sample_data && opt.sample_data[0] 
                                            ? JSON.stringify(opt.sample_data[0], null, 2).slice(0, 120) 
                                            : "No metadata available"}...
                                       </pre>
                                    </div>
                                  </div>
                                ))}
                              </div>

                              <div className="pt-8 border-t border-tactile-border flex justify-end">
                                <button 
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    const prevMsg = messages[messages.findIndex((m: any) => m.id === msg.id) - 1];
                                    const userQ = prevMsg ? prevMsg.content : "";
                                    const suggestedTables = msg.metadata.probing_options.map((opt: any) => `'${opt.table_name}'`).join(", ");
                                    sendMessage(`REJECTED_INTENT: The suggested tables [${suggestedTables}] are not what I meant. Please find other tables to answer my question about '${userQ}'.`);
                                  }}
                                  className="text-xs font-bold uppercase tracking-[0.2em] text-tactile-text/20 hover:text-tactile-warning transition-all flex items-center gap-3 px-6 py-3 hover:bg-tactile-warning/5 rounded-xl border border-transparent hover:border-tactile-warning/20"
                                >
                                  <X className="w-4 h-4" />
                                  None of these match my intent
                                </button>
                              </div>
                           </div>
                        )}

                        {/* Artifact Indicator */}
                        {(msg.metadata?.result || msg.metadata?.artifact) && (
                          <div className={`absolute top-6 right-8 flex items-center gap-3 px-5 py-2 rounded-xl text-[10px] font-bold uppercase tracking-[0.2em] transition-all shadow-tactile border ${
                            activeArtifactMsg?.id === msg.id && isArtifactActive
                            ? 'bg-tactile-primary text-tactile-base border-tactile-primary'
                            : 'bg-tactile-surface text-tactile-text/40 group-hover/msg:text-tactile-primary border-tactile-border group-hover/msg:border-tactile-primary/40'
                          }`}>
                            <LayoutDashboard className="w-3.5 h-3.5" />
                            {activeArtifactMsg?.id === msg.id && isArtifactActive ? 'Active Workspace' : 'Inspect Intel'}
                          </div>
                        )}

                        {msg.isError && (
                          <div className="bg-tactile-warning/5 border border-tactile-warning/20 rounded-3xl p-8 flex items-start gap-5 mt-10 shadow-tactile animate-in slide-in-from-bottom-2 duration-300">
                            <div className="w-12 h-12 rounded-2xl bg-tactile-warning/10 flex items-center justify-center text-tactile-warning shrink-0 border border-tactile-warning/20">
                              <AlertCircle className="w-6 h-6" />
                            </div>
                            <div>
                              <h3 className="text-lg font-bold text-tactile-warning mb-1 tracking-tight">Execution Error</h3>
                              <div className="text-base text-tactile-text/70 font-medium leading-relaxed">{msg.content}</div>
                            </div>
                          </div>
                        )}

                        {msg.status === "pending_approval" && (!msg.metadata?.probing_options || msg.metadata.probing_options.length === 0) && (
                          <div className="flex items-center gap-4 mt-6 p-4 bg-tactile-primary/5 rounded-2xl border border-tactile-primary/10 max-w-fit shadow-tactile-inner">
                            <ShieldCheck className="w-4 h-4 text-tactile-primary" />
                            <span className="text-[10px] font-mono text-tactile-text/40 uppercase tracking-[0.2em] font-bold">Authorize Execution?</span>
                            <div className="flex items-center gap-2 ml-4">
                              <button
                                onClick={() => handleApprove(true, msg.metadata!.thread_id!)}
                                disabled={isLoading}
                                className="px-4 py-1.5 text-[11px] font-bold uppercase tracking-[0.2em] bg-tactile-primary text-tactile-base rounded-lg hover:bg-tactile-primary-hover transition-all disabled:opacity-40 shadow-tactile"
                              >
                                Allow
                              </button>
                              <button
                                onClick={() => handleApprove(false, msg.metadata!.thread_id!)}
                                disabled={isLoading}
                                className="px-4 py-1.5 text-[11px] font-bold uppercase tracking-[0.2em] bg-tactile-surface text-tactile-text/40 border border-tactile-border rounded-lg hover:text-tactile-warning hover:border-tactile-warning/30 transition-all disabled:opacity-40"
                              >
                                Deny
                              </button>
                            </div>
                          </div>
                        )}

                        {msg.status === "completed" && !msg.isError && (
                          <div className="mt-10 space-y-12">
                            {msg.content && (
                              <div className="prose prose-lg prose-zinc prose-invert max-w-none prose-headings:tracking-tighter prose-p:text-tactile-text/80 prose-strong:text-tactile-primary">
                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
                                  {msg.content}
                                </ReactMarkdown>
                              </div>
                            )}

                            {/* Feedback & Actions */}
                            <div className="flex items-center gap-8 pt-6 border-t border-tactile-border/50">
                               <button 
                                 onClick={(e) => {
                                   e.stopPropagation();
                                   const comment = prompt("Why is this wrong? (e.g. wrong table, incorrect filter)");
                                   if (comment !== null) markAsWrong(msg.id, comment);
                                 }}
                                 className="flex items-center gap-3 text-xs font-bold uppercase tracking-[0.2em] text-tactile-text/20 hover:text-tactile-warning transition-all px-4 py-2 hover:bg-tactile-warning/5 rounded-xl border border-transparent hover:border-tactile-warning/20"
                               >
                                 <AlertCircle className="w-4 h-4" />
                                 Feedback
                               </button>
                            </div>

                            {!isArtifactActive && (
                               <div className="pt-12 border-t border-tactile-border">
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
          
          {/* Bottom Chat Input */}
          <div className="p-12 bg-gradient-to-t from-tactile-base via-tactile-base to-transparent flex-shrink-0 relative z-10">
             <div className="max-w-4xl mx-auto relative group">
                <div className="absolute inset-0 bg-tactile-primary/5 blur-2xl rounded-3xl opacity-0 group-focus-within:opacity-100 transition-opacity duration-700" />
                <form 
                  onSubmit={handleSubmit} 
                  className="relative flex items-center bg-tactile-surface rounded-3xl border-2 border-tactile-border p-3 pl-8 shadow-2xl focus-within:border-tactile-primary/40 focus-within:shadow-[0_20px_60px_rgba(0,0,0,0.6)] transition-all duration-500"
                >
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Ask Axiom Intelligence..."
                    className="flex-1 py-5 text-xl font-bold bg-transparent border-0 outline-none text-tactile-text placeholder:text-tactile-text/10 tracking-tight"
                    disabled={isLoading}
                  />
                  <button 
                    type="submit" 
                    disabled={!input.trim() || isLoading}
                    className="h-16 w-16 flex items-center justify-center rounded-2xl bg-tactile-primary text-tactile-base hover:bg-tactile-primary-hover transition-all disabled:opacity-10 shadow-tactile cursor-pointer active:scale-90"
                  >
                    {isLoading ? <Loader2 className="w-7 h-7 animate-spin" /> : <ArrowRight className="w-7 h-7" />}
                  </button>
                </form>
             </div>
          </div>
        </div>

        {/* Workspace (Right side) */}
        <AnimatePresence>
          {isArtifactActive && (
            <motion.div 
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: '58%', opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.6, ease: [0.23, 1, 0.32, 1] }}
              className="h-full bg-tactile-surface/80 border-l border-tactile-border relative flex flex-col min-w-0 backdrop-blur-3xl shadow-2xl"
            >
              <div className="h-20 border-b border-tactile-border flex items-center justify-between px-10 flex-shrink-0">
                 <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-xl bg-tactile-base border border-tactile-border flex items-center justify-center text-tactile-primary shadow-tactile-inner">
                      <Sparkles className="w-5 h-5 shadow-[0_0_8px_rgba(125,163,139,0.3)]" />
                    </div>
                    <div>
                      <span className="text-xs font-bold text-tactile-primary uppercase tracking-[0.3em] block">Artifact_Analysis</span>
                      <span className="text-[9px] font-mono text-tactile-text/20 uppercase tracking-[0.1em] mt-0.5 block">Live Workspace Mode</span>
                    </div>
                 </div>
                 <button 
                    onClick={() => setShowArtifacts(false)}
                    className="p-3 rounded-2xl hover:bg-white/5 text-tactile-text/20 hover:text-white transition-all border border-transparent hover:border-tactile-border shadow-sm group"
                  >
                    <PanelRightClose className="w-5 h-5 transition-transform group-hover:translate-x-0.5" />
                  </button>
              </div>
              <div className="flex-1 overflow-hidden p-12">
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
  const [lakes, setLakes] = useState<any[]>([]);
  const [selectedLakeId, setSelectedLakeId] = useState<string>("");
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
      import("@/lib/api").then(({ fetchLakes }) => {
        fetchLakes(tenantId).then((data) => {
          setLakes(data);
        });
      });
    }
  }, [tenantId]);

  if (!mounted || isVerifying || !tenantId) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-tactile-base">
        <div className="flex flex-col items-center gap-6">
          <div className="w-16 h-16 rounded-2xl bg-tactile-surface border border-tactile-border flex items-center justify-center shadow-2xl">
            <Loader2 className="w-8 h-8 animate-spin text-tactile-primary" />
          </div>
          <p className="text-[10px] font-mono text-tactile-text/20 uppercase tracking-[0.4em] font-bold">Initializing Axiom Core</p>
        </div>
      </div>
    );
  }

  return (
    <ChatProvider
      tenantId={tenantId}
      selectedLakeId={selectedLakeId}
    >
      <div className="flex w-full h-screen overflow-hidden bg-tactile-base">
        <SidebarConsumer />
        <main className="flex-1 h-full overflow-hidden">
          <ChatInner
            tenantId={tenantId}
            lakes={lakes}
            selectedLakeId={selectedLakeId}
            setSelectedLakeId={setSelectedLakeId}
          />
        </main>
      </div>
    </ChatProvider>
  );
}
