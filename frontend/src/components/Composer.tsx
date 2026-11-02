import { useRef } from "react";

interface PendingFile {
  key: string;
  file: File;
}

interface ComposerProps {
  value: string;
  generating: boolean;
  pendingFiles: PendingFile[];
  onChange: (value: string) => void;
  onAddFiles: (files: FileList | File[]) => void;
  onRemoveFile: (key: string) => void;
  onSend: () => void;
  onStop: () => void;
}

export function Composer({
  value,
  generating,
  pendingFiles,
  onChange,
  onAddFiles,
  onRemoveFile,
  onSend,
  onStop,
}: ComposerProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const canSend = Boolean(value.trim() || pendingFiles.length);

  return (
    <div className="mx-auto w-full max-w-3xl space-y-2">
      {pendingFiles.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {pendingFiles.map((item) => (
            <li
              key={item.key}
              className="flex items-center gap-1 rounded-full border border-ink-900/15 bg-white px-3 py-1 text-xs dark:border-white/10 dark:bg-[#2a241d]"
            >
              <span className="max-w-[12rem] truncate">{item.file.name}</span>
              {!generating && (
                <button
                  type="button"
                  className="text-ink-500 hover:text-ink-900 dark:hover:text-paper-50"
                  aria-label={`Remover ${item.file.name}`}
                  onClick={() => onRemoveFile(item.key)}
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex w-full items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!generating && canSend) onSend();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="sr-only"
          onChange={(event) => {
            if (event.target.files?.length) onAddFiles(event.target.files);
            event.target.value = "";
          }}
        />
        <button
          type="button"
          disabled={generating}
          className="shrink-0 rounded-2xl border border-ink-900/15 px-3 py-3 text-sm disabled:opacity-40 dark:border-white/10"
          onClick={() => inputRef.current?.click()}
        >
          Anexar
        </button>
        <label className="sr-only" htmlFor="composer">
          Mensagem
        </label>
        <textarea
          id="composer"
          rows={1}
          value={value}
          maxLength={32000}
          placeholder="Escreva uma mensagem"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (!generating && canSend) onSend();
            }
          }}
          className="max-h-40 min-h-12 flex-1 resize-y rounded-2xl border border-ink-900/15 bg-white px-4 py-3 outline-none ring-pine-500 focus:ring-2 dark:border-white/10 dark:bg-[#2a241d]"
        />
        {generating ? (
          <button
            type="button"
            onClick={onStop}
            className="rounded-full bg-ink-900 px-4 py-3 text-sm text-white dark:bg-paper-100 dark:text-ink-900"
          >
            Parar
          </button>
        ) : (
          <button
            type="submit"
            disabled={!canSend}
            className="rounded-full bg-pine-700 px-4 py-3 text-sm text-white disabled:opacity-40"
          >
            Enviar
          </button>
        )}
      </form>
    </div>
  );
}
