import { useState, useCallback } from "react";
import { ChatMessage, QueryResponse, ReasoningStep } from "../types";
import { askQuestionStream, approveQuery, fetchThreadHistory, sendFeedback } from "../lib/api";

export function useAxiomChat(tenantId: string = "default_tenant", lakeId?: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [threadId, setThreadId] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [selectedModel, setSelectedModel] = useState<string>("claude-sonnet");

  const appendMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const startNewThread = useCallback(() => {
    setMessages([]);
    setThreadId("");
    // Session ID can persist or be cleared depending on requirements
  }, []);

  const switchThread = useCallback(async (newThreadId: string) => {
    setIsLoading(true);
    setThreadId(newThreadId);
    setMessages([]);
    try {
      const history = await fetchThreadHistory(newThreadId);

      // Restore metadata (model, source)
      if (history.metadata) {
        if (history.metadata.llm_model) {
          setSelectedModel(history.metadata.llm_model);
        }
      }

      const newMessages: ChatMessage[] = [];
      
      history.turns.forEach((turn, idx) => {
        // Add user question
        newMessages.push({
          id: `history-u-${idx}`,
          role: "user",
          content: turn.question
        });
        
        // Add agent response
        newMessages.push({
          id: `history-a-${idx}`,
          role: "agent",
          content: turn.insight || "", 
          status: "completed",
            metadata: {
              sql: turn.sql,
              result: typeof turn.result === "string" ? turn.result : JSON.stringify(turn.result),
              thread_id: newThreadId,
              artifact: turn.artifact,
              insight: turn.insight,
              thought: turn.thought
            }
        });
      });
      setMessages(newMessages);
    } catch (error) {
      console.error("Failed to load thread history", error);
    } finally {
      setIsLoading(false);
    }
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
          artifact: data.artifact,
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
    appendMessage({ 
      id: agentMsgId, 
      role: "agent", 
      content: "", 
      status: "loading",
      reasoning_steps: []
    });

    setIsLoading(true);
    let currentSteps: ReasoningStep[] = [];

    const formatNodeName = (node: string) => {
      const parts = node.split("_");
      return parts.map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(" ");
    };

    const getStepDescription = (node: string) => {
      if (node === "retrieve_schema") return "Scanning schema for relevant tables...";
      if (node === "plan_query") return "Formulating logical execution plan...";
      if (node === "generate_sql") return "Writing SQL dialect...";
      if (node === "execute_sql") return "Executing query against database...";
      if (node === "build_notebook_artifact") return "Building executable notebook artifact...";
      if (node === "critic_sql") return "Validating query semantics...";
      if (node === "synthesize_response") return "Cleaning data & generating summary...";
      return `Processing ${formatNodeName(node)}...`;
    };

    try {
      await askQuestionStream({
        question,
        session_id: sessionId,
        thread_id: threadId,
        tenant_id: tenantId,
        lake_id: lakeId || undefined,
        model: selectedModel || undefined,
      }, (chunk) => {
        if (chunk.__final__) {
           const finalState = chunk.__final__ as Record<string, unknown>;
           const isPaused = chunk.__is_paused__;

           // Mark last step completed
           if (currentSteps.length > 0) {
             currentSteps[currentSteps.length - 1].status = 'completed';
           }

           const responseObj: QueryResponse = {
              sql: String(finalState.sql_query || ""),
              result: typeof finalState.sql_result === 'string' ? finalState.sql_result : undefined,
              insight: typeof finalState.response_text === "string"
                ? finalState.response_text
                : finalState.error ? `Database error: ${String(finalState.error)}` : undefined,
              thought: typeof finalState.agent_thought === "string" ? finalState.agent_thought : undefined,
              artifact: finalState.artifact as QueryResponse["artifact"],
              layout: typeof finalState.layout === "string" ? finalState.layout : "default",
              action_bar: Array.isArray(finalState.action_bar) ? finalState.action_bar as string[] : [],
              probing_options: Array.isArray(finalState.probing_options) ? finalState.probing_options as QueryResponse["probing_options"] : [],
              session_id: sessionId || String(finalState.session_id || ""),
              thread_id: threadId || String(finalState.thread_id || ""),
              tenant_id: tenantId,
              status: isPaused ? "pending_approval" : "completed"
           };
           
           // Apply final update
           setMessages((prev) => {
             const newMessages = [...prev];
             const idx = newMessages.findIndex(m => m.id === agentMsgId);
             if (idx !== -1) {
               newMessages[idx] = {
                 ...newMessages[idx],
                 status: responseObj.status,
                 content: responseObj.insight || "",
                 reasoning_steps: currentSteps,
                 metadata: {
                   sql: responseObj.sql,
                   result: responseObj.result,
                   insight: responseObj.insight,
                   thought: responseObj.thought,
                   artifact: responseObj.artifact,
                   layout: responseObj.layout,
                   action_bar: responseObj.action_bar,
                   probing_options: responseObj.probing_options,
                   thread_id: responseObj.thread_id,
                   session_id: responseObj.session_id,
                 }
               };
             }
             return newMessages;
           });

           if (!sessionId && responseObj.session_id) setSessionId(responseObj.session_id);
           if (!threadId && responseObj.thread_id) setThreadId(responseObj.thread_id);

           return;
        }

        // Process active nodes
          const nodeNames = Object.keys(chunk);
          if (nodeNames.length > 0) {
            const nodeName = nodeNames[0]; // Usually one node per chunk

          // Mark previous step as completed
          if (currentSteps.length > 0) {
            currentSteps[currentSteps.length - 1].status = 'completed';
          }
          
          // Add new step
          currentSteps = [
            ...currentSteps, 
            { 
              node: formatNodeName(nodeName), 
              description: getStepDescription(nodeName),
              status: 'active' 
            }
          ];

          setMessages((prev) => {
            const newMessages = [...prev];
            const idx = newMessages.findIndex(m => m.id === agentMsgId);
            if (idx !== -1) {
              newMessages[idx] = {
                ...newMessages[idx],
                reasoning_steps: [...currentSteps]
              };
            }
            return newMessages;
          });
        }
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "An error occurred.";
      if (currentSteps.length > 0) {
        currentSteps[currentSteps.length - 1].status = 'error';
      }
      updateLastMessage({
        status: "completed",
        isError: true,
        content: message,
        reasoning_steps: currentSteps
      });
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, threadId, tenantId, lakeId, selectedModel, appendMessage, updateLastMessage]);

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
    } catch (error: unknown) {
      updateLastMessage({
        status: "completed",
        isError: true,
        content: error instanceof Error ? error.message : "An error occurred.",
      });
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, tenantId, selectedModel, updateLastMessage, handleResponse]);

  const markAsWrong = useCallback(async (messageId: string, comment?: string) => {
    if (!threadId) return;
    await sendFeedback({
      thread_id: threadId,
      message_id: messageId,
      is_correct: false,
      comment,
    });
  }, [threadId]);

  return {
    messages,
    isLoading,
    sendMessage,
    handleApprove,
    selectedModel,
    setSelectedModel,
    markAsWrong,
    startNewThread,
    switchThread,
    threadId,
  };
  }
