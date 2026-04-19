export interface QueryRequest {
  question: string;
  session_id?: string;
  thread_id?: string;
  tenant_id?: string;
  source_id?: string;
}

export interface ApproveRequest {
  thread_id: string;
  session_id?: string;
  tenant_id?: string;
  approved: boolean;
}

export interface QueryResponse {
  sql: string;
  result: string; // JSON string of the results or error
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
}

export interface SourceIn {
  tenant_id: string;
  source_id: string;
  db_url: string;
  db_type: string;
  description: string;
  mcp_config?: any;
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
    thread_id?: string;
    session_id?: string;
  };
}