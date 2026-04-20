"use client";

import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
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
  startNewThread: () => void;
  switchThread: (threadId: string) => Promise<void>;
  refreshThreads: () => Promise<void>;
}

const ChatContext = createContext<ChatContextType | undefined>(undefined);

export function ChatProvider({ children, tenantId, selectedSourceId }: { children: React.ReactNode, tenantId: string | null, selectedSourceId: string }) {
  const chat = useAxiomChat(tenantId || "default", selectedSourceId);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [isThreadsLoading, setIsThreadsLoading] = useState(false);

  const refreshThreads = useCallback(async () => {
    if (!tenantId) return;
    setIsThreadsLoading(true);
    try {
      const data = await fetchThreads(tenantId);
      setThreads(data);
    } catch (err) {
      console.error("Failed to fetch threads", err);
    } finally {
      setIsThreadsLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    refreshThreads();
  }, [refreshThreads, tenantId]); // Refetch when tenant changes

  // Also refresh when a new message completes, as it might create a new thread or update one
  const lastMessageStatus = chat.messages.length > 0 ? chat.messages[chat.messages.length - 1].status : null;
  useEffect(() => {
    if (lastMessageStatus === "completed") {
      refreshThreads();
    }
  }, [lastMessageStatus, refreshThreads]);

  const value = {
    ...chat,
    threads,
    isThreadsLoading,
    activeThreadId: chat.threadId,
    refreshThreads,
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
