"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Database, Activity, LayoutDashboard, Settings, LogOut, MessageSquare, Plus,
  ChevronLeft, ChevronRight, Menu, Waves
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
  const [isCollapsed, setIsCollapsed] = useState(false);

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
    <aside className={`${isCollapsed ? "w-20" : "w-72"} border-r border-[rgba(255,255,255,0.05)] flex flex-col flex-shrink-0 z-20 relative bg-[#2A2927] h-full transition-all duration-300 ease-in-out shadow-2xl`}>
      {/* Toggle Button */}
      <button 
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="absolute -right-3 top-20 w-6 h-6 bg-[#638A70] text-[#1E1E1C] rounded-full flex items-center justify-center shadow-lg hover:bg-[#729E81] transition-all z-30"
      >
        {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
      </button>

      <div className={`p-6 border-b border-[rgba(255,255,255,0.05)] ${isCollapsed ? "items-center justify-center flex" : ""}`}>
        <div className="flex items-center justify-between w-full">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] flex items-center justify-center text-[#638A70] shadow-inner">
              <Database className="w-5 h-5" />
            </div>
            {!isCollapsed && <span className="font-heading font-bold text-2xl tracking-tight text-[#E6E1D8]">Axiom</span>}
          </div>
          {!isCollapsed && (
            <button onClick={handleSignOut} className="text-[#E6E1D8]/30 hover:text-[#C26D5C] transition-colors p-2 rounded-md hover:bg-white/5" title="Sign Out">
              <LogOut className="w-5 h-5" />
            </button>
          )}
        </div>
      </div>
      
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className={`p-6 ${isCollapsed ? "space-y-6 px-4" : "space-y-10"}`}>
          {/* New Thread Button */}
          <button 
            onClick={onNewThread}
            className={`w-full flex items-center justify-center bg-[#638A70] text-[#1E1E1C] rounded-xl font-bold transition-all shadow-lg active:translate-y-0.5 hover:bg-[#729E81] group ${isCollapsed ? "h-12 w-12 mx-auto" : "gap-3 px-4 py-4 text-base"}`}
            title="New Analysis"
          >
            <Plus className={`${isCollapsed ? "w-6 h-6" : "w-5 h-5"}`} />
            {!isCollapsed && <span>New Analysis</span>}
          </button>

          {/* Navigation */}
          <section>
            {!isCollapsed && (
              <h2 className="text-[12px] font-bold text-[#E6E1D8]/40 uppercase tracking-[0.2em] mb-4 px-2">
                Nexus
              </h2>
            )}
            <nav className="space-y-2">
              <Link 
                href="/"
                className={`w-full flex items-center rounded-xl font-semibold transition-all cursor-pointer group ${
                  pathname === "/" 
                  ? "bg-[#638A70]/10 text-[#638A70] border border-[#638A70]/20 shadow-inner shadow-[#638A70]/5" 
                  : "text-[#E6E1D8]/60 hover:bg-white/5 hover:text-[#E6E1D8]"
                } ${isCollapsed ? "h-12 w-12 justify-center mx-auto" : "gap-4 px-4 py-3.5 text-base"}`}
                title="Workspace"
              >
                <LayoutDashboard className={`${isCollapsed ? "w-6 h-6" : "w-5 h-5"} ${pathname === "/" ? "text-[#638A70]" : "text-[#E6E1D8]/40 group-hover:text-[#638A70] transition-colors"}`} />
                {!isCollapsed && <span>Workspace</span>}
              </Link>
              <Link 
                href="/data-sources"
                className={`w-full flex items-center rounded-xl font-semibold transition-all cursor-pointer group ${
                  pathname === "/data-sources" 
                  ? "bg-[#638A70]/10 text-[#638A70] border border-[#638A70]/20 shadow-inner shadow-[#638A70]/5" 
                  : "text-[#E6E1D8]/60 hover:bg-white/5 hover:text-[#E6E1D8]"
                } ${isCollapsed ? "h-12 w-12 justify-center mx-auto" : "gap-4 px-4 py-3.5 text-base"}`}
                title="Data Sources"
              >
                <Database className={`${isCollapsed ? "w-6 h-6" : "w-5 h-5"} ${pathname === "/data-sources" ? "text-[#638A70]" : "text-[#E6E1D8]/40 group-hover:text-[#638A70] transition-colors"}`} />
                {!isCollapsed && <span>Data Sources</span>}
              </Link>
              <Link
                href="/data-lake"
                className={`w-full flex items-center rounded-xl font-semibold transition-all cursor-pointer group ${
                  pathname === "/data-lake"
                  ? "bg-[#638A70]/10 text-[#638A70] border border-[#638A70]/20 shadow-inner shadow-[#638A70]/5"
                  : "text-[#E6E1D8]/60 hover:bg-white/5 hover:text-[#E6E1D8]"
                } ${isCollapsed ? "h-12 w-12 justify-center mx-auto" : "gap-4 px-4 py-3.5 text-base"}`}
                title="Data Lake"
              >
                <Waves className={`${isCollapsed ? "w-6 h-6" : "w-5 h-5"} ${pathname === "/data-lake" ? "text-[#638A70]" : "text-[#E6E1D8]/40 group-hover:text-[#638A70] transition-colors"}`} />
                {!isCollapsed && <span>Data Lake</span>}
              </Link>
              <button
                className={`w-full flex items-center rounded-xl font-semibold transition-all cursor-pointer group text-[#E6E1D8]/60 hover:bg-white/5 hover:text-[#E6E1D8] ${isCollapsed ? "h-12 w-12 justify-center mx-auto" : "gap-4 px-4 py-3.5 text-base"}`}
                title="Orchestration"
              >
                <Settings className={`${isCollapsed ? "w-6 h-6" : "w-5 h-5"} text-[#E6E1D8]/40 group-hover:text-[#638A70] transition-colors`} />
                {!isCollapsed && <span>Orchestration</span>}
              </button>
            </nav>
          </section>

          {/* Active Context Section */}
          {!isCollapsed && (
            <section className="pt-6 border-t border-white/5">
              <h2 className="text-[12px] font-bold text-[#E6E1D8]/40 uppercase tracking-[0.2em] mb-6 px-2 flex items-center gap-2">
                <Activity className="w-4 h-4 text-[#638A70]" /> Telemetry
              </h2>
              <div className="space-y-6">
                <div className="group px-2">
                  <label className="text-[11px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest mb-2 block group-hover:text-[#638A70] transition-colors">
                    Organization
                  </label>
                  <div className="px-4 py-3 bg-[#1E1E1C] border border-white/5 rounded-xl font-sans text-sm text-[#E6E1D8] flex items-center justify-between shadow-inner">
                    <div className="flex items-center gap-3">
                      <div className="w-2 h-2 rounded-full bg-[#638A70] animate-pulse" />
                      {tenantName}
                    </div>
                  </div>
                </div>
                <div className="group px-2">
                  <label className="text-[11px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest mb-2 block group-hover:text-[#638A70] transition-colors">
                    Workspace_ID
                  </label>
                  <div className="px-4 py-3 bg-[#1E1E1C] border border-white/5 rounded-xl font-mono text-[11px] text-[#E6E1D8]/40 flex items-center justify-between shadow-inner truncate">
                    {tenantId || "pending_init"}
                  </div>
                </div>
              </div>
            </section>
          )}
        </div>
      </div>
      
      {isCollapsed && (
        <div className="p-4 border-t border-white/5 flex justify-center">
          <button onClick={handleSignOut} className="text-[#E6E1D8]/30 hover:text-[#C26D5C] transition-colors p-3 rounded-xl hover:bg-white/5" title="Sign Out">
            <LogOut className="w-6 h-6" />
          </button>
        </div>
      )}
    </aside>
  );
}
