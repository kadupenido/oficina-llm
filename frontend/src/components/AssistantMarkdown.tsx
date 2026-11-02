import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { CodeBlock } from "./CodeBlock";

const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    td: [...(defaultSchema.attributes?.td ?? []), "colspan", "rowspan"],
    th: [...(defaultSchema.attributes?.th ?? []), "colspan", "rowspan"],
  },
};

interface AssistantMarkdownProps {
  content: string;
}

export function AssistantMarkdown({ content }: AssistantMarkdownProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema]]}
      components={{
        pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        table: ({ children }) => (
          <div className="markdown-table-wrap">
            <table>{children}</table>
          </div>
        ),
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        ),
      }}
    >
      {content || " "}
    </ReactMarkdown>
  );
}
