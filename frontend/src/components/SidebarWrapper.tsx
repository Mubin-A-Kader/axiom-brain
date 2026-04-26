"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { useChat, ChatProvider } from "../hooks/useAxiomChatContext";
import { useState, useEffect } from "react";
import { createClient } from "@/lib/supabase/client";
import { Loader2 } from "lucide-react";

function SidebarConsumer() {
  const chat = useChat();
  
  // If useChat is used outside of ChatProvider, it might return default values or throw.
  // We need to handle the case where we just want the Sidebar UI but without chat state if not available.
  
  return (
    <Sidebar 
      threads={chat?.threads || []} 
      isThreadsLoading={chat?.isThreadsLoading || false}
      activeThreadId={chat?.activeThreadId || ""} 
      onThreadSelect={chat?.switchThread || (() => {})} 
      onNewThread={chat?.startNewThread || (() => {})} 
    />
  );
}

export function SidebarWrapper({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [isVerifying, setIsVerifying] = useState(true);
  
  const isLoginPage = pathname === "/login";
  const isWorkspacePage = pathname === "/";

  useEffect(() => {
    if (isLoginPage) {
      setIsVerifying(false);
      return;
    }

    const checkTenant = async () => {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        setIsVerifying(false);
        return;
      }

      try {
        const hostname = typeof window !== 'undefined' ? window.location.hostname : '127.0.0.1';
        const API_URL = process.env.NEXT_PUBLIC_API_URL || `http://${hostname}:8080`;
        const res = await fetch(`${API_URL}/api/tenant`, {
          headers: { "Authorization": `Bearer ${session.access_token}` }
        });
        if (res.ok) {
          const tenant = await res.json();
          if (tenant) setTenantId(tenant.id);
        }
      } catch (err) {
        console.error("Sidebar tenant check failed", err);
      } finally {
        setIsVerifying(false);
      }
    };

    checkTenant();
  }, [isLoginPage]);

  if (isLoginPage) {
    return <main className="w-full h-screen overflow-hidden">{children}</main>;
  }

  if (isVerifying) {
    return (
      <div className="h-screen w-full flex items-center justify-center bg-[#1E1E1C]">
        <Loader2 className="w-6 h-6 animate-spin text-[#638A70]" />
      </div>
    );
  }

  // If we are on Workspace page, the page itself provides ChatProvider
  if (isWorkspacePage) {
    return <div className="flex w-full h-screen overflow-hidden">{children}</div>;
  }

  // For other pages, we wrap them in a ChatProvider if we have a tenantId
  // so the Sidebar (threads, etc) works correctly.
  return (
    <div className="flex w-full h-screen overflow-hidden">
      {tenantId ? (
        <ChatProvider tenantId={tenantId}>
          <SidebarConsumer />
          <main className="flex-1 h-full overflow-hidden bg-[#1E1E1C]">
            {children}
          </main>
        </ChatProvider>
      ) : (
        <>
          <div className="w-20 border-r border-white/5 bg-[#2A2927] h-full flex flex-col items-center py-8">
             <div className="w-10 h-10 rounded-lg bg-[#1E1E1C] border border-white/5 flex items-center justify-center text-[#638A70]">
                <Loader2 className="w-5 h-5 animate-spin" />
             </div>
          </div>
          <main className="flex-1 h-full overflow-hidden bg-[#1E1E1C]">
            {children}
          </main>
        </>
      )}
    </div>
  );
}
