export type Role = "user" | "assistant";
export type MessageStatus = "complete" | "interrupted" | "error";

export interface AttachmentSummary {
  id: string;
  filename: string;
}

export interface Message {
  id: string;
  role: Role;
  content: string;
  thinking?: string;
  status: MessageStatus;
  created_at: string;
  attachments?: AttachmentSummary[];
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
  context_tokens: number;
  context_limit: number;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  upstream: {
    reachable: boolean;
    status: string;
  };
}

export interface StreamMeta {
  conversation_id: string;
  user_message_id: string;
  title: string;
  context_tokens: number;
  context_limit: number;
}

export interface StreamDone {
  message_id: string;
  content: string;
  status: MessageStatus;
  title: string;
  history_truncated: boolean;
  finish_reason: string | null;
  context_tokens: number;
  tokens_per_second: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  thinking: string | null;
}

export interface StreamTitle {
  conversation_id: string;
  title: string;
}

export interface StreamToolSource {
  title: string;
  url: string;
}

export interface StreamToolStart {
  name: string;
  query: string;
}

export interface StreamToolEnd {
  sources: StreamToolSource[];
  error?: string | null;
}

export type ChatEvent =
  | { event: "meta"; data: StreamMeta }
  | { event: "think"; data: { delta: string } }
  | { event: "tool_start"; data: StreamToolStart }
  | { event: "tool_end"; data: StreamToolEnd }
  | { event: "token"; data: { delta: string } }
  | { event: "done"; data: StreamDone }
  | { event: "title"; data: StreamTitle }
  | { event: "error"; data: { detail: string } };
