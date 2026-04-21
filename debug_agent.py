"""debug_agent.py — inspect every message produced by the financial agent."""

from agent.financial_agent import build_agent_executor

question = "Compare Apple and Microsoft revenue for their most recent fiscal year"

agent = build_agent_executor()
result = agent.invoke(
    {"messages": [("human", question)]},
    config={"recursion_limit": 20},
)

print(f"\nQuestion: {question}")
print("=" * 70)

for i, msg in enumerate(result["messages"]):
    print(f"\n[{i}] {type(msg).__name__}")

    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            print(f"  tool_call: {tc['name']}  args={tc['args']}")

    content = msg.content or ""
    snippet = content[:500]
    if snippet:
        print(f"  content: {snippet}")
        if len(content) > 500:
            print(f"  ... ({len(content)} chars total)")
