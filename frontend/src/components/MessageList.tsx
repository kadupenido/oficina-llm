import { memo } from "react";
import type { Message } from "../types";
import { AssistantMarkdown } from "./AssistantMarkdown";

interface MessageListProps {
  messages: Message[];
  generating: boolean;
  clipped: boolean;
  webSearchQuery?: string | null;
  webSearchSources?: { title: string; url: string }[];
}

interface BubbleProps {
  message: Message;
  streaming: boolean;
  clipped: boolean;
  webSearchQuery?: string | null;
  webSearchSources?: { title: string; url: string }[];
}

const MessageBubble = memo(function MessageBubble({
  message,
  streaming,
  clipped,
  webSearchQuery,
  webSearchSources,
}: BubbleProps) {
  const waiting = streaming && message.content === "" && !message.thinking && !webSearchQuery;
  const showThinking = Boolean(message.thinking);
  const thinkingOpen = streaming && !message.content;
  return (
    <article className={message.role === "user" ? "ml-8 flex justify-end" : "mr-8"}>
      <div
        className={
          message.role === "user"
            ? "max-w-[85%] rounded-2xl bg-ink-900 px-4 py-3 text-paper-50 dark:bg-pine-800"
            : "max-w-full px-1 py-1"
        }
      >
        {message.role === "assistant" ? (
          <>
            {showThinking && (
              <details open={thinkingOpen} className="mb-3 rounded-lg border border-ink-200/60 px-3 py-2 dark:border-paper-200/20">
                <summary className="cursor-pointer text-xs font-medium text-ink-500">Raciocínio</summary>
                <p className="mt-2 whitespace-pre-wrap break-words text-xs text-ink-500">{message.thinking || "…"}</p>
              </details>
            )}
            {webSearchQuery && streaming && (
              <p className="mb-2 text-xs text-ink-500">Buscando na web: {webSearchQuery}</p>
            )}
            {waiting ? (
              <p className="text-ink-500">Gerando…</p>
            ) : (
              <div className="markdown">
                <AssistantMarkdown content={message.content} />
              </div>
            )}
            {streaming && webSearchSources && webSearchSources.length > 0 && (
              <ul className="mt-3 flex flex-wrap gap-2 text-xs">
                {webSearchSources.map((source) => (
                  <li key={source.url}>
                    <a
                      className="rounded-full border border-ink-200/70 px-2 py-1 text-ink-600 hover:underline dark:border-paper-200/20 dark:text-paper-200"
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {source.title || source.url}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <>
            {message.attachments && message.attachments.length > 0 && (
              <ul className="mb-2 flex flex-wrap gap-2">
                {message.attachments.map((attachment) => (
                  <li
                    key={attachment.id}
                    className="rounded-full bg-paper-50/15 px-2 py-0.5 text-xs text-paper-100"
                  >
                    {attachment.filename}
                  </li>
                ))}
              </ul>
            )}
            {message.content ? (
              <p className="whitespace-pre-wrap break-words">{message.content}</p>
            ) : null}
          </>
        )}
        {message.role === "assistant" && message.status === "interrupted" && (
          <p className="mt-2 text-xs text-ink-500">Geração interrompida.</p>
        )}
        {message.role === "assistant" && message.status === "error" && (
          <p className="mt-2 text-xs text-red-700 dark:text-red-300">A resposta falhou.</p>
        )}
        {clipped && (
          <p className="mt-2 text-xs text-ink-500">A resposta foi cortada pelo limite de tokens.</p>
        )}
      </div>
    </article>
  );
});

export function MessageList({
  messages,
  generating,
  clipped,
  webSearchQuery = null,
  webSearchSources = [],
}: MessageListProps) {
  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 text-center">
        <div>
          <p className="text-2xl font-semibold">O modelo roda nesta máquina</p>
          <p className="mt-2 max-w-md text-ink-500 dark:text-paper-200">
            Escreva uma mensagem. A resposta chega token a token, e o histórico fica no seu backend.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      {messages.map((message, index) => {
        const isLast = index === messages.length - 1;
        const streaming = generating && isLast && message.role === "assistant";
        return (
          <MessageBubble
            key={message.id}
            message={message}
            streaming={streaming}
            clipped={clipped && isLast && message.role === "assistant" && !streaming}
            webSearchQuery={streaming ? webSearchQuery : null}
            webSearchSources={streaming ? webSearchSources : []}
          />
        );
      })}
    </div>
  );
}
