import { useState, useCallback } from "react";
import { ChatMessage, QueryResponse } from "../types";
import { askQuestion, approveQuery } from "../lib/api";

export function useAxiomChat(tenantId: string = "default_tenant", sourceId?: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [threadId, setThreadId] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [selectedModel, setSelectedModel] = useState<string>("");

  const appendMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateLastMessage = useCallback((updates: Partial<ChatMessage>) => {
    setMessages((prev) => {
      const newMessages = [...prev];
      if (newMessages.length > 0) {
        newMessages[newMessages.length - 1] = {
          ...newMessages[newMessages.length - 1],
          ...updates,
        };
      }
      return newMessages;
    });
  }, []);

  const handleResponse = useCallback((data: QueryResponse) => {
    if (!sessionId) setSessionId(data.session_id);
    if (!threadId) setThreadId(data.thread_id);

    if (data.status === "pending_approval") {
      updateLastMessage({
        status: "pending_approval",
        metadata: {
          sql: data.sql,
          thought: data.thought,
          thread_id: data.thread_id,
          session_id: data.session_id,
        },
      });
    } else if (data.status === "completed") {
      updateLastMessage({
        status: "completed",
        content: data.insight || "",
        metadata: {
          sql: data.sql,
          result: data.result,
          insight: data.insight,
          thought: data.thought,
          visualization: data.visualization,
          thread_id: data.thread_id,
          session_id: data.session_id,
        },
      });
    } else if (data.status === "rejected") {
        updateLastMessage({
            status: "completed",
            content: "Execution was rejected.",
            metadata: {
              sql: data.sql,
            },
        });
    }
  }, [sessionId, threadId, updateLastMessage]);

  const sendMessage = useCallback(async (question: string) => {
    const userMsgId = Date.now().toString();
    appendMessage({ id: userMsgId, role: "user", content: question });

    const agentMsgId = (Date.now() + 1).toString();
    appendMessage({ id: agentMsgId, role: "agent", content: "", status: "loading" });

    setIsLoading(true);
    try {
      const response = await askQuestion({
        question,
        session_id: sessionId,
        thread_id: threadId,
        tenant_id: tenantId,
        source_id: sourceId,
        model: selectedModel || undefined,
      });
      handleResponse(response);
    } catch (error: any) {
      updateLastMessage({
        status: "completed",
        isError: true,
        content: error.message || "An error occurred.",
      });
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, threadId, tenantId, sourceId, selectedModel, appendMessage, updateLastMessage, handleResponse]);

  const handleApprove = useCallback(async (approved: boolean, currentThreadId: string) => {
    setIsLoading(true);
    updateLastMessage({ status: "loading" });
    try {
      const response = await approveQuery({
        thread_id: currentThreadId,
        session_id: sessionId,
        tenant_id: tenantId,
        approved,
        model: selectedModel || undefined,
      });
      handleResponse(response);
    } catch (error: any) {
      updateLastMessage({
        status: "completed",
        isError: true,
        content: error.message || "An error occurred.",
      });
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, tenantId, selectedModel, updateLastMessage, handleResponse]);

  return {
    messages,
    isLoading,
    sendMessage,
    handleApprove,
    selectedModel,
    setSelectedModel,
  };
}