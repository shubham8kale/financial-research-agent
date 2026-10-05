import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

import Home from "@/app/page";
import { streamQuery, type StreamQueryOptions } from "@/lib/api";
import { THREAD_ID_PATTERN } from "@/lib/thread";

// The SSE client is mocked so the test drives the stream by hand; what is under
// test here is which thread id the page hands it, and the "New chat" control.
vi.mock("@/lib/api", () => ({
  streamQuery: vi.fn(() => Promise.resolve()),
}));

const mockedStreamQuery = vi.mocked(streamQuery);

function lastStreamOptions(): StreamQueryOptions {
  const calls = mockedStreamQuery.mock.calls;
  return calls[calls.length - 1][0];
}

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Ask a question"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

function finishAnswer(text: string) {
  const opts = lastStreamOptions();
  act(() => opts.onToken(text));
  act(() => opts.onDone());
}

describe("conversation thread", () => {
  beforeEach(() => {
    mockedStreamQuery.mockClear();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("sends a valid thread id with every question, the same one within a conversation", () => {
    render(<Home />);
    ask("What was Apple's revenue?");
    const first = lastStreamOptions().threadId;
    expect(first).toMatch(THREAD_ID_PATTERN);
    finishAnswer("$416,161 million.");

    ask("And Microsoft?");
    expect(mockedStreamQuery).toHaveBeenCalledTimes(2);
    expect(lastStreamOptions().threadId).toBe(first);
  });

  it("mints a different thread id for each page load", () => {
    const { unmount } = render(<Home />);
    ask("First page load.");
    const a = lastStreamOptions().threadId;
    unmount();
    render(<Home />);
    ask("Second page load.");
    expect(lastStreamOptions().threadId).toMatch(THREAD_ID_PATTERN);
    expect(lastStreamOptions().threadId).not.toBe(a);
  });

  it("'New chat' clears the messages and starts a new thread", () => {
    render(<Home />);
    ask("What was Apple's revenue?");
    const before = lastStreamOptions().threadId;
    finishAnswer("A distinctive earlier answer.");
    expect(screen.getByText("A distinctive earlier answer.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "New chat" }));

    expect(screen.queryByText("A distinctive earlier answer.")).not.toBeInTheDocument();
    expect(screen.getByText("Ask about SEC 10-K filings")).toBeInTheDocument();
    ask("A fresh question.");
    expect(lastStreamOptions().threadId).toMatch(THREAD_ID_PATTERN);
    expect(lastStreamOptions().threadId).not.toBe(before);
  });

  it("disables 'New chat' while an answer is streaming", () => {
    render(<Home />);
    ask("What was Apple's revenue?");
    expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled();
    finishAnswer("Done.");
    expect(screen.getByRole("button", { name: "New chat" })).toBeEnabled();
  });

  it("tells the user follow-ups are remembered, and that memory is not saved", () => {
    render(<Home />);
    expect(
      screen.getByText(
        "Follow-up questions are remembered for this session. Memory is not saved and clears when the server restarts.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the thread id in React state only: nothing is written to browser storage", () => {
    render(<Home />);
    ask("What was Apple's revenue?");
    finishAnswer("$416,161 million.");
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).not.toContain(String(lastStreamOptions().threadId));
  });
});
