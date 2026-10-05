import { describe, it, expect, vi, afterEach } from "vitest";

import { streamQuery } from "@/lib/api";
import { THREAD_ID_PATTERN, newThreadId, sanitizeThreadId } from "@/lib/thread";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("newThreadId", () => {
  it("makes ids the server accepts, and different ones", () => {
    const ids = Array.from({ length: 50 }, () => newThreadId());
    for (const id of ids) expect(id).toMatch(THREAD_ID_PATTERN);
    expect(new Set(ids).size).toBe(50);
  });

  it("falls back to getRandomValues where randomUUID is unavailable (an insecure context)", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (a: Uint8Array) => a.fill(171),
    });
    expect(newThreadId()).toBe("ab".repeat(16));
    expect(newThreadId()).toMatch(THREAD_ID_PATTERN);
  });

  it("falls back to Math.random where there is no crypto at all", () => {
    vi.stubGlobal("crypto", undefined);
    const id = newThreadId();
    expect(id).toMatch(THREAD_ID_PATTERN);
    expect(newThreadId()).not.toBe(id);
  });
});

describe("sanitizeThreadId", () => {
  it("replaces characters the server would reject and holds the length to 8 to 64", () => {
    expect(sanitizeThreadId("550e8400-e29b-41d4-a716-446655440000")).toBe(
      "550e8400-e29b-41d4-a716-446655440000",
    );
    expect(sanitizeThreadId("has space; and/slash")).toBe("has_space__and_slash");
    expect(sanitizeThreadId("ab")).toBe("abxxxxxx");
    expect(sanitizeThreadId("é".repeat(100))).toHaveLength(64);
    for (const raw of ["", "a", "///", "x".repeat(200), "ok-thread_1"]) {
      expect(sanitizeThreadId(raw)).toMatch(THREAD_ID_PATTERN);
    }
  });
});

function sseResponse(events: object[]): Response {
  const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  const bytes = new TextEncoder().encode(body);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

describe("streamQuery request body", () => {
  const callbacks = () => ({
    onToken: vi.fn(),
    onSources: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  });

  it("carries thread_id when given one", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(sseResponse([{ type: "done" }])));
    vi.stubGlobal("fetch", fetchMock);
    const cb = callbacks();
    await streamQuery({ question: "And Microsoft?", ticker: null, threadId: "thread-aaaaaaaa", ...cb });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/query\/stream$/);
    expect(JSON.parse(init.body as string)).toEqual({
      question: "And Microsoft?",
      ticker: null,
      thread_id: "thread-aaaaaaaa",
    });
    expect(cb.onDone).toHaveBeenCalledTimes(1);
  });

  it("sends exactly {question, ticker} when there is no thread id", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(sseResponse([{ type: "done" }])));
    vi.stubGlobal("fetch", fetchMock);
    await streamQuery({ question: "q", ticker: "AAPL", ...callbacks() });
    await streamQuery({ question: "q", ticker: "AAPL", threadId: null, ...callbacks() });
    for (const call of fetchMock.mock.calls as unknown as [string, RequestInit][]) {
      expect(JSON.parse(call[1].body as string)).toEqual({ question: "q", ticker: "AAPL" });
    }
  });

  it("hands the thread fields of the meta event to onMeta without touching the other events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          sseResponse([
            { type: "token", text: "Hi " },
            { type: "sources", items: [] },
            {
              type: "meta",
              latency_ms: 1,
              llm_calls: 2,
              input_tokens: 3,
              output_tokens: 4,
              cost_usd: 0.001,
              tools: {},
              tool_ms_total: 0,
              trace_id: null,
              thread_id: "thread-aaaaaaaa",
              thread_turns: 2,
            },
            { type: "done" },
          ]),
        ),
      ),
    );
    const cb = { ...callbacks(), onMeta: vi.fn() };
    await streamQuery({ question: "q", ticker: null, threadId: "thread-aaaaaaaa", ...cb });
    expect(cb.onToken).toHaveBeenCalledWith("Hi ");
    expect(cb.onMeta).toHaveBeenCalledWith(
      expect.objectContaining({ thread_id: "thread-aaaaaaaa", thread_turns: 2 }),
    );
    expect(cb.onDone).toHaveBeenCalledTimes(1);
    expect(cb.onError).not.toHaveBeenCalled();
  });
});
