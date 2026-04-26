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
        .catch(() => setTenantName("Axiom"));
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
    <aside className={`${isCollapsed ? "w-24" : "w-80"} border-r border-tactile-border flex flex-col flex-shrink-0 z-20 relative bg-tactile-surface h-full transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] shadow-2xl`}>
      {/* Toggle Button */}
      <button 
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="absolute -right-3.5 top-12 w-7 h-7 bg-tactile-primary text-tactile-base rounded-full flex items-center justify-center shadow-tactile hover:bg-tactile-primary-hover transition-all z-30 group"
      >
        {isCollapsed ? <ChevronRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" /> : <ChevronLeft className="w-4 h-4 transition-transform group-hover:-translate-x-0.5" />}
      </button>

      <div className={`p-8 border-b border-tactile-border ${isCollapsed ? "items-center justify-center flex" : ""}`}>
        <div className="flex items-center justify-between w-full">
          <div className="flex items-center gap-4">
            <div className="w-11 h-11 rounded-xl bg-tactile-base border border-tactile-border flex items-center justify-center text-tactile-primary shadow-tactile-inner group transition-all hover:scale-105">
              <Database className="w-5 h-5 group-hover:rotate-12 transition-transform" />
            </div>
            {!isCollapsed && <span className="font-heading font-bold text-2xl tracking-tighter text-tactile-text">Axiom</span>}
          </div>
          {!isCollapsed && (
            <button onClick={handleSignOut} className="text-tactile-text/20 hover:text-tactile-warning transition-colors p-2 rounded-lg hover:bg-white/5" title="Sign Out">
              <LogOut className="w-5 h-5" />
            </button>
          )}
        </div>
      </div>
      
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className={`p-8 ${isCollapsed ? "space-y-8 px-4" : "space-y-12"}`}>
          {/* New Thread Button */}
          <button 
            onClick={onNewThread}
            className={`w-full flex items-center justify-center bg-tactile-primary text-tactile-base rounded-2xl font-bold transition-all shadow-tactile active:scale-95 hover:bg-tactile-primary-hover group ${isCollapsed ? "h-14 w-14 mx-auto" : "gap-3 px-5 py-5 text-base"}`}
            title="New Analysis"
          >
            <Plus className={`${isCollapsed ? "w-7 h-7" : "w-5 h-5"} group-hover:rotate-90 transition-transform duration-300`} />
            {!isCollapsed && <span>New Analysis</span>}
          </button>

          {/* Navigation */}
          <section>
            {!isCollapsed && (
              <h2 className="text-[10px] font-bold text-tactile-text/30 uppercase tracking-[0.3em] mb-6 px-4">
                Systems
              </h2>
            )}
            <nav className="space-y-2.5">
              <Link 
                href="/"
                className={`tactile-sidebar-item group ${
                  pathname === "/" 
                  ? "tactile-sidebar-item-active" 
                  : "text-tactile-text/50 hover:bg-white/5 hover:text-tactile-text"
                } ${isCollapsed ? "h-14 w-14 justify-center mx-auto" : "gap-4 px-5"}`}
                title="Workspace"
              >
                <LayoutDashboard className={`${isCollapsed ? "w-7 h-7" : "w-5 h-5"} ${pathname === "/" ? "text-tactile-primary" : "text-tactile-text/30 group-hover:text-tactile-primary transition-colors"}`} />
                {!isCollapsed && <span className="tracking-tight">Workspace</span>}
              </Link>
              <Link 
                href="/data-sources"
                className={`tactile-sidebar-item group ${
                  pathname === "/data-sources" 
                  ? "tactile-sidebar-item-active" 
                  : "text-tactile-text/50 hover:bg-white/5 hover:text-tactile-text"
                } ${isCollapsed ? "h-14 w-14 justify-center mx-auto" : "gap-4 px-5"}`}
                title="Data Sources"
              >
                <Database className={`${isCollapsed ? "w-7 h-7" : "w-5 h-5"} ${pathname === "/data-sources" ? "text-tactile-primary" : "text-tactile-text/30 group-hover:text-tactile-primary transition-colors"}`} />
                {!isCollapsed && <span className="tracking-tight">Data Connectors</span>}
              </Link>
              <Link
                href="/data-lake"
                className={`tactile-sidebar-item group ${
                  pathname === "/data-lake"
                  ? "tactile-sidebar-item-active"
                  : "text-tactile-text/50 hover:bg-white/5 hover:text-tactile-text"
                } ${isCollapsed ? "h-14 w-14 justify-center mx-auto" : "gap-4 px-5"}`}
                title="Data Lake"
              >
                <Waves className={`${isCollapsed ? "w-7 h-7" : "w-5 h-5"} ${pathname === "/data-lake" ? "text-tactile-primary" : "text-tactile-text/30 group-hover:text-tactile-primary transition-colors"}`} />
                {!isCollapsed && <span className="tracking-tight">Data Lake</span>}
              </Link>
              <button
                className={`tactile-sidebar-item group text-tactile-text/50 hover:bg-white/5 hover:text-tactile-text ${isCollapsed ? "h-14 w-14 justify-center mx-auto" : "gap-4 px-5"}`}
                title="Orchestration"
              >
                <Settings className={`${isCollapsed ? "w-7 h-7" : "w-5 h-5"} text-tactile-text/30 group-hover:text-tactile-primary transition-colors`} />
                {!isCollapsed && <span className="tracking-tight">Orchestration</span>}
              </button>
            </nav>
          </section>

          {/* Active Context Section */}
          {!isCollapsed && (
            <section className="pt-8 border-t border-tactile-border">
              <h2 className="text-[10px] font-bold text-tactile-text/30 uppercase tracking-[0.3em] mb-8 px-4 flex items-center gap-2">
                <Activity className="w-4 h-4 text-tactile-primary" /> Core Intel
              </h2>
              <div className="space-y-8">
                <div className="group px-4">
                  <label className="text-[10px] font-mono text-tactile-text/20 uppercase tracking-[0.2em] mb-3 block group-hover:text-tactile-primary transition-colors">
                    Deployment
                  </label>
                  <div className="px-5 py-4 bg-tactile-base border border-tactile-border rounded-2xl font-sans text-xs text-tactile-text flex items-center justify-between shadow-tactile-inner">
                    <div className="flex items-center gap-3">
                      <div className="w-1.5 h-1.5 rounded-full bg-tactile-primary animate-pulse shadow-[0_0_8px_rgba(125,163,139,0.5)]" />
                      {tenantName}
                    </div>
                  </div>
                </div>
                <div className="group px-4">
                  <label className="text-[10px] font-mono text-tactile-text/20 uppercase tracking-[0.2em] mb-3 block group-hover:text-tactile-primary transition-colors">
                    Node_ID
                  </label>
                  <div className="px-5 py-4 bg-tactile-base border border-tactile-border rounded-2xl font-mono text-[10px] text-tactile-text/30 flex items-center justify-between shadow-tactile-inner truncate">
                    {tenantId || "axiom_01"}
                  </div>
                </div>
              </div>
            </section>
          )}
        </div>
      </div>
      
      {isCollapsed && (
        <div className="p-6 border-t border-tactile-border flex justify-center">
          <button onClick={handleSignOut} className="text-tactile-text/20 hover:text-tactile-warning transition-colors p-3.5 rounded-2xl hover:bg-white/5" title="Sign Out">
            <LogOut className="w-7 h-7" />
          </button>
        </div>
      )}
    </aside>
  );
}
