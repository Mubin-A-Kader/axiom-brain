import { useState, useCallback } from "react";
import { ChatMessage, QueryResponse, ThreadHistory, ReasoningStep } from "../types";
import { askQuestion, askQuestionStream, approveQuery, fetchThreadHistory } from "../lib/api";

export function useAxiomChat(tenantId: string = "default_tenant", sourceId?: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [threadId, setThreadId] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [selectedModel, setSelectedModel] = useState<string>("");

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
          content: "", // Content might be in insight or we use thought
          status: "completed",
          metadata: {
            sql: turn.sql,
            result: turn.result,
            thread_id: newThreadId
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

    const getStepDescription = (node: string, data: any) => {
      if (node === "retrieve_schema") return "Scanning schema for relevant tables...";
      if (node === "plan_query") return "Formulating logical execution plan...";
      if (node === "generate_sql") return "Writing SQL dialect...";
      if (node === "execute_sql") return "Executing query against database...";
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
        source_id: sourceId,
        model: selectedModel || undefined,
      }, (chunk) => {
        if (chunk.__final__) {
           const finalState = chunk.__final__;
           const isPaused = chunk.__is_paused__;

           // Mark last step completed
           if (currentSteps.length > 0) {
             currentSteps[currentSteps.length - 1].status = 'completed';
           }

           const responseObj: QueryResponse = {
              sql: finalState.sql_query || "",
              result: finalState.sql_result || finalState.error || "",
              insight: finalState.response_text || (finalState.error ? `Database error: ${finalState.error}` : undefined),
              thought: finalState.agent_thought,
              visualization: finalState.visualization ? JSON.parse(finalState.visualization) : undefined,
              layout: finalState.layout || "default",
              action_bar: finalState.action_bar || [],
              probing_options: finalState.probing_options || [],
              session_id: sessionId || finalState.session_id || "",
              thread_id: threadId || finalState.thread_id || "",
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
                   visualization: responseObj.visualization,
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
          const stateData = chunk[nodeName];

          // Mark previous step as completed
          if (currentSteps.length > 0) {
            currentSteps[currentSteps.length - 1].status = 'completed';
          }
          
          // Add new step
          currentSteps = [
            ...currentSteps, 
            { 
              node: formatNodeName(nodeName), 
              description: getStepDescription(nodeName, stateData),
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
    } catch (error: any) {
      if (currentSteps.length > 0) {
        currentSteps[currentSteps.length - 1].status = 'error';
      }
      updateLastMessage({
        status: "completed",
        isError: true,
        content: error.message || "An error occurred.",
        reasoning_steps: currentSteps
      });
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, threadId, tenantId, sourceId, selectedModel, appendMessage, updateLastMessage]);

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
    startNewThread,
    switchThread,
    threadId,
  };
  }