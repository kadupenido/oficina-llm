import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteConversation,
  errorMessage,
  getConversation,
  listConversations,
  renameConversation,
  uploadAttachments,
} from "../api/client";
import { streamChat } from "../api/sse";
import type { AttachmentSummary, Conversation, Message } from "../types";

interface PendingFile {
  key: string;
  file: File;
}

function temporaryMessage(role: Message["role"], content: string): Message {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    status: "complete",
    created_at: new Date().toISOString(),
  };
}

export function canRetry(messages: Message[]): boolean {
  if (messages.length === 0) return false;
  const last = messages[messages.length - 1];
  if (last.role === "user") return true;
  return last.role === "assistant" && last.status !== "complete";
}

function lastUserText(messages: Message[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === "user") return messages[index].content;
  }
  return null;
}

export function useChat() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [historyTruncated, setHistoryTruncated] = useState(false);
  const [finishReason, setFinishReason] = useState<string | null>(null);
  const [tokensPerSecond, setTokensPerSecond] = useState<number | null>(null);
  const [contextTokens, setContextTokens] = useState<number | null>(null);
  const [contextLimit, setContextLimit] = useState<number | null>(null);
  const [webSearchQuery, setWebSearchQuery] = useState<string | null>(null);
  const [webSearchSources, setWebSearchSources] = useState<{ title: string; url: string }[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const stoppedByUser = useRef(false);
  const activeIdRef = useRef<string | null>(null);
  const skipLoad = useRef(false);
  const generation = useRef(0);
  const deltaRef = useRef("");
  const thinkDeltaRef = useRef("");
  const frameRef = useRef<number | null>(null);
  const thinkFrameRef = useRef<number | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const statsRef = useRef({ base: 0, generated: 0, started: null as number | null });

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  const refreshList = useCallback(async () => {
    const payload = await listConversations();
    setConversations(payload.conversations);
    setListError(null);
    return payload.conversations;
  }, []);

  const reloadAfterStop = useCallback(async (id: string, stopped: boolean) => {
    const attempts = stopped ? 8 : 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const detail = await getConversation(id);
      if (activeIdRef.current !== id) return;
      const last = detail.messages[detail.messages.length - 1];
      if (!stopped || last?.role === "assistant" || attempt === attempts - 1) {
        setMessages(detail.messages);
        setHistoryTruncated(false);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    refreshList()
      .then((items) => {
        if (cancelled) return;
        if (items[0]) setActiveId(items[0].id);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setListError(errorMessage(reason));
      })
      .finally(() => {
        if (!cancelled) setLoadingList(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshList]);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      statsRef.current = { base: 0, generated: 0, started: null };
      setTokensPerSecond(null);
      setContextTokens(null);
      setContextLimit(null);
      return;
    }
    if (skipLoad.current) {
      skipLoad.current = false;
      return;
    }
    let cancelled = false;
    setTokensPerSecond(null);
    getConversation(activeId)
      .then((detail) => {
        if (cancelled) return;
        statsRef.current = { base: detail.context_tokens, generated: 0, started: null };
        setMessages(detail.messages);
        setContextTokens(detail.context_tokens);
        setContextLimit(detail.context_limit);
        setTokensPerSecond(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(errorMessage(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  const stop = useCallback(() => {
    stoppedByUser.current = true;
    abortRef.current?.abort();
  }, []);

  const cancelDelta = useCallback(() => {
    if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    deltaRef.current = "";
    if (thinkFrameRef.current != null) cancelAnimationFrame(thinkFrameRef.current);
    thinkFrameRef.current = null;
    thinkDeltaRef.current = "";
  }, []);

  const queueThinkDelta = useCallback((id: string, delta: string) => {
    assistantIdRef.current = id;
    thinkDeltaRef.current += delta;
    if (thinkFrameRef.current != null) return;
    thinkFrameRef.current = requestAnimationFrame(() => {
      thinkFrameRef.current = null;
      const extra = thinkDeltaRef.current;
      thinkDeltaRef.current = "";
      if (!extra) return;
      setMessages((current) =>
        current.map((item) =>
          item.id === id ? { ...item, thinking: (item.thinking ?? "") + extra } : item,
        ),
      );
    });
  }, []);

  const queueDelta = useCallback((id: string, delta: string) => {
    assistantIdRef.current = id;
    deltaRef.current += delta;
    if (frameRef.current != null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const extra = deltaRef.current;
      deltaRef.current = "";
      if (!extra) return;
      setMessages((current) =>
        current.map((item) => (item.id === id ? { ...item, content: item.content + extra } : item)),
      );
    });
  }, []);

  const send = useCallback(
    async (text: string, options?: { regenerate?: boolean }) => {
      const trimmed = text.trim();
      const regenerating = options?.regenerate === true;
      const filesToUpload = pendingFiles.map((item) => item.file);
      if ((!trimmed && filesToUpload.length === 0) || generating) return;
      setError(null);
      setHistoryTruncated(false);
      setFinishReason(null);
      setWebSearchQuery(null);
      setWebSearchSources([]);
      stoppedByUser.current = false;
      const ticket = generation.current + 1;
      generation.current = ticket;
      cancelDelta();

      let attachmentIds: string[] = [];
      let attachmentSummaries: AttachmentSummary[] = [];
      let conversationId = activeIdRef.current;
      if (!regenerating && filesToUpload.length > 0) {
        try {
          const uploaded = await uploadAttachments(filesToUpload, conversationId);
          conversationId = uploaded.conversation_id;
          if (activeIdRef.current !== conversationId) skipLoad.current = true;
          activeIdRef.current = conversationId;
          setActiveId(conversationId);
          attachmentIds = uploaded.attachments.map((item) => item.id);
          attachmentSummaries = uploaded.attachments.map((item) => ({
            id: item.id,
            filename: item.filename,
          }));
          setPendingFiles([]);
          setConversations((current) => {
            const exists = current.some((item) => item.id === conversationId);
            const entry: Conversation = {
              id: conversationId!,
              title: uploaded.attachments[0]?.filename ?? "Nova conversa",
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            };
            if (exists) {
              return current.map((item) =>
                item.id === entry.id ? { ...item, updated_at: entry.updated_at } : item,
              );
            }
            return [entry, ...current];
          });
        } catch (reason: unknown) {
          setError(errorMessage(reason));
          return;
        }
      }

      const assistantId = crypto.randomUUID();
      assistantIdRef.current = assistantId;
      setMessages((current) => {
        if (regenerating) {
          return [...current, { ...temporaryMessage("assistant", ""), id: assistantId }];
        }
        return [
          ...current,
          {
            ...temporaryMessage("user", trimmed),
            attachments: attachmentSummaries.length ? attachmentSummaries : undefined,
          },
          { ...temporaryMessage("assistant", ""), id: assistantId },
        ];
      });
      setDraft("");
      setGenerating(true);

      const controller = new AbortController();
      abortRef.current = controller;
      let sawTerminal = false;
      try {
        await streamChat(
          {
            conversation_id: conversationId,
            message: trimmed,
            regenerate: regenerating || undefined,
            attachment_ids: attachmentIds.length ? attachmentIds : undefined,
          },
          controller.signal,
          (event) => {
            if (generation.current !== ticket) return;
            if (event.event === "meta") {
              if (activeIdRef.current !== event.data.conversation_id) skipLoad.current = true;
              activeIdRef.current = event.data.conversation_id;
              setActiveId(event.data.conversation_id);
              setConversations((current) => {
                const exists = current.some((item) => item.id === event.data.conversation_id);
                const entry: Conversation = {
                  id: event.data.conversation_id,
                  title: event.data.title,
                  created_at: new Date().toISOString(),
                  updated_at: new Date().toISOString(),
                };
                if (exists) {
                  return current.map((item) =>
                    item.id === entry.id ? { ...item, title: entry.title, updated_at: entry.updated_at } : item,
                  );
                }
                return [entry, ...current];
              });
              statsRef.current = { base: event.data.context_tokens, generated: 0, started: null };
              setContextTokens(event.data.context_tokens);
              setContextLimit(event.data.context_limit);
              setTokensPerSecond(null);
            } else if (event.event === "think") {
              queueThinkDelta(assistantId, event.data.delta);
            } else if (event.event === "tool_start") {
              setWebSearchQuery(event.data.query);
              setWebSearchSources([]);
            } else if (event.event === "tool_end") {
              setWebSearchSources(event.data.sources ?? []);
            } else if (event.event === "token") {
              queueDelta(assistantId, event.data.delta);
              const stats = statsRef.current;
              stats.generated += 1;
              if (stats.started == null) stats.started = performance.now();
              const elapsed = (performance.now() - stats.started) / 1000;
              setContextTokens(stats.base + stats.generated);
              if (elapsed > 0) setTokensPerSecond(stats.generated / elapsed);
            } else if (event.event === "done") {
              sawTerminal = true;
              cancelDelta();
              setGenerating(false);
              setWebSearchQuery(null);
              setFinishReason(event.data.finish_reason);
              setHistoryTruncated(event.data.history_truncated);
              if (event.data.prompt_tokens != null) {
                statsRef.current = { base: event.data.context_tokens, generated: 0, started: null };
                setContextTokens(event.data.context_tokens);
              }
              if (event.data.tokens_per_second != null) {
                setTokensPerSecond(event.data.tokens_per_second);
              }
              setMessages((current) =>
                current.map((item) =>
                  item.id === assistantId
                    ? {
                        ...item,
                        id: event.data.message_id,
                        content: event.data.content,
                        status: event.data.status,
                        ...(event.data.thinking != null && event.data.thinking !== ""
                          ? { thinking: event.data.thinking }
                          : item.thinking
                            ? { thinking: item.thinking }
                            : {}),
                      }
                    : item,
                ),
              );
              setConversations((current) =>
                current.map((item) =>
                  item.id === activeIdRef.current
                    ? { ...item, title: event.data.title, updated_at: new Date().toISOString() }
                    : item,
                ),
              );
            } else if (event.event === "title") {
              setConversations((current) =>
                current.map((item) =>
                  item.id === event.data.conversation_id
                    ? { ...item, title: event.data.title, updated_at: new Date().toISOString() }
                    : item,
                ),
              );
            } else if (event.event === "error") {
              sawTerminal = true;
              cancelDelta();
              setError(event.data.detail);
            }
          },
        );
      } catch (reason) {
        if (generation.current !== ticket) return;
        cancelDelta();
        if (controller.signal.aborted && stoppedByUser.current) {
          setError(null);
        } else if (!sawTerminal) {
          setError(errorMessage(reason));
        }
      } finally {
        if (generation.current === ticket) setGenerating(false);
        abortRef.current = null;
        if (generation.current !== ticket) return;
        const id = activeIdRef.current;
        const stopped = stoppedByUser.current;
        try {
          await refreshList();
          if (id) await reloadAfterStop(id, stopped);
        } catch (reason) {
          setListError(errorMessage(reason));
        }
      }
    },
    [cancelDelta, generating, pendingFiles, queueDelta, queueThinkDelta, refreshList, reloadAfterStop],
  );

  const addPendingFiles = useCallback((files: FileList | File[]) => {
    const next = Array.from(files).map((file) => ({ key: crypto.randomUUID(), file }));
    if (!next.length) return;
    setPendingFiles((current) => [...current, ...next]);
  }, []);

  const removePendingFile = useCallback((key: string) => {
    setPendingFiles((current) => current.filter((item) => item.key !== key));
  }, []);

  const retry = useCallback(async () => {
    const text = lastUserText(messages);
    if (text) await send(text, { regenerate: true });
  }, [messages, send]);

  const startNew = useCallback(() => {
    generation.current += 1;
    if (generating) stop();
    activeIdRef.current = null;
    setActiveId(null);
    setMessages([]);
    setDraft("");
    setPendingFiles([]);
    setError(null);
    setHistoryTruncated(false);
    setFinishReason(null);
    statsRef.current = { base: 0, generated: 0, started: null };
    setTokensPerSecond(null);
    setContextTokens(null);
    setContextLimit(null);
    setWebSearchQuery(null);
    setWebSearchSources([]);
  }, [generating, stop]);

  const selectConversation = useCallback(
    (id: string) => {
      generation.current += 1;
      if (generating) stop();
      setError(null);
      setHistoryTruncated(false);
      setFinishReason(null);
      if (activeIdRef.current !== id) {
        statsRef.current = { base: 0, generated: 0, started: null };
        setTokensPerSecond(null);
        setContextTokens(null);
        setContextLimit(null);
      }
      activeIdRef.current = id;
      setActiveId(id);
    },
    [generating, stop],
  );

  const rename = useCallback(
    async (id: string, title: string) => {
      const updated = await renameConversation(id, title);
      setConversations((current) =>
        current.map((item) => (item.id === id ? { ...item, title: updated.title } : item)),
      );
    },
    [],
  );

  const remove = useCallback(
    async (id: string) => {
      await deleteConversation(id);
      if (activeIdRef.current === id) {
        activeIdRef.current = null;
        setActiveId(null);
        setMessages([]);
      }
      await refreshList();
    },
    [refreshList],
  );

  const activeTitle =
    conversations.find((item) => item.id === activeId)?.title ?? "Nova conversa";

  return {
    conversations,
    activeId,
    activeTitle,
    messages,
    draft,
    setDraft,
    pendingFiles,
    addPendingFiles,
    removePendingFile,
    generating,
    error,
    listError,
    historyTruncated,
    finishReason,
    tokensPerSecond,
    contextTokens,
    contextLimit,
    webSearchQuery,
    webSearchSources,
    loadingList,
    send,
    stop,
    retry,
    startNew,
    selectConversation,
    rename,
    remove,
    showRetry: !generating && canRetry(messages),
  };
}
