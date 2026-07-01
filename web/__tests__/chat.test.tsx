import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

import Home from "@/app/page";
import { streamQuery, type StreamQueryOptions } from "@/lib/api";

// Mock the SSE client so the test drives the stream callbacks manually and can
// assert the UI updates incrementally — no real network or backend involved.
vi.mock("@/lib/api", () => ({
  streamQuery: vi.fn(() => Promise.resolve()),
}));

const mockedStreamQuery = vi.mocked(streamQuery);

function lastStreamOptions(): StreamQueryOptions {
  const calls = mockedStreamQuery.mock.calls;
  return calls[calls.length - 1][0];
}

describe("chat streaming UI", () => {
  beforeEach(() => {
    mockedStreamQuery.mockClear();
  });

  it("renders streamed tokens incrementally and shows a citation", () => {
    render(<Home />);

    // Ask a question.
    fireEvent.change(screen.getByLabelText("Ask a question"), {
      target: { value: "Summarize the latest 10-K." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // streamQuery is called once; grab its callbacks.
    expect(mockedStreamQuery).toHaveBeenCalledTimes(1);
    const opts = lastStreamOptions();

    // Before any token arrives, the assistant shows a loading indicator.
    expect(screen.getByLabelText("Assistant is thinking")).toBeInTheDocument();

    // First delta renders; the later text is not present yet (incremental).
    act(() => opts.onToken("Net sales "));
    expect(screen.getByText(/Net sales/)).toBeInTheDocument();
    expect(screen.queryByText(/\$416,161 million/)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Assistant is thinking"),
    ).not.toBeInTheDocument();

    // Second delta appends to the same message.
    act(() => opts.onToken("were $416,161 million."));
    expect(screen.getByText(/Net sales were \$416,161 million\./)).toBeInTheDocument();

    // Citations arrive and render as a Sources list.
    act(() =>
      opts.onSources([
        { ticker: "AAPL", chunk_idx: "42", source: "AAPL_10K_chunk_42" },
      ]),
    );
    act(() => opts.onDone());

    expect(screen.getByText("Sources")).toBeInTheDocument();
    expect(screen.getByText(/AAPL · chunk 42/)).toBeInTheDocument();
  });

  it("shows error copy when the stream fails", () => {
    render(<Home />);

    fireEvent.change(screen.getByLabelText("Ask a question"), {
      target: { value: "Trigger a failure." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    act(() => lastStreamOptions().onError("Failed to fetch"));

    expect(
      screen.getByText(/Something went wrong: Failed to fetch/),
    ).toBeInTheDocument();
  });
});
