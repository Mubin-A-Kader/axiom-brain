import { ApproveRequest, QueryRequest, QueryResponse, Source, SourceIn } from "../types";
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
    }),
  });

  if (!response.ok) {
    if (response.status === 401) throw new Error("Unauthorized. Please log in again.");
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "An error occurred during approval.");
  }

  return response.json();
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