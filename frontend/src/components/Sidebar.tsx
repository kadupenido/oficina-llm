import { useState } from "react";
import { contextBarWidth, contextPercent, formatTokenCount, formatTokensPerSecond } from "../hooks/contextStats";
import type { Conversation } from "../types";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  loading: boolean;
  listError: string | null;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  themeLabel: string;
  onToggleTheme: () => void;
  tokensPerSecond: number | null;
  contextTokens: number | null;
  contextLimit: number | null;
}

export function Sidebar({
  conversations,
  activeId,
  loading,
  listError,
  open,
  onClose,
  onSelect,
  onNew,
  onRename,
  onDelete,
  themeLabel,
  onToggleTheme,
  tokensPerSecond,
  contextTokens,
  contextLimit,
}: SidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function saveTitle(id: string) {
    const title = titleDraft.trim();
    if (!title) return;
    try {
      await onRename(id, title);
      setEditingId(null);
      setActionError(null);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Não foi possível renomear.");
    }
  }

  async function confirmDelete(id: string) {
    try {
      await onDelete(id);
      setPendingDelete(null);
      setActionError(null);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Não foi possível apagar.");
    }
  }

  return (
    <>
      <button
        type="button"
        aria-label="Fechar conversas"
        className={`fixed inset-0 z-20 bg-ink-900/40 md:hidden ${open ? "block" : "hidden"}`}
        onClick={onClose}
      />
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-80 flex-col border-r border-ink-900/10 bg-paper-100 dark:border-white/10 dark:bg-[#221e19] md:static md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        } transition-transform`}
      >
        <div className="flex items-center justify-between gap-3 px-4 py-4">
          <div>
            <p className="text-xs uppercase tracking-[0.16em] text-ink-500 dark:text-paper-200">Oficina LLM</p>
            <h1 className="text-lg font-semibold">Conversas</h1>
          </div>
          <button
            type="button"
            onClick={() => {
              onNew();
              onClose();
            }}
            className="rounded-full bg-pine-700 px-3 py-1.5 text-sm text-white hover:bg-pine-800"
          >
            Nova
          </button>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
          {loading && <p className="px-2 text-sm text-ink-500">Carregando…</p>}
          {listError && <p className="px-2 text-sm text-red-700 dark:text-red-300">{listError}</p>}
          {!loading && conversations.length === 0 && (
            <p className="px-2 text-sm text-ink-500 dark:text-paper-200">Nenhuma conversa ainda.</p>
          )}
          {conversations.map((conversation) => {
            const active = conversation.id === activeId;
            return (
              <div
                key={conversation.id}
                className={`rounded-xl px-2 py-2 ${active ? "bg-white dark:bg-white/10" : "hover:bg-white/60 dark:hover:bg-white/5"}`}
              >
                {editingId === conversation.id ? (
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      void saveTitle(conversation.id);
                    }}
                  >
                    <input
                      autoFocus
                      value={titleDraft}
                      onChange={(event) => setTitleDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Escape") setEditingId(null);
                      }}
                      className="w-full rounded-lg border border-ink-900/15 bg-transparent px-2 py-1 text-sm outline-none"
                      aria-label="Novo título"
                    />
                  </form>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      onSelect(conversation.id);
                      onClose();
                    }}
                    className="w-full truncate text-left text-sm"
                  >
                    {conversation.title}
                  </button>
                )}
                <div className="mt-1 flex gap-2 text-xs text-ink-500 dark:text-paper-200">
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(conversation.id);
                      setTitleDraft(conversation.title);
                      setPendingDelete(null);
                    }}
                  >
                    Renomear
                  </button>
                  {pendingDelete === conversation.id ? (
                    <button type="button" className="text-red-700 dark:text-red-300" onClick={() => void confirmDelete(conversation.id)}>
                      Confirmar
                    </button>
                  ) : (
                    <button type="button" onClick={() => setPendingDelete(conversation.id)}>
                      Apagar
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          {actionError && <p className="px-2 text-sm text-red-700 dark:text-red-300">{actionError}</p>}
        </div>
        <div className="border-t border-ink-900/10 p-3 dark:border-white/10">
          {activeId && contextTokens != null && contextLimit != null && (
            <div className="mb-3 px-3">
              {tokensPerSecond != null && (
                <p className="text-sm tabular-nums">{formatTokensPerSecond(tokensPerSecond)}</p>
              )}
              <p className="text-xs tabular-nums text-ink-500 dark:text-paper-200">
                {formatTokenCount(contextTokens)} / {formatTokenCount(contextLimit)} · {contextPercent(contextTokens, contextLimit)}%
              </p>
              <div
                className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-900/10 dark:bg-white/10"
                role="meter"
                aria-label="Contexto da conversa"
                aria-valuemin={0}
                aria-valuemax={contextLimit}
                aria-valuenow={Math.min(contextLimit, Math.max(0, contextTokens))}
              >
                <div
                  className="h-full rounded-full bg-pine-700"
                  style={{ width: `${contextBarWidth(contextTokens, contextLimit)}%` }}
                />
              </div>
            </div>
          )}
          <button type="button" onClick={onToggleTheme} className="w-full rounded-xl px-3 py-2 text-left text-sm hover:bg-white/70 dark:hover:bg-white/10">
            {themeLabel}
          </button>
        </div>
      </aside>
    </>
  );
}
