/**
 * Typed client for the FRA streaming endpoint.
 *
 * We POST to /query/stream and parse Server-Sent Events with fetch +
 * ReadableStream (NOT EventSource — EventSource only supports GET, and we need
 * to send a JSON body). The backend emits one JSON object per SSE `data:` line.
 */
import { API_BASE_URL } from "./config";

// ── Backend event contract (mirrors api/main.py) ────────────────────────────────

export interface Citation {
  ticker: string;
  chunk_idx: string;
  source: string;
}

export interface TokenEvent {
  type: "token";
  text: string;
}

export interface SourcesEvent {
  type: "sources";
  items: Citation[];
}

export interface DoneEvent {
  type: "done";
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export type StreamEvent = TokenEvent | SourcesEvent | DoneEvent | ErrorEvent;

// ── streamQuery ─────────────────────────────────────────────────────────────────

export interface StreamQueryOptions {
  question: string;
  ticker: string | null;
  onToken: (text: string) => void;
  onSources: (items: Citation[]) => void;
  onDone: () => void;
  onError: (message: string) => void;
  /** Optional abort signal to cancel an in-flight stream. */
  signal?: AbortSignal;
}

/**
 * Open a streaming query and dispatch each parsed SSE event to the matching
 * callback. Resolves when the stream completes (after onDone/onError fire).
 * Never throws — transport and parse failures are surfaced via onError.
 */
export async function streamQuery(opts: StreamQueryOptions): Promise<void> {
  const { question, ticker, onToken, onSources, onDone, onError, signal } = opts;

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/query/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, ticker }),
      signal,
    });
  } catch (err) {
    onError(err instanceof Error ? err.message : "Network error");
    return;
  }

  if (!res.ok || !res.body) {
    onError(`Request failed (${res.status} ${res.statusText})`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let doneEmitted = false;

  const dispatch = (payload: string) => {
    let evt: StreamEvent;
    try {
      evt = JSON.parse(payload) as StreamEvent;
    } catch {
      return; // ignore malformed lines
    }
    switch (evt.type) {
      case "token":
        onToken(evt.text);
        break;
      case "sources":
        onSources(evt.items);
        break;
      case "done":
        doneEmitted = true;
        onDone();
        break;
      case "error":
        doneEmitted = true;
        onError(evt.message);
        break;
    }
  };

  try {
    // SSE events are separated by a blank line; each event has one or more
    // `data:` lines. We buffer partial reads and flush complete events.
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = rawEvent
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.replace(/^data:\s?/, ""))
          .join("\n");
        if (data) dispatch(data);
      }
    }
    // Stream closed without an explicit done/error event: finalize gracefully.
    if (!doneEmitted) onDone();
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    onError(err instanceof Error ? err.message : "Stream error");
  }
}
