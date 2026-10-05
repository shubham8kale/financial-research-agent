import ReactMarkdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AnswerMeta, AnswerVerification, Citation } from "@/lib/api";

/**
 * Chat-bubble scale for rendered markdown: the model writes headings, bold,
 * lists and tables, and none of it may look page-sized inside a bubble.
 * Preflight resets list and table styling, so those are put back here.
 * Raw HTML in the model's output is never rendered: there is deliberately no
 * rehype-raw, so react-markdown shows such text as text.
 */
// singleTilde is off: with it on, "~$5B to (~$7B)" strikes a real figure through, and "~" means "about" here.
const remarkPlugins: Options["remarkPlugins"] = [[remarkGfm, { singleTilde: false }]];

const markdownComponents: Components = {
  h1: ({ children }) => <h1 className="mt-3 text-sm font-bold first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-3 text-sm font-bold first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-3 text-sm font-bold first:mt-0">{children}</h3>,
  h4: ({ children }) => <h4 className="mt-3 text-sm font-bold first:mt-0">{children}</h4>,
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 list-disc space-y-0.5 pl-5 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal space-y-0.5 pl-5 last:mb-0">{children}</ol>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  // Markdown image syntax would make the browser fetch whatever URL the model wrote (a prompt-injected filing could
  // use that to leak the conversation); show the alt text instead and never load an image.
  img: ({ alt }) => <span>{alt}</span>,
  // react-markdown blanks an unsafe URL (javascript:, data:) to "", and an <a href=""> is still a live link, so an
  // empty href renders as plain text instead.
  a: ({ href, children }) =>
    href ? (
      <a href={href} target="_blank" rel="noopener noreferrer" className="underline">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children, style }) => (
    <th style={style} className="border border-border px-2 py-1 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children, style }) => (
    <td style={style} className="border border-border px-2 py-1">
      {children}
    </td>
  ),
};

/** UI model for one turn in the conversation (session-only, never persisted). */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  /** True while the assistant message is still receiving tokens. */
  streaming: boolean;
  /** Set when the stream failed; replaces the content in the UI. */
  error?: string;
  /** Latency, tokens, cost, tools and trace id; arrives after the sources. */
  meta?: AnswerMeta;
  /** The output contract's verdict; arrives after the sources. */
  verification?: AnswerVerification;
}

/**
 * One line above the sources: did the answer verify against what was
 * retrieved? Every count comes from the backend's deterministic check.
 */
function Verification({ verification }: { verification: AnswerVerification }) {
  if (verification.status === "skipped") return null;
  const figures =
    verification.n_figures && verification.n_figures > 0
      ? `${verification.n_supported ?? 0}/${verification.n_figures} figures found in cited sources`
      : "no figures to check";
  const first = verification.failures?.[0];
  let tone = "text-muted";
  let text = "";
  if (verification.status === "verified") {
    tone = "text-emerald-700";
    text = `✓ Verified · ${verification.n_claims ?? 0} claims · ${figures}${
      verification.repaired ? " · after one repair" : ""
    }`;
  } else if (verification.status === "unverified") {
    tone = "text-amber-700";
    text = `⚠ Unverified · ${first ? `${first.check.replace("_", " ")}: ${first.detail}` : "see failures"}`;
  } else {
    tone = "text-red-700";
    text = "⛔ Answer withheld: it could not be verified against the filings";
  }
  return (
    <p className={`mt-2 text-[11px] leading-4 ${tone}`} aria-label="Verification">
      {text}
    </p>
  );
}

function formatTools(tools: Record<string, number>): string {
  return Object.entries(tools)
    .map(([name, n]) => (n > 1 ? `${name} ×${n}` : name))
    .join(", ");
}

/** One line under the answer: what it cost. Every number comes from the backend's own meter. */
function Meta({ meta }: { meta: AnswerMeta }) {
  const parts: string[] = [];
  if (meta.latency_ms != null) parts.push(`${(meta.latency_ms / 1000).toFixed(1)} s`);
  if (meta.input_tokens != null && meta.output_tokens != null)
    parts.push(`${(meta.input_tokens + meta.output_tokens).toLocaleString()} tokens`);
  if (meta.cost_usd != null) parts.push(`$${meta.cost_usd.toFixed(4)}`);
  const tools = formatTools(meta.tools ?? {});
  if (tools) parts.push(`tools: ${tools}`);
  return (
    <p
      className="mt-2 text-[11px] leading-4 text-muted"
      aria-label="Answer cost"
      title={meta.trace_id ? `trace ${meta.trace_id}` : undefined}
    >
      {parts.join(" · ")}
      {meta.trace_id ? ` · trace ${meta.trace_id.slice(0, 8)}` : ""}
    </p>
  );
}

function Sources({ items }: { items: Citation[] }) {
  return (
    <div className="mt-3 border-t border-border pt-2">
      <p className="mb-1.5 text-xs font-medium text-muted">Sources</p>
      <ul className="flex flex-wrap gap-1.5">
        {items.map((c, i) => (
          <li
            key={`${c.source}-${i}`}
            title={c.cited ? `${c.source} · cited by a verified claim` : c.source}
            className={`rounded-full border px-2 py-0.5 text-xs ${
              c.cited
                ? "border-emerald-600 bg-background text-emerald-700"
                : "border-border bg-background text-muted"
            }`}
          >
            {c.cited ? "✓ " : ""}
            {c.ticker}
            {c.chunk_idx ? ` · chunk ${c.chunk_idx}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TypingDots() {
  return (
    <span className="inline-flex gap-1 py-1" aria-label="Assistant is thinking">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" />
    </span>
  );
}

export default function Message({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  // Loading state: assistant is streaming but no tokens have arrived yet.
  const isPending =
    message.role === "assistant" &&
    message.streaming &&
    message.content === "" &&
    !message.error;

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm shadow-sm sm:max-w-[75%] ${
          isUser
            ? "bg-accent text-accent-foreground"
            : "border border-border bg-surface text-foreground"
        }`}
      >
        {message.error ? (
          <p className="text-red-600">{message.error}</p>
        ) : isPending ? (
          <TypingDots />
        ) : isUser ? (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        ) : (
          <div className="break-words">
            <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>
              {message.content}
            </ReactMarkdown>
            {message.streaming && (
              <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-current align-middle" />
            )}
          </div>
        )}

        {!isUser && !message.streaming && message.verification && (
          <Verification verification={message.verification} />
        )}
        {!isUser && message.citations.length > 0 && (
          <Sources items={message.citations} />
        )}
        {!isUser && !message.streaming && message.meta && <Meta meta={message.meta} />}
      </div>
    </div>
  );
}
