"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { useChat } from "../hooks/useAxiomChatContext";

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

export function SidebarWrapper({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLoginPage = pathname === "/login";
  const isWorkspacePage = pathname === "/";

  if (isLoginPage) {
    return <main className="w-full h-screen overflow-hidden">{children}</main>;
  }

  // Only the Workspace page has ChatProvider currently. 
  // For other pages, we can either wrap them in ChatProvider or show a limited sidebar.
  // For now, let's just render the children.
  
  if (isWorkspacePage) {
     return <div className="flex w-full h-screen overflow-hidden">{children}</div>;
  }

  return (
    <div className="flex w-full h-screen overflow-hidden">
      <div className="w-72 border-r border-[rgba(255,255,255,0.05)] bg-[#2A2927] h-full flex flex-col items-center justify-center text-[10px] uppercase tracking-widest text-[#E6E1D8]/20 font-mono">
        Sidebar Restricted
      </div>
      <main className="flex-1 h-full overflow-hidden">{children}</main>
    </div>
  );
}
