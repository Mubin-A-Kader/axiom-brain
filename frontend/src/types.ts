export interface QueryRequest {
  question: string;
  session_id?: string;
  thread_id?: string;
  tenant_id?: string;
  source_id?: string;
  model?: string;
}

export interface ApproveRequest {
  thread_id: string;
  session_id?: string;
  tenant_id?: string;
  approved: boolean;
  model?: string;
}

export interface QueryResponse {
  sql: string;
  result: string; // JSON string of the results or error
  visualization?: {
    x_axis: string | null;
    y_axis: string | string[];
    plot_type: "bar" | "line" | "scatter" | "pie" | "histogram" | "area" | "indicator";
    title: string;
  };
  insight?: string;
  thought?: string;
  session_id: string;
  thread_id: string;
  tenant_id: string;
  status: "completed" | "pending_approval" | "rejected";
}

export interface Source {
  source_id: string;
  tenant_id: string;
  name: string;
  description?: string;
  db_type: string;
  status: string;
  error_message?: string;
  mcp_config?: any;
  custom_rules?: any;
}

export interface SourceIn {
  tenant_id: string;
  source_id: string;
  db_url: string;
  db_type: string;
  description: string;
  mcp_config?: any;
  custom_rules?: any;
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  content: string; // Question for user, final answer or SQL block for agent
  isError?: boolean;
  status?: "loading" | "completed" | "pending_approval";
  metadata?: {
    sql?: string;
    result?: string;
    insight?: string;
    thought?: string;
    visualization?: QueryResponse["visualization"];
    thread_id?: string;
    session_id?: string;
  };
}

export interface Thread {
  thread_id: string;
  last_question: string;
  updated_at: number;
}

export interface ThreadHistory {
  turns: {
    timestamp: number;
    question: string;
    sql: string;
    result: any;
    active_filters?: string[];
    verified_joins?: string[];
    error_log?: string[];
  }[];
}
