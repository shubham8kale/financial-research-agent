"use client";

import { useCallback, useState } from "react";
import CompanySelect from "@/components/CompanySelect";
import ChatInput from "@/components/ChatInput";
import MessageList from "@/components/MessageList";
import type { ChatMessage } from "@/components/Message";
import { streamQuery, type Citation } from "@/lib/api";

// Each suggestion is verified against the live backend before shipping: a
// prompt the UI offers should not be one the corpus answers badly.
//
// "What are the main risk factors Meta discloses?" was removed. It is a fair
// question and the system handles it honestly - it retrieves 12 passages,
// finds none containing the Item 1A text, and declines rather than
// confabulating - but a suggestion that reliably produces a refusal is a poor
// first impression. The cause is retrieval: a broad "main risk factors" query
// does not surface the risk-factor sections.
//
// Both financial prompts say "most recent fiscal year" deliberately. The
// filings present three years side by side, and without that phrase the agent
// answers with the PRIOR year - measured at 4 of 5 on the benchmark's temporal
// stratum (see eval/EVALUATION.md finding 2). The wording steers the demo
// around a real, documented defect; it does not fix it.
const EXAMPLE_QUESTIONS = [
  "What were Apple's total net sales in the most recent fiscal year?",
  "Compare Microsoft and Alphabet cloud revenue in their most recent fiscal years.",
  "How many employees did Meta have at the end of 2025?",
];

function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 text-center">
      <div>
        <h2 className="text-xl font-semibold text-foreground">
          Ask about SEC 10-K filings
        </h2>
        <p className="mt-1 text-sm text-muted">
          Streamed, source-grounded answers over five companies&apos; annual
          reports.
        </p>
      </div>
      <div className="flex flex-col gap-2">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="rounded-lg border border-border bg-surface px-4 py-2 text-sm text-muted shadow-sm transition-colors hover:border-accent hover:text-foreground"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function Home() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [ticker, setTicker] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);

  const send = useCallback(
    (text: string) => {
      if (isStreaming) return;

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: text,
        citations: [],
        streaming: false,
      };
      const assistantId = crypto.randomUUID();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        citations: [],
        streaming: true,
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsStreaming(true);

      const patch = (fn: (m: ChatMessage) => ChatMessage) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? fn(m) : m)),
        );

      void streamQuery({
        question: text,
        ticker,
        onToken: (t) => patch((m) => ({ ...m, content: m.content + t })),
        onSources: (items: Citation[]) =>
          patch((m) => ({ ...m, citations: items })),
        onDone: () => {
          patch((m) => ({ ...m, streaming: false }));
          setIsStreaming(false);
        },
        onError: (message) => {
          patch((m) => ({
            ...m,
            streaming: false,
            error: m.content
              ? `${m.content}\n\n(Stream interrupted: ${message})`
              : `Something went wrong: ${message}`,
          }));
          setIsStreaming(false);
        },
      });
    },
    [isStreaming, ticker],
  );

  return (
    <div className="flex h-dvh flex-col">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-4 px-4 py-3">
          <div>
            <h1 className="text-base font-semibold text-foreground">
              Financial Research Agent
            </h1>
            <p className="text-xs text-muted">SEC 10-K research, streamed</p>
          </div>
          <CompanySelect
            value={ticker}
            onChange={setTicker}
            disabled={isStreaming}
          />
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto h-full w-full max-w-3xl px-4 py-6">
          {messages.length === 0 ? (
            <EmptyState onPick={send} />
          ) : (
            <MessageList messages={messages} />
          )}
        </div>
      </main>

      <footer className="border-t border-border bg-surface">
        <div className="mx-auto w-full max-w-3xl px-4 py-3">
          <ChatInput onSend={send} disabled={isStreaming} />
          <p className="mt-1.5 text-center text-xs text-muted">
            Answers are grounded in the filing text. Enter to send, Shift+Enter
            for a newline.
          </p>
        </div>
      </footer>
    </div>
  );
}
