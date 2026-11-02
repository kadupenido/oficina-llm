import type { Conversation, ConversationDetail, HealthResponse } from "../types";

const rawBase = import.meta.env.VITE_API_BASE_URL ?? "";
export const API_BASE = rawBase.replace(/\/$/, "");

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.name === "AbortError") return "Geração interrompida.";
  return "Não foi possível falar com o servidor.";
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    /* corpo vazio ou não JSON */
  }
  return "Não foi possível concluir o pedido.";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw new ApiError(response.status, await readError(response));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function listConversations(): Promise<{ conversations: Conversation[] }> {
  return request("/api/conversations");
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return request(`/api/conversations/${id}`);
}

export function renameConversation(id: string, title: string): Promise<Conversation> {
  return request(`/api/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteConversation(id: string): Promise<void> {
  return request(`/api/conversations/${id}`, { method: "DELETE" });
}

export interface UploadedAttachment {
  id: string;
  filename: string;
  char_count: number;
}

export interface UploadAttachmentsResponse {
  conversation_id: string;
  attachments: UploadedAttachment[];
}

export async function uploadAttachments(
  files: File[],
  conversationId?: string | null,
): Promise<UploadAttachmentsResponse> {
  const form = new FormData();
  if (conversationId) form.append("conversation_id", conversationId);
  for (const file of files) form.append("files", file);
  const response = await fetch(`${API_BASE}/api/attachments`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new ApiError(response.status, await readError(response));
  }
  return response.json() as Promise<UploadAttachmentsResponse>;
}
