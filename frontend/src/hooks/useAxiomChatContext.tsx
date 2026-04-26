"use client";

import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from "react";
import { ChatMessage, Thread } from "../types";
import { useAxiomChat } from "./useAxiomChat";
import { fetchThreads } from "../lib/api";

interface ChatContextType {
  messages: ChatMessage[];
  threads: Thread[];
  activeThreadId: string;
  isLoading: boolean;
  isThreadsLoading: boolean;
  selectedModel: string;
  setSelectedModel: (model: string) => void;
  sendMessage: (question: string) => Promise<void>;
  handleApprove: (approved: boolean, threadId: string) => Promise<void>;
  markAsWrong: (messageId: string, comment?: string) => Promise<void>;
  startNewThread: () => void;
  switchThread: (threadId: string) => Promise<void>;
  refreshThreads: () => Promise<void>;
}

const ChatContext = createContext<ChatContextType | undefined>(undefined);

export function ChatProvider({
  children,
  tenantId,
  selectedLakeId,
}: {
  children: React.ReactNode,
  tenantId: string | null,
  selectedLakeId?: string,
}) {
  const chat = useAxiomChat(tenantId || "default", selectedLakeId);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [isThreadsLoading, setIsThreadsLoading] = useState(false);
  const [refreshSignal, setRefreshSignal] = useState(0);

  /**
   * Global Re-validate Signal
   * Triggers a thread fetch across the application
   */
  const triggerRefresh = useCallback(() => {
    setRefreshSignal(prev => prev + 1);
  }, []);

  const refreshThreads = useCallback(async () => {
    if (!tenantId) return;
    setIsThreadsLoading(true);
    try {
      // Map threads with workspace/tenant integrity
      const data = await fetchThreads(tenantId);
      setThreads(data || []);
    } catch (err) {
      console.error("Critical: Thread Synchronization Failure", err);
    } finally {
      setIsThreadsLoading(false);
    }
  }, [tenantId]);

  // Primary Fetch Hook with deep dependencies
  useEffect(() => {
    void Promise.resolve().then(refreshThreads);
  }, [refreshThreads, tenantId, refreshSignal]);

  // New Analysis Intercept
  const startNewThread = useCallback(() => {
    chat.startNewThread();
    triggerRefresh();
  }, [chat, triggerRefresh]);

  // Monitor agent completion to sync history
  const lastAgentMsg = useMemo(() => {
    const agentMsgs = chat.messages.filter(m => m.role === "agent");
    return agentMsgs.length > 0 ? agentMsgs[agentMsgs.length - 1] : null;
  }, [chat.messages]);

  useEffect(() => {
    if (lastAgentMsg?.status === "completed") {
      // Small delay to ensure DB persistence on backend
      const timer = setTimeout(refreshThreads, 500);
      return () => clearTimeout(timer);
    }
  }, [lastAgentMsg?.status, refreshThreads]);

  const value = {
    ...chat,
    threads,
    isThreadsLoading,
    activeThreadId: chat.threadId,
    refreshThreads,
    startNewThread
  };

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error("useChat must be used within a ChatProvider");
  }
  return context;
}
