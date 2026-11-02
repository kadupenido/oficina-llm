import { API_BASE, ApiError } from "./client";
import type { ChatEvent } from "../types";

export async function streamChat(
  body: {
    conversation_id?: string | null;
    message: string;
    regenerate?: boolean;
    temperature?: number;
    max_tokens?: number;
    attachment_ids?: string[];
  },
  signal: AbortSignal,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = "Não foi possível concluir o pedido.";
    try {
      const payload = await response.json();
      if (payload && typeof payload.detail === "string") detail = payload.detail;
    } catch {
      /* sem JSON */
    }
    throw new ApiError(response.status, detail);
  }
  if (!response.body) {
    throw new ApiError(response.status, "O servidor não devolveu um stream.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      const chunk = decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const parsed = pushSseChunk(buffer, chunk);
      buffer = parsed.buffer;
      for (const event of parsed.events) onEvent(event);
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}

export function pushSseChunk(
  buffer: string,
  chunk: string,
): { buffer: string; events: ChatEvent[] } {
  buffer += chunk;
  buffer = buffer.replace(/\r\n/g, "\n");
  const events: ChatEvent[] = [];
  let splitAt = buffer.indexOf("\n\n");
  while (splitAt !== -1) {
    const raw = buffer.slice(0, splitAt);
    buffer = buffer.slice(splitAt + 2);
    const event = parseFrame(raw);
    if (event) events.push(event);
    splitAt = buffer.indexOf("\n\n");
  }
  return { buffer, events };
}

export function parseFrame(raw: string): ChatEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  const data = JSON.parse(dataLines.join("\n")) as ChatEvent["data"];
  if (event === "meta" || event === "think" || event === "tool_start" || event === "tool_end" || event === "token" || event === "done" || event === "title" || event === "error") {
    return { event, data } as ChatEvent;
  }
  return null;
}
