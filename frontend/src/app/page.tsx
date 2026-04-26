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
      ? "bg-[#638A70]"
      : source.status === "syncing"
      ? "bg-amber-400 animate-pulse"
      : "bg-red-500";
  return (
    <div className="flex items-center gap-3 bg-[#2A2927] border border-white/5 rounded-xl px-4 py-3">
      <Icon className="w-4 h-4 text-[#638A70] shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-[#E6E1D8] truncate">{source.source_id}</p>
        <p className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest">{source.db_type}</p>
      </div>
      <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusClass}`} />
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
    strong: ({node, ...props}: any) => <strong className="font-bold text-[#638A70] text-base" {...props} />,
    li: ({node, ...props}: any) => (
      <li className="flex items-start gap-3 my-2 text-base">
        <div className="w-1.5 h-1.5 rounded-full bg-[#638A70] mt-2" />
        <span className="text-[#E6E1D8]/90 leading-relaxed" {...props} />
      </li>
    ),
    ul: ({node, ...props}: any) => <ul className="space-y-1.5 my-4 list-none pl-0" {...props} />,
    p: ({node, ...props}: any) => <p className="mb-4 leading-relaxed text-[#E6E1D8] text-base" {...props} />
  };

  return (
    <div className="h-full flex flex-col bg-[#1E1E1C] overflow-hidden relative">
      {/* History Drawer Overlay */}
      <AnimatePresence>
        {showHistory && (
          <>
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setShowHistory(false)}
              className="absolute inset-0 bg-black/60 z-40 backdrop-blur-sm"
            />
            <motion.div 
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="absolute top-0 left-0 bottom-0 w-80 bg-[#2A2927] z-50 shadow-2xl border-r border-white/5 flex flex-col"
            >
              <div className="p-6 border-b border-white/5 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <MessageSquare className="w-5 h-5 text-[#638A70]" />
                  <span className="font-bold text-lg text-[#E6E1D8]">History</span>
                </div>
                <button onClick={() => setShowHistory(false)} className="p-2 hover:bg-white/5 rounded-lg text-[#E6E1D8]/40 hover:text-white">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-4 space-y-2 custom-scrollbar">
                {isThreadsLoading && threads.length === 0 ? (
                  <div className="space-y-4 p-4">
                    {[1, 2, 3, 4, 5].map((i) => (
                      <div key={i} className="h-12 w-full bg-[#1E1E1C] animate-pulse rounded-xl opacity-50" />
                    ))}
                  </div>
                ) : threads.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-40 text-center p-6">
                    <MessageSquare className="w-8 h-8 text-white/5 mb-3" />
                    <p className="text-sm text-[#E6E1D8]/30">No recent analysis history found.</p>
                  </div>
                ) : (
                  threads.map((thread) => (
                    <button
                      key={thread.thread_id}
                      onClick={() => {
                        switchThread(thread.thread_id);
                        setShowHistory(false);
                      }}
                      className={`w-full text-left px-4 py-3.5 rounded-xl text-sm transition-all border ${
                        activeThreadId === thread.thread_id
                        ? "bg-[#638A70]/10 text-[#638A70] border-[#638A70]/30 shadow-inner"
                        : "text-[#E6E1D8]/60 hover:bg-white/5 hover:text-[#E6E1D8] border-transparent"
                      }`}
                    >
                      <div className="font-semibold truncate mb-1">{thread.last_question}</div>
                      <div className="text-[10px] uppercase tracking-widest opacity-40 font-mono">
                        {new Date(thread.updated_at).toLocaleDateString()}
                      </div>
                    </button>
                  ))
                )}
              </div>
              <div className="p-4 border-t border-white/5">
                <button 
                  onClick={() => {
                    startNewThread();
                    setShowHistory(false);
                  }}
                  className="w-full flex items-center justify-center gap-2 py-3 bg-[#638A70] text-[#1E1E1C] rounded-xl font-bold text-sm hover:bg-[#729E81] transition-all"
                >
                  <Plus className="w-4 h-4" />
                  New Analysis
                </button>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Header */}
      <header className="h-16 border-b border-white/5 flex items-center justify-between px-6 bg-[#2A2927] flex-shrink-0 z-20">
        <div className="flex items-center gap-4">
          <button 
            onClick={() => setShowHistory(true)}
            className="flex items-center gap-2.5 px-4 py-2 bg-[#1E1E1C] hover:bg-[#32312F] border border-white/5 rounded-xl text-[#E6E1D8] transition-all group"
          >
            <MessageSquare className="w-4 h-4 text-[#638A70]" />
            <span className="text-sm font-bold uppercase tracking-widest">History</span>
            <div className="ml-1 px-1.5 py-0.5 bg-[#638A70]/20 text-[#638A70] rounded text-[10px] font-mono">
              {threads.length}
            </div>
          </button>

          <div className="h-6 w-px bg-white/5 mx-2" />

          <div className="flex items-center gap-2 px-4 py-2 bg-[#1E1E1C] border border-white/5 rounded-xl">
            <Waves className="w-4 h-4 text-[#638A70]" />
            <select 
              className="bg-transparent border-0 outline-none text-[11px] font-mono font-bold uppercase tracking-widest text-[#E6E1D8]/80 cursor-pointer appearance-none pr-4"
              value={selectedLakeId}
              onChange={(e) => setSelectedLakeId(e.target.value)}
            >
              <option value="">Full Data Lake</option>
              {lakes.map((l: any) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2 px-4 py-2 bg-[#1E1E1C] border border-white/5 rounded-xl">
            <Zap className="w-4 h-4 text-[#638A70]" />
            <select 
              className="bg-transparent border-0 outline-none text-[11px] font-mono font-bold uppercase tracking-widest text-[#E6E1D8]/80 cursor-pointer appearance-none pr-4"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              <optgroup label="Claude">
                <option value="claude-sonnet">Claude Sonnet ★</option>
                <option value="claude-opus">Claude Opus</option>
              </optgroup>
              <optgroup label="OpenAI">
                <option value="gpt-4o">GPT-4o</option>
                <option value="gpt-4o-mini">GPT-4o Mini</option>
              </optgroup>
              <optgroup label="Gemini">
                <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
                <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
              </optgroup>
              <optgroup label="DeepSeek">
                <option value="deepseek-chat">DeepSeek Chat</option>
                <option value="deepseek-reasoner">DeepSeek Reasoner</option>
              </optgroup>
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {!!activeArtifactMsg && !showArtifacts && (
            <button 
              onClick={() => setShowArtifacts(true)}
              className="flex items-center gap-2.5 px-4 py-2 bg-[#638A70]/10 text-[#638A70] rounded-xl border border-[#638A70]/20 text-[11px] font-bold uppercase tracking-widest hover:bg-[#638A70]/20 transition-all shadow-lg"
            >
              <PanelRightOpen className="w-4 h-4" />
              Open Workspace
            </button>
          )}
          <button 
            className="p-2.5 rounded-xl bg-[#1E1E1C] border border-white/5 text-[#E6E1D8]/40 hover:text-white hover:bg-[#32312F] transition-all"
            onClick={() => router.push("/data-sources")}
          >
            <Settings className="w-5 h-5" />
          </button>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* Main Chat Area */}
        <div className="flex-1 flex flex-col min-w-0 bg-[#1E1E1C] relative">
          <div className="flex-1 overflow-y-auto scroll-smooth custom-scrollbar px-6 py-10 pb-32">
            <div className="max-w-4xl mx-auto space-y-16">
              {messages.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-center mt-20">
                  <div className="relative mb-8">
                    <div className="absolute inset-0 bg-[#638A70]/20 blur-3xl rounded-full" />
                    <Network className="w-20 h-20 text-[#638A70] relative z-10" />
                  </div>
                  <h1 className="text-4xl font-heading font-bold text-[#E6E1D8] mb-4 tracking-tight">
                    Axiom Intelligence
                  </h1>
                  <p className="text-[#E6E1D8]/40 text-lg max-w-md mx-auto leading-relaxed mb-12">
                    How can I help you analyze your data infrastructure today?
                  </p>

                  {displayedSources.length > 0 && (
                    <div className="w-full max-w-2xl text-left">
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#E6E1D8]/25">
                          {selectedLakeId ? "Lake sources" : "Connected sources"} &middot; {displayedSources.length}
                        </span>
                        <button
                          onClick={() => router.push("/data-sources")}
                          className="text-[10px] font-mono uppercase tracking-widest text-[#638A70]/50 hover:text-[#638A70] transition-colors"
                        >
                          Manage →
                        </button>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                        {displayedSources.map((src: any) => (
                          <SourceMiniCard key={src.source_id} source={src} />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                messages.map((msg: any) => (
                  <div key={msg.id} className="relative">
                    {msg.role === "user" && (
                      <div className="flex flex-col items-start border-l-4 border-[#638A70] pl-8 py-1.5 mb-2">
                        <h2 className="text-lg font-heading font-bold text-[#E6E1D8] tracking-tight leading-tight">
                          {msg.content}
                        </h2>
                      </div>
                    )}

                        {msg.role === "agent" && (
                      <div 
                        className={`relative pt-8 px-6 -mx-6 rounded-2xl transition-all cursor-pointer group/msg ${
                          activeArtifactMsg?.id === msg.id && isArtifactActive 
                          ? 'bg-[#2A2927]/40 border border-[#638A70]/10 shadow-2xl' 
                          : 'hover:bg-white/5 border border-transparent'
                        }`}
                        onClick={() => {
                          if (msg.metadata?.result || msg.metadata?.artifact) {
                            setSelectedArtifactId(msg.id);
                            setShowArtifacts(true);
                          }
                        }}
                      >
                        {/* Vertical connector line */}
                        <div className="absolute top-0 left-0 w-1 h-full bg-[#638A70]/5 ml-[-4px]" />

                        <div className="mt-2">
                          <IntelligenceOrb
                            steps={msg.reasoning_steps || []}
                            isCompleted={msg.status === "completed"}
                            thought={msg.metadata?.thought}
                            sql={msg.metadata?.sql}
                          />
                        </div>

                        {/* Proactive Probing Comparison Card (Shows during pause) */}
                        {msg.metadata?.probing_options && msg.metadata.probing_options.length > 0 && (
                           <div className="bg-[#2A2927] border border-[#638A70]/30 rounded-2xl p-8 space-y-8 shadow-2xl mt-8 relative z-30">
                              <div className="flex items-center gap-4">
                                 <Search className="w-6 h-6 text-[#638A70]" />
                                 <h3 className="text-base font-bold text-[#E6E1D8] uppercase tracking-[0.2em]">Verify Business Intent</h3>
                              </div>
                              <p className="text-base text-[#E6E1D8]/70 leading-relaxed">
                                I found multiple tables that could answer your question. Which one matches your logic?
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
                                    className="bg-[#1E1E1C] border border-white/5 rounded-xl p-6 cursor-pointer hover:border-[#638A70] hover:bg-[#638A70]/5 transition-all group/opt shadow-lg"
                                  >
                                    <div className="flex items-center justify-between mb-4">
                                      <span className="text-xs font-bold text-[#638A70] uppercase tracking-widest">{opt.business_name}</span>
                                      <div className="w-5 h-5 rounded-full border-2 border-[#638A70]/20 group-hover/opt:border-[#638A70] transition-colors" />
                                    </div>
                                    <p className="text-sm text-[#E6E1D8]/60 mb-6 h-12 overflow-hidden leading-relaxed">{opt.description}</p>
                                    
                                    <div className="bg-black/30 rounded-lg p-3 overflow-hidden border border-white/5">
                                       <span className="text-[9px] font-mono text-[#638A70] block mb-2 uppercase tracking-widest font-bold">Sample Data</span>
                                       <pre className="text-[10px] font-mono text-[#E6E1D8]/40 leading-tight">
                                          {opt.sample_data && opt.sample_data[0] 
                                            ? JSON.stringify(opt.sample_data[0], null, 2).slice(0, 100) 
                                            : "No sample data available"}...
                                       </pre>
                                    </div>
                                  </div>
                                ))}
                              </div>

                              <div className="pt-6 border-t border-white/5 flex justify-end">
                                <button 
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    const prevMsg = messages[messages.findIndex((m: any) => m.id === msg.id) - 1];
                                    const userQ = prevMsg ? prevMsg.content : "";
                                    const suggestedTables = msg.metadata.probing_options.map((opt: any) => `'${opt.table_name}'`).join(", ");
                                    sendMessage(`REJECTED_INTENT: The suggested tables [${suggestedTables}] are not what I meant. Please find other tables to answer my question about '${userQ}'.`);
                                  }}
                                  className="text-xs font-bold uppercase tracking-widest text-[#E6E1D8]/30 hover:text-[#C26D5C] transition-all flex items-center gap-3 px-4 py-2 hover:bg-[#C26D5C]/5 rounded-lg"
                                >
                                  <X className="w-4 h-4" />
                                  None of these match my intent
                                </button>
                              </div>
                           </div>
                        )}

                        {/* Click indicator for messages with artifacts */}
                        {(msg.metadata?.result || msg.metadata?.artifact) && (
                          <div className={`absolute top-4 right-6 flex items-center gap-3 px-4 py-1.5 rounded-full text-[10px] font-bold uppercase tracking-[0.15em] transition-all shadow-lg ${
                            activeArtifactMsg?.id === msg.id && isArtifactActive
                            ? 'bg-[#638A70] text-[#1E1E1C]'
                            : 'bg-[#2A2927] text-[#E6E1D8]/40 group-hover/msg:text-[#638A70] border border-white/5'
                          }`}>
                            <LayoutDashboard className="w-3.5 h-3.5" />
                            {activeArtifactMsg?.id === msg.id && isArtifactActive ? 'Active in Workspace' : 'Inspect Results'}
                          </div>
                        )}

                        {msg.isError && (
                          <div className="bg-[#C26D5C]/10 border border-[#C26D5C]/30 rounded-2xl p-6 flex items-start gap-4 mt-8">
                            <AlertCircle className="w-6 h-6 flex-shrink-0 text-[#C26D5C]" />
                            <div className="text-base text-[#E6E1D8] font-semibold">{msg.content}</div>
                          </div>
                        )}

                        {msg.status === "pending_approval" && (!msg.metadata?.probing_options || msg.metadata.probing_options.length === 0) && (
                          <div className="flex items-center gap-3 mt-4">
                            <span className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest">run query?</span>
                            <button
                              onClick={() => handleApprove(true, msg.metadata!.thread_id!)}
                              disabled={isLoading}
                              className="px-3 py-1 text-[11px] font-mono font-bold uppercase tracking-widest bg-[#638A70]/10 text-[#638A70] border border-[#638A70]/30 rounded hover:bg-[#638A70]/20 transition-colors disabled:opacity-40"
                            >
                              Allow
                            </button>
                            <button
                              onClick={() => handleApprove(false, msg.metadata!.thread_id!)}
                              disabled={isLoading}
                              className="px-3 py-1 text-[11px] font-mono font-bold uppercase tracking-widest bg-transparent text-[#E6E1D8]/30 border border-white/10 rounded hover:text-[#C26D5C] hover:border-[#C26D5C]/30 transition-colors disabled:opacity-40"
                            >
                              Deny
                            </button>
                          </div>
                        )}

                        {msg.status === "completed" && !msg.isError && (
                          <div className="mt-8 space-y-10">
                            {msg.content && (
                              <div className="prose prose-lg prose-zinc prose-invert max-w-none">
                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
                                  {msg.content}
                                </ReactMarkdown>
                              </div>
                            )}

                            {/* Feedback & Actions */}
                            <div className="flex items-center gap-6 pt-4">
                               <button 
                                 onClick={(e) => {
                                   e.stopPropagation();
                                   const comment = prompt("Why is this wrong? (e.g. wrong table, incorrect filter)");
                                   if (comment !== null) markAsWrong(msg.id, comment);
                                 }}
                                 className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[#E6E1D8]/20 hover:text-[#C26D5C] transition-all px-3 py-1.5 hover:bg-[#C26D5C]/5 rounded-lg"
                               >
                                 <AlertCircle className="w-4 h-4" />
                                 Feedback
                               </button>
                            </div>

                            {!isArtifactActive && (
                               <div className="pt-8 border-t border-white/5">
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
          <div className="p-10 bg-gradient-to-t from-[#1E1E1C] via-[#1E1E1C] to-transparent flex-shrink-0 relative z-10">
             <div className="max-w-4xl mx-auto relative">
                <form 
                  onSubmit={handleSubmit} 
                  className="relative flex items-center bg-[#2A2927] rounded-2xl border-2 border-white/5 p-2 pl-6 shadow-[0_20px_50px_rgba(0,0,0,0.5)] focus-within:border-[#638A70]/40 transition-all"
                >
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Ask Axiom Intelligence..."
                    className="flex-1 py-4 text-lg font-medium bg-transparent border-0 outline-none text-[#E6E1D8] placeholder:text-[#E6E1D8]/20"
                    disabled={isLoading}
                  />
                  <button 
                    type="submit" 
                    disabled={!input.trim() || isLoading}
                    className="h-14 w-14 flex items-center justify-center rounded-xl bg-[#638A70] text-[#1E1E1C] hover:bg-[#729E81] transition-all disabled:opacity-20 shadow-xl cursor-pointer active:scale-95"
                  >
                    {isLoading ? <Loader2 className="w-6 h-6 animate-spin" /> : <ArrowRight className="w-6 h-6" />}
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
              animate={{ width: '55%', opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.4, ease: [0.23, 1, 0.32, 1] }}
              className="h-full bg-[#2A2927]/60 border-l border-white/5 relative flex flex-col min-w-0 backdrop-blur-xl"
            >
              <div className="h-16 border-b border-white/5 flex items-center justify-between px-8 flex-shrink-0">
                 <div className="flex items-center gap-3">
                    <Sparkles className="w-4 h-4 text-[#638A70]" />
                    <span className="text-xs font-bold text-[#638A70] uppercase tracking-[0.2em]">Live Workspace</span>
                 </div>
                 <button 
                    onClick={() => setShowArtifacts(false)}
                    className="p-2.5 rounded-xl hover:bg-white/5 text-[#E6E1D8]/20 hover:text-white transition-all border border-transparent hover:border-white/5"
                  >
                    <PanelRightClose className="w-5 h-5" />
                  </button>
              </div>
              <div className="flex-1 overflow-hidden p-10">
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
      <div className="h-full w-full flex items-center justify-center bg-[#1E1E1C]">
        <Loader2 className="w-6 h-6 animate-spin text-[#638A70]" />
      </div>
    );
  }

  return (
    <ChatProvider
      tenantId={tenantId}
      selectedLakeId={selectedLakeId}
    >
      <div className="flex w-full h-screen overflow-hidden bg-[#1E1E1C]">
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
