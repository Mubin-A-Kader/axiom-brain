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
          probing_options: data.probing_options,
          clarification_questions: data.clarification_questions,
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
    if (isLoading) return;

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

    // Strip subgraph namespace: "sql_subgraph:generate_sql" → "generate_sql"
    const bareNode = (node: string) => node.includes(':') ? node.split(':').pop()! : node;

    const NODE_LABELS: Record<string, string> = {
      memory_manager:          'Context',
      question_ambiguity:      'Clarity',
      route_database:          'Source',
      route_tables:            'Schema',
      intent_prober:           'Probe',
      retrieve_schema:         'Context',
      generate_sql:            'Query',
      execute_sql:             'Execute',
      data_validator:          'Quality',
      critic:                  'Validate',
      discovery:               'Discover',
      synthesize_response:     'Synthesize',
      visualize:               'Visualize',
      build_notebook_artifact: 'Artifact',
      task_planner:            'Plan',
      task_executor:           'Execute',
      lake_orchestrator:       'Orchestrate',
      lake_curator:            'Curate',
      supervisor:              'Route',
      execute:                 'App Query',
    };

    const NODE_DESCRIPTIONS: Record<string, string> = {
      memory_manager:          'Loading conversation context…',
      question_ambiguity:      'Evaluating intent clarity…',
      route_database:          'Connecting to data source…',
      route_tables:            'Identifying relevant schema objects…',
      intent_prober:           'Probing candidates for disambiguation…',
      retrieve_schema:         'Fetching schema context…',
      generate_sql:            'Generating query…',
      execute_sql:             'Running query…',
      data_validator:          'Evaluating data quality…',
      critic:                  'Validating query correctness…',
      discovery:               'Discovering schema structure…',
      synthesize_response:     'Synthesizing insight…',
      visualize:               'Building visualization…',
      build_notebook_artifact: 'Compiling notebook artifact…',
      task_planner:            'Decomposing task into steps…',
      task_executor:           'Executing planned steps…',
      lake_orchestrator:       'Fanning out across data sources…',
      lake_curator:            'Curating cross-source results…',
      supervisor:              'Routing request…',
      execute:                 'Querying data source…',
    };

    const formatNodeName = (node: string) => {
      const key = bareNode(node);
      if (NODE_LABELS[key]) return NODE_LABELS[key];
      return key.split('_').map((p: string) => p.charAt(0).toUpperCase() + p.slice(1)).join(' ');
    };

    const getStepDescription = (node: string) => {
      const key = bareNode(node);
      return NODE_DESCRIPTIONS[key] || `Processing…`;
    };

    // Extract the actual meaningful output from each node's state-update chunk
    const extractStepOutput = (node: string, update: Record<string, unknown>): string | undefined => {
      const key = bareNode(node);

      if (key === 'route_database' || key === 'route_tables') {
        const tables = update.selected_tables as string[] | undefined;
        const sourceId = update.source_id as string | undefined;
        const dbType = update.db_type as string | undefined;
        if (tables?.length) return `Candidates: ${tables.slice(0, 5).join(', ')}`;
        if (sourceId) return dbType ? `${sourceId} (${dbType})` : sourceId;
      }

      if (key === 'question_ambiguity') {
        const qs = update.clarification_questions as unknown[] | undefined;
        if (qs?.length) return `${qs.length} clarification${qs.length !== 1 ? 's' : ''} needed`;
        return 'Intent is clear';
      }

      if (key === 'intent_prober') {
        const opts = update.probing_options as unknown[] | undefined;
        if (opts?.length) return `${opts.length} candidate${opts.length !== 1 ? 's' : ''} found`;
        return 'Intent confirmed';
      }

      if (key === 'retrieve_schema') {
        const ctx = update.schema_context as string | undefined;
        if (ctx) {
          const tableCount = (ctx.match(/CREATE TABLE/gi) || []).length;
          if (tableCount > 0) return `${tableCount} table schema${tableCount !== 1 ? 's' : ''} loaded`;
          return 'Schema loaded';
        }
      }

      if (key === 'generate_sql') {
        const sql = update.sql_query as string | undefined;
        const blueprint = update.logical_blueprint as string | undefined;
        if (blueprint) return blueprint.length > 120 ? blueprint.substring(0, 120) + '…' : blueprint;
        if (sql) return sql.length > 120 ? sql.substring(0, 120) + '…' : sql;
      }

      if (key === 'execute_sql') {
        const result = update.sql_result as string | undefined;
        if (result) {
          try {
            const rows = JSON.parse(result);
            if (Array.isArray(rows)) return `${rows.length} row${rows.length !== 1 ? 's' : ''} returned`;
          } catch { /* not JSON */ }
          return 'Query executed';
        }
        const error = update.error as string | undefined;
        if (error) return error.length > 100 ? error.substring(0, 100) + '…' : error;
      }

      if (key === 'critic') {
        const fb = update.critic_feedback as string | undefined;
        if (fb) return fb.length > 120 ? fb.substring(0, 120) + '…' : fb;
      }

      if (key === 'supervisor') {
        const agent = update.next_agent as string | undefined;
        if (agent) return `→ ${agent.replace('_AGENT', '')}`;
      }

      if (key === 'memory_manager') {
        const filters = update.active_filters as string[] | undefined;
        const confirmed = update.confirmed_tables as string[] | undefined;
        if (confirmed?.length) return `Confirmed: ${confirmed.join(', ')}`;
        if (filters?.length) return `${filters.length} active filter${filters.length !== 1 ? 's' : ''}`;
      }

      if (key === 'lake_orchestrator') {
        const scope = update.lake_scope as string[] | undefined;
        if (scope?.length) return `${scope.length} source${scope.length !== 1 ? 's' : ''} in scope`;
      }

      if (key === 'lake_curator') {
        const results = update.lake_worker_results as unknown[] | undefined;
        if (results?.length) return `${results.length} source result${results.length !== 1 ? 's' : ''} merged`;
      }

      if (key === 'visualize') {
        const code = update.python_code as string | undefined;
        if (code) {
          const lines = code.split('\n').length;
          return `Plotly: ${lines} line${lines !== 1 ? 's' : ''}`;
        }
      }

      if (key === 'build_notebook_artifact') {
        const artifact = update.artifact as Record<string, unknown> | undefined;
        if (artifact?.id) return `Artifact ${artifact.id}`;
        if (artifact) return 'Artifact ready';
      }

      if (key === 'task_planner') {
        const plan = update.task_plan as unknown[] | undefined;
        if (plan?.length) return `${plan.length} task${plan.length !== 1 ? 's' : ''} planned`;
      }

      if (key === 'execute') {
        const results = update.mcp_tool_results as unknown[] | undefined;
        if (results?.length) return `${results.length} result${results.length !== 1 ? 's' : ''} fetched`;
        const err = update.app_error as string | undefined;
        if (err) return err.length > 80 ? err.substring(0, 80) + '…' : err;
      }

      return undefined;
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
              probing_options: Array.isArray(finalState.probing_options) && finalState.probing_options.length > 0
                ? finalState.probing_options as QueryResponse["probing_options"]
                : Array.isArray(finalState.routing_candidates) && finalState.routing_candidates.length > 0
                  ? (finalState.routing_candidates as any[]).map(c => ({
                      id: c.source_id,
                      business_name: c.source_id.replace(/^n8n_/, '').replace(/_/g, ' ').toUpperCase(),
                      description: c.reason,
                      table_name: c.source_id,
                      sample_data: []
                    }))
                  : [],
              clarification_questions: Array.isArray(finalState.clarification_questions)
                ? finalState.clarification_questions as QueryResponse["clarification_questions"]
                : [],
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
                   clarification_questions: responseObj.clarification_questions,
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
            const nodeUpdate = (chunk[nodeName] as Record<string, unknown>) || {};

          // Mark previous step as completed
          if (currentSteps.length > 0) {
            currentSteps[currentSteps.length - 1].status = 'completed';
          }

          // Add new step — output is extracted from the update dict immediately
          // (LangGraph streams updates AFTER a node finishes, so data is already available)
          currentSteps = [
            ...currentSteps,
            {
              node: formatNodeName(nodeName),
              description: getStepDescription(nodeName),
              output: extractStepOutput(nodeName, nodeUpdate),
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
