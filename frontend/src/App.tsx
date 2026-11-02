import { useEffect, useRef, useState } from "react";
import { getHealth } from "./api/client";
import { Composer } from "./components/Composer";
import { MessageList } from "./components/MessageList";
import { Sidebar } from "./components/Sidebar";
import { useChat } from "./hooks/useChat";
import { useTheme } from "./hooks/useTheme";

export default function App() {
  const chat = useChat();
  const { theme, toggle } = useTheme();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [modelReady, setModelReady] = useState<boolean | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const health = await getHealth();
        if (!cancelled) setModelReady(health.status === "ok");
      } catch {
        if (!cancelled) setModelReady(false);
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 20000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!stick.current) return;
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [chat.messages, chat.generating]);

  const modelLabel =
    modelReady === null ? "Verificando o modelo…" : modelReady ? "Modelo pronto" : "Modelo indisponível";

  return (
    <div className="flex h-full bg-paper-50 text-ink-900 dark:bg-[#14120f] dark:text-paper-50">
      <Sidebar
        conversations={chat.conversations}
        activeId={chat.activeId}
        loading={chat.loadingList}
        listError={chat.listError}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onSelect={chat.selectConversation}
        onNew={chat.startNew}
        onRename={chat.rename}
        onDelete={chat.remove}
        themeLabel={theme === "dark" ? "Tema claro" : "Tema escuro"}
        onToggleTheme={toggle}
        tokensPerSecond={chat.tokensPerSecond}
        contextTokens={chat.contextTokens}
        contextLimit={chat.contextLimit}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-ink-900/10 px-4 py-3 dark:border-white/10">
          <button
            type="button"
            className="rounded-lg border border-ink-900/15 px-2 py-1 text-sm md:hidden dark:border-white/15"
            onClick={() => setSidebarOpen(true)}
          >
            Conversas
          </button>
          <div className="min-w-0 flex-1">
            <h2 className="truncate font-semibold">{chat.activeTitle}</h2>
            <p className="text-xs text-ink-500 dark:text-paper-200">{modelLabel}</p>
          </div>
          <button type="button" onClick={toggle} className="hidden text-sm md:block">
            {theme === "dark" ? "Tema claro" : "Tema escuro"}
          </button>
        </header>
        <div
          ref={scroller}
          onScroll={(event) => {
            const element = event.currentTarget;
            stick.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
          }}
          className="flex-1 overflow-y-auto px-4 py-6"
        >
          <MessageList
            messages={chat.messages}
            generating={chat.generating}
            clipped={chat.finishReason === "length"}
            webSearchQuery={chat.webSearchQuery}
            webSearchSources={chat.webSearchSources}
          />
        </div>
        <div className="space-y-3 border-t border-ink-900/10 px-4 py-4 dark:border-white/10">
          {chat.historyTruncated && (
            <p className="mx-auto max-w-3xl text-xs text-ink-500">
              Mensagens antigas foram omitidas para caber no contexto.
            </p>
          )}
          {chat.error && (
            <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 rounded-xl bg-red-100 px-3 py-2 text-sm text-red-900 dark:bg-red-950 dark:text-red-100">
              <span>{chat.error}</span>
              {chat.showRetry && (
                <button type="button" className="shrink-0 underline" onClick={() => void chat.retry()}>
                  Tentar novamente
                </button>
              )}
            </div>
          )}
          {!chat.error && chat.showRetry && (
            <div className="mx-auto max-w-3xl">
              <button type="button" className="text-sm underline" onClick={() => void chat.retry()}>
                Tentar novamente
              </button>
            </div>
          )}
          <Composer
            value={chat.draft}
            generating={chat.generating}
            pendingFiles={chat.pendingFiles}
            onChange={chat.setDraft}
            onAddFiles={chat.addPendingFiles}
            onRemoveFile={chat.removePendingFile}
            onSend={() => void chat.send(chat.draft)}
            onStop={chat.stop}
          />
        </div>
      </main>
    </div>
  );
}
