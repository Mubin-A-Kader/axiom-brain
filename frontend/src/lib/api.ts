import {
  ApproveRequest,
  LakeIn,
  LakeOut,
  LakeSource,
  NotebookArtifact,
  NotebookDocument,
  QueryRequest,
  QueryResponse,
  Source,
  SourceIn,
  Thread,
  ThreadHistory,
} from "../types";
import { createClient } from "./supabase/client";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

async function getAuthHeaders() {
  const supabase = createClient();
  const { data: { session } } = await supabase.auth.getSession();

  if (!session?.access_token) {
    throw new Error("No active authentication session.");
  }

  return {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${session.access_token}`,
  };
}

export async function askQuestion(req: QueryRequest): Promise<QueryResponse> {
  console.log("askQuestion req:", req);
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_URL}/query`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      question: req.question,
      session_id: req.session_id || "",
      thread_id: req.thread_id || "",
      tenant_id: req.tenant_id || "default_tenant",
      source_id: req.source_id,
      lake_id: req.lake_id,
      model: req.model,
    }),
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "An error occurred during the query.");
  }

  const resData = await response.json();
  console.log("askQuestion res:", resData);
  return resData;
}

export async function askQuestionStream(
  req: QueryRequest,
  onChunk: (data: Record<string, unknown>) => void
): Promise<void> {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_URL}/query/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      question: req.question,
      session_id: req.session_id || "",
      thread_id: req.thread_id || "",
      tenant_id: req.tenant_id || "default_tenant",
      source_id: req.source_id,
      lake_id: req.lake_id,
      model: req.model,
    }),
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "An error occurred during the query.");
  }

  if (!response.body) throw new Error("No response body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || ""; // keep incomplete line

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const dataStr = line.replace("data: ", "").trim();
        if (dataStr === "[DONE]") {
          return;
        }
        try {
          const data = JSON.parse(dataStr);
          onChunk(data);
        } catch {
          // parse error, wait for more data
        }
      }
    }
  }
}

export async function approveQuery(req: ApproveRequest): Promise<QueryResponse> {
  console.log("approveQuery req:", req);
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_URL}/approve`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      thread_id: req.thread_id,
      session_id: req.session_id || "",
      tenant_id: req.tenant_id || "default_tenant",
      approved: req.approved,
      model: req.model,
    }),
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "An error occurred during approval.");
  }

  return response.json();
}

export async function fetchArtifact(
  artifactId: string
): Promise<{ artifact: NotebookArtifact; notebook: NotebookDocument }> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/artifacts/${artifactId}`, { headers });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "Failed to fetch notebook artifact.");
  }

  return response.json();
}

export async function rerunArtifact(
  artifactId: string
): Promise<{ artifact: NotebookArtifact; notebook: NotebookDocument }> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/artifacts/${artifactId}/rerun`, {
    method: "POST",
    headers,
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "Failed to rerun notebook artifact.");
  }

  return response.json();
}

export async function downloadArtifact(artifactId: string): Promise<void> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/artifacts/${artifactId}/download`, { headers });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    throw new Error("Failed to download notebook artifact.");
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `axiom-${artifactId}.ipynb`;
  link.click();
  URL.revokeObjectURL(url);
}

export async function fetchSources(tenantId: string): Promise<Source[]> {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_URL}/api/sources/${tenantId}`, {
    headers,
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    throw new Error("Failed to fetch sources");
  }
  return response.json();
}

export async function onboardSource(data: SourceIn): Promise<{ status: string; source_id: string }> {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_URL}/api/sources`, {
    method: "POST",
    headers,
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "Failed to onboard source");
  }

  return response.json();
}

export async function deleteSource(tenantId: string, sourceId: string): Promise<{ status: string }> {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_URL}/api/sources/${tenantId}/${sourceId}`, {
    method: "DELETE",
    headers,
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    if (response.status === 403) throw new Error("Forbidden: You do not have permission to delete this source.");
    throw new Error("Failed to delete source");
  }

  return response.json();
}

export async function updateSource(tenantId: string, sourceId: string, data: Partial<SourceIn>): Promise<{ status: string }> {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_URL}/api/sources/${tenantId}/${sourceId}`, {
    method: "PATCH",
    headers,
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    if (response.status === 403) throw new Error("Forbidden: You do not have permission to update this source.");
    throw new Error("Failed to update source");
  }

  return response.json();
}

export async function syncSource(tenantId: string, sourceId: string): Promise<{ status: string }> {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_URL}/api/sources/${tenantId}/${sourceId}/sync`, {
    method: "POST",
    headers,
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    if (response.status === 403) throw new Error("Forbidden: You do not have permission to sync this source.");
    throw new Error("Failed to sync source");
  }

  return response.json();
}
export async function fetchThreads(tenantId: string): Promise<Thread[]> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/threads?tenant_id=${tenantId}`, {
    headers,
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized");
    throw new Error("Failed to fetch threads");
  }
  return response.json();
}

export async function fetchThreadHistory(threadId: string): Promise<ThreadHistory> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/threads/${threadId}`, {
    headers,
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized");
    throw new Error("Failed to fetch thread history");
  }
  return response.json();
}

export async function sendFeedback(data: { 
  thread_id: string, 
  message_id: string, 
  is_correct: boolean, 
  comment?: string 
}): Promise<void> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/api/feedback`, {
    method: "POST",
    headers,
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    throw new Error("Failed to send feedback");
  }
}

// ── Data Lake (Multi-lake support) ──────────────────────────────────────────

export async function fetchLakes(tenantId: string): Promise<LakeOut[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_URL}/api/lakes/${tenantId}`, { headers });
  if (!res.ok) throw new Error("Failed to fetch lakes");
  return res.json();
}

export async function createLake(tenantId: string, data: LakeIn): Promise<LakeOut> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_URL}/api/lakes/${tenantId}`, {
    method: "POST",
    headers,
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create lake");
  return res.json();
}

export async function deleteLake(tenantId: string, lakeId: string): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_URL}/api/lakes/${tenantId}/${lakeId}`, {
    method: "DELETE",
    headers,
  });
  if (!res.ok) throw new Error("Failed to delete lake");
}

export async function fetchLakeSources(lakeId: string): Promise<{ sources: any[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_URL}/api/lake-sources/${lakeId}`, { headers });
  if (!res.ok) throw new Error("Failed to fetch lake sources");
  return res.json();
}

export async function addSourceToLake(lakeId: string, sourceId: string): Promise<{ status: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_URL}/api/lake-sources/${lakeId}/${sourceId}`, {
    method: "POST",
    headers,
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Failed to add source to lake");
  }
  return res.json();
}

export async function removeSourceFromLake(lakeId: string, sourceId: string): Promise<{ status: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_URL}/api/lake-sources/${lakeId}/${sourceId}`, {
    method: "DELETE",
    headers,
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Failed to remove source from lake");
  }
  return res.json();
}

export async function fetchOAuthUrl(data: { connector: string, tenant_id: string, source_id: string }): Promise<{ url: string }> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/api/oauth/url`, {
    method: "POST",
    headers,
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "Failed to generate OAuth URL");
  }

  return response.json();
}
