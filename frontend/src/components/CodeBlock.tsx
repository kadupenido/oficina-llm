import { isValidElement, useState, type ReactNode } from "react";

function nodeText(node: ReactNode): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return "";
}

function nodeLanguage(node: ReactNode): string {
  if (Array.isArray(node)) {
    for (const child of node) {
      const language = nodeLanguage(child);
      if (language) return language;
    }
    return "";
  }
  if (isValidElement<{ className?: string }>(node)) {
    const match = node.props.className?.match(/language-([\w-]+)/);
    return match?.[1] ?? "";
  }
  return "";
}

export function CodeBlock({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const text = nodeText(children).replace(/\n$/, "");
  const language = nodeLanguage(children);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "true");
      area.style.position = "fixed";
      area.style.left = "-9999px";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-ink-900/10 bg-[#1c1915] text-[#f6f3eb] dark:border-white/10">
      <div className="flex items-center justify-between px-3 py-1.5 text-xs text-white/70">
        <span className="font-mono">{language || "código"}</span>
        <button
          type="button"
          onClick={() => void copy()}
          className="rounded-md px-2 py-1 hover:bg-white/10"
        >
          {copied ? "Copiado" : "Copiar"}
        </button>
      </div>
      <pre className="overflow-x-auto px-3 pb-3 font-mono text-sm leading-relaxed">
        <code>{text}</code>
      </pre>
    </div>
  );
}
