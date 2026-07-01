import type { Citation } from "@/lib/api";

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
}

function Sources({ items }: { items: Citation[] }) {
  return (
    <div className="mt-3 border-t border-border pt-2">
      <p className="mb-1.5 text-xs font-medium text-muted">Sources</p>
      <ul className="flex flex-wrap gap-1.5">
        {items.map((c, i) => (
          <li
            key={`${c.source}-${i}`}
            title={c.source}
            className="rounded-full border border-border bg-background px-2 py-0.5 text-xs text-muted"
          >
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
        ) : (
          <p className="whitespace-pre-wrap break-words">
            {message.content}
            {message.streaming && (
              <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-current align-middle" />
            )}
          </p>
        )}

        {!isUser && message.citations.length > 0 && (
          <Sources items={message.citations} />
        )}
      </div>
    </div>
  );
}
