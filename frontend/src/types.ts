export interface QueryRequest {
  question: string;
  session_id?: string;
  thread_id?: string;
  tenant_id?: string;
  source_id?: string;
  lake_id?: string;
  lake_scope?: string[];
  model?: string;
}

export interface LakeIn {
  name: string;
  description?: string;
}

export interface LakeOut {
  id: string;
  name: string;
  description?: string;
  created_at: string;
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
  result?: string;
  artifact?: NotebookArtifact;
  insight?: string;
  thought?: string;
  layout?: string;
  action_bar?: string[];
  probing_options?: JsonObject[];
  session_id: string;
  thread_id: string;
  tenant_id: string;
  status: "completed" | "pending_approval" | "rejected" | "needs_clarification";
  routing_candidates?: RoutingCandidate[];
}

export interface RoutingCandidate {
  source_id: string;
  reason: string;
  score: number;
}

export interface LakeSource {
  source_id: string;
  name: string;
  db_type: string;
  description?: string;
  status: string;
  added_at: string;
}

export interface NotebookOutput {
  cell_index: number;
  type: "stream" | "image" | "html" | "text" | "error";
  name?: string;
  text?: string;
  html?: string;
  data_url?: string;
  mime?: string;
  ename?: string;
  evalue?: string;
}

export interface NotebookCellOutput {
  output_type?: string;
  name?: string;
  text?: string | string[];
  html?: string | string[];
  data?: Record<string, string | string[]>;
  ename?: string;
  evalue?: string;
}

export interface NotebookCell {
  cell_type: "markdown" | "code";
  source?: string | string[];
  outputs?: NotebookCellOutput[];
}

export interface NotebookDocument {
  cells?: NotebookCell[];
}

export interface NotebookArtifact {
  artifact_id: string;
  kind: "notebook";
  status: "queued" | "running" | "completed" | "failed";
  notebook_url?: string;
  download_url?: string;
  cells_summary?: string[];
  outputs?: NotebookOutput[];
  execution_error?: string;
  created_at: string;
}

export interface Source {
  source_id: string;
  tenant_id: string;
  name: string;
  description?: string;
  db_type: string;
  status: string;
  error_message?: string;
  mcp_config?: SourceMcpConfig;
  custom_rules?: string | unknown[];
}

export interface SourceIn {
  tenant_id: string;
  source_id: string;
  db_url: string;
  db_type: string;
  description: string;
  mcp_config?: SourceMcpConfig;
  custom_rules?: string | unknown[];
}

export interface ReasoningStep {
  node: string;
  description: string;
  status: 'active' | 'completed' | 'error';
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  content: string; // Question for user, final answer or SQL block for agent
  isError?: boolean;
  status?: "loading" | "completed" | "pending_approval" | "rejected" | "needs_clarification";
  reasoning_steps?: ReasoningStep[];
  metadata?: {
    sql?: string;
    result?: string;
    insight?: string;
    thought?: string;
    layout?: string;
    action_bar?: string[];
    probing_options?: JsonObject[];
    artifact?: NotebookArtifact;
    thread_id?: string;
    session_id?: string;
  };
}

export interface Thread {
  thread_id: string;
  last_question: string;
  updated_at: number;
  metadata?: {
    llm_model?: string;
    source_id?: string;
  };
}

export interface ThreadHistory {
  turns: {
    timestamp: number;
    question: string;
    sql: string;
    result: unknown;
    active_filters?: string[];
    verified_joins?: string[];
    error_log?: string[];
    artifact?: NotebookArtifact;
    insight?: string;
    thought?: string;
  }[];
  metadata?: {
    llm_model?: string;
    source_id?: string;
  };
}
export type JsonObject = Record<string, unknown>;

export interface SourceMcpConfig {
  ssh?: {
    host?: string;
    port?: string | number;
    username?: string;
    private_key?: string;
  };
  [key: string]: unknown;
}
