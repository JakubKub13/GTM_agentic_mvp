import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ToolEvent } from "@/lib/api";
import { ToolCallFeed } from "./ToolCallFeed";

function makeTool(over: Partial<ToolEvent> = {}): ToolEvent {
  return {
    type: "tool",
    run_id: "r1",
    account: "acme.com",
    agent: "scout",
    beat: "hiring",
    tool: "exa_search",
    arg_summary: "query=acme hiring",
    ts: "2026-06-06T10:00:00Z",
    ...over,
  };
}

describe("ToolCallFeed", () => {
  it("renders an empty state when there are no events", () => {
    render(<ToolCallFeed events={[]} />);
    expect(screen.getByText(/no tool calls yet/i)).toBeInTheDocument();
  });

  it("renders a line per tool event with account, agent:beat and tool(args)", () => {
    render(
      <ToolCallFeed
        events={[
          makeTool({ tool: "exa_search", arg_summary: "query=acme hiring" }),
          makeTool({ account: "globex.com", agent: "analyst", beat: null, tool: "record_assessment" }),
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(screen.getByText("acme.com")).toBeInTheDocument();
    expect(screen.getByText(/scout:hiring/)).toBeInTheDocument();
    expect(screen.getByText(/exa_search\(query=acme hiring\)/)).toBeInTheDocument();
    expect(screen.getByText(/record_assessment/)).toBeInTheDocument();
  });

  it("renders agent without a beat as just the agent role", () => {
    render(<ToolCallFeed events={[makeTool({ agent: "router", beat: null })]} />);
    expect(screen.getByText("router")).toBeInTheDocument();
  });

  it("filters events by the typed substring (account / agent / tool)", async () => {
    render(
      <ToolCallFeed
        events={[
          makeTool({ account: "acme.com", tool: "exa_search" }),
          makeTool({ account: "globex.com", tool: "record_assessment" }),
        ]}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(2);

    const filter = screen.getByRole("textbox", { name: /filter/i });
    await userEvent.type(filter, "globex");

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(screen.getByText("globex.com")).toBeInTheDocument();
    expect(screen.queryByText("acme.com")).not.toBeInTheDocument();
  });

  it("filtering by tool name also matches", async () => {
    render(
      <ToolCallFeed
        events={[
          makeTool({ account: "acme.com", tool: "exa_search" }),
          makeTool({ account: "globex.com", tool: "record_assessment" }),
        ]}
      />,
    );
    const filter = screen.getByRole("textbox", { name: /filter/i });
    await userEvent.type(filter, "record_assess");
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(screen.getByText(/record_assessment/)).toBeInTheDocument();
  });

  it("shows an explicit no-match state when the filter excludes everything", async () => {
    render(<ToolCallFeed events={[makeTool({ account: "acme.com" })]} />);
    const filter = screen.getByRole("textbox", { name: /filter/i });
    await userEvent.type(filter, "zzzznotfound");
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
    expect(screen.getByText(/no tool calls match/i)).toBeInTheDocument();
  });

  it("renders the feed as a log region for assistive tech", () => {
    render(<ToolCallFeed events={[makeTool()]} />);
    expect(screen.getByRole("log")).toBeInTheDocument();
  });
});
