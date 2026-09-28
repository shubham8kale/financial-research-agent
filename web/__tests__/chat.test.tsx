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
        { ticker: "AAPL", chunk_idx: "42", source: "AAPL_10K_chunk_42", cited: true },
        { ticker: "AAPL", chunk_idx: "43", source: "AAPL_10K_chunk_43", cited: false },
      ]),
    );
    // The contract's verdict arrives after the sources; it renders once the stream ends.
    act(() =>
      opts.onVerification?.({
        status: "verified",
        n_claims: 2,
        n_figures: 3,
        n_supported: 3,
        failures: [],
        cited: ["AAPL_10K_chunk_42"],
        repaired: true,
      }),
    );
    expect(screen.queryByLabelText("Verification")).not.toBeInTheDocument();
    // The meter arrives after the sources; it renders once the stream ends.
    act(() =>
      opts.onMeta?.({
        latency_ms: 2345,
        llm_calls: 3,
        input_tokens: 4000,
        output_tokens: 120,
        cost_usd: 0.00118,
        tools: { lookup_financial_fact: 2, compute_metric: 1 },
        tool_ms_total: 900,
        trace_id: "0123456789abcdef",
        backend: "direct",
      }),
    );
    expect(screen.queryByLabelText("Answer cost")).not.toBeInTheDocument();
    act(() => opts.onDone());

    expect(screen.getByText("Sources")).toBeInTheDocument();
    expect(screen.getByText(/✓ AAPL · chunk 42/)).toBeInTheDocument();
    expect(screen.getByText(/^AAPL · chunk 43/)).toBeInTheDocument();
    expect(screen.getByLabelText("Verification")).toHaveTextContent(
      "✓ Verified · 2 claims · 3/3 figures found in cited sources · after one repair",
    );
    const cost = screen.getByLabelText("Answer cost");
    expect(cost).toHaveTextContent("2.3 s · 4,120 tokens · $0.0012");
    expect(cost).toHaveTextContent("lookup_financial_fact ×2, compute_metric");
    expect(cost).toHaveTextContent("trace 01234567");
  });

  it("shows a withheld answer as refused", () => {
    render(<Home />);
    fireEvent.change(screen.getByLabelText("Ask a question"), {
      target: { value: "What was the figure?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const opts = lastStreamOptions();
    act(() => opts.onToken("I could not verify my draft answer against the filings."));
    act(() =>
      opts.onVerification?.({
        status: "refused",
        n_claims: 1,
        n_figures: 1,
        n_supported: 0,
        failures: [{ check: "unsupported_figure", claim: "x", detail: "$1,234 million" }],
        cited: [],
      }),
    );
    act(() => opts.onDone());
    expect(screen.getByLabelText("Verification")).toHaveTextContent("Answer withheld");
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
