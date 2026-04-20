"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { 
  Database, Activity, LayoutDashboard, Settings, LogOut, MessageSquare, Plus
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Thread } from "@/types";

interface SidebarProps {
  threads: Thread[];
  isThreadsLoading: boolean;
  activeThreadId: string;
  onThreadSelect: (threadId: string) => void;
  onNewThread: () => void;
}

export function Sidebar({ threads, isThreadsLoading, activeThreadId, onThreadSelect, onNewThread }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [hasSession, setHasSession] = useState(false);
  const [tenantName, setTenantName] = useState<string>("Loading...");
  const [tenantId, setTenantId] = useState<string>("");

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      setHasSession(!!session);
      if (session) {
        // Fetch tenant details
        const hostname = typeof window !== 'undefined' ? window.location.hostname : '127.0.0.1';
        const API_URL = process.env.NEXT_PUBLIC_API_URL || `http://${hostname}:8080`;
        fetch(`${API_URL}/api/tenant`, {
          headers: { "Authorization": `Bearer ${session.access_token}` }
        })
        .then(res => res.json())
        .then(data => {
          if (data) {
            setTenantName(data.name);
            setTenantId(data.id);
          } else {
            setTenantName("No Workspace");
          }
        })
        .catch(() => setTenantName("Nexus"));
      }
    });
  }, []);

  const handleSignOut = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
  };

  if (!hasSession) return null;

  return (
    <aside className="w-72 border-r border-[rgba(255,255,255,0.05)] flex flex-col flex-shrink-0 z-20 relative bg-[#2A2927] h-full">
      <div className="p-6 border-b border-[rgba(255,255,255,0.05)]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] flex items-center justify-center text-[#E6E1D8]">
              <Database className="w-4 h-4" />
            </div>
            <span className="font-heading font-bold text-xl tracking-tight text-[#E6E1D8]">Axiom</span>
          </div>
          <button onClick={handleSignOut} className="text-[#E6E1D8]/30 hover:text-[#E6E1D8]/80 transition-colors" title="Sign Out">
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
      
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="p-6 space-y-8">
          {/* New Thread Button */}
          <button 
            onClick={onNewThread}
            className="w-full flex items-center gap-3 px-4 py-3 bg-[#638A70] text-[#1E1E1C] rounded-md font-semibold text-sm hover:bg-[#729E81] transition-all shadow-lg active:translate-y-0.5"
          >
            <Plus className="w-4 h-4" />
            New Analysis
          </button>

          {/* History Section */}
          <section>
            <h2 className="text-[11px] font-semibold text-[#E6E1D8]/50 uppercase tracking-widest mb-4 flex items-center gap-2">
              <MessageSquare className="w-3 h-3 text-[#638A70]" /> Recent Threads
            </h2>
            <div className="space-y-1">
              {isThreadsLoading && threads.length === 0 ? (
                <div className="space-y-2 px-2">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-8 w-full bg-[#1E1E1C] animate-pulse rounded opacity-50" />
                  ))}
                </div>
              ) : threads.length === 0 ? (
                <p className="text-[10px] text-[#E6E1D8]/30 italic px-2">No recent history.</p>
              ) : (
                threads.slice(0, 10).map((thread) => (
                  <button
                    key={thread.thread_id}
                    onClick={() => onThreadSelect(thread.thread_id)}
                    className={`w-full text-left px-3 py-2.5 rounded text-xs transition-colors truncate ${
                      activeThreadId === thread.thread_id
                      ? "bg-[#1E1E1C] text-[#E6E1D8] border border-[rgba(255,255,255,0.05)] shadow-inner"
                      : "text-[#E6E1D8]/60 hover:bg-[#32312F] hover:text-[#E6E1D8]"
                    }`}
                    title={thread.last_question}
                  >
                    {thread.last_question}
                  </button>
                ))
              )}
            </div>
          </section>

          {/* Active Context Section */}
          <section>
            <h2 className="text-[11px] font-semibold text-[#E6E1D8]/50 uppercase tracking-widest mb-4 flex items-center gap-2">
              <Activity className="w-3 h-3 text-[#638A70]" /> System Telemetry
            </h2>
            <div className="space-y-4">
              <div className="group">
                <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-wider mb-1 block">
                  Organization
                </label>
                <div className="px-3 py-2 bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded font-sans text-xs text-[#E6E1D8] flex items-center justify-between shadow-inner">
                  <div className="flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#638A70]" />
                    {tenantName}
                  </div>
                </div>
              </div>
              <div className="group">
                <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-wider mb-1 block">
                  Workspace_ID
                </label>
                <div className="px-3 py-2 bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded font-mono text-[10px] text-[#E6E1D8]/50 flex items-center justify-between shadow-inner">
                  {tenantId || "pending_init"}
                </div>
              </div>
            </div>
          </section>

          {/* Navigation */}
          <section>
            <h2 className="text-[11px] font-semibold text-[#E6E1D8]/50 uppercase tracking-widest mb-3">
              Nexus
            </h2>
            <nav className="space-y-1">
              <Link 
                href="/"
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded text-sm font-medium transition-colors cursor-pointer ${
                  pathname === "/" 
                  ? "bg-[#1E1E1C] text-[#E6E1D8] border border-[rgba(255,255,255,0.05)] shadow-inner" 
                  : "text-[#E6E1D8]/70 hover:bg-[#32312F] hover:text-[#E6E1D8]"
                }`}
              >
                <LayoutDashboard className="w-4 h-4 text-[#638A70]" />
                Workspace
              </Link>
              <Link 
                href="/data-sources"
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded text-sm font-medium transition-colors cursor-pointer ${
                  pathname === "/data-sources" 
                  ? "bg-[#1E1E1C] text-[#E6E1D8] border border-[rgba(255,255,255,0.05)] shadow-inner" 
                  : "text-[#E6E1D8]/70 hover:bg-[#32312F] hover:text-[#E6E1D8]"
                }`}
              >
                <Database className="w-4 h-4" />
                Data Sources
              </Link>
              <button className="w-full flex items-center gap-3 px-3 py-2.5 rounded text-[#E6E1D8]/70 hover:bg-[#32312F] hover:text-[#E6E1D8] text-sm font-medium transition-colors cursor-pointer">
                <Settings className="w-4 h-4" />
                Orchestration
              </button>
            </nav>
          </section>
        </div>
      </div>
    </aside>
  );
}
