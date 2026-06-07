import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { AgentLogViewer } from "./AgentLogViewer";

describe("AgentLogViewer", () => {
  it("renders each persisted tool-call line", () => {
    const json = JSON.stringify([
      "scout:hiring exa_search(query=...)",
      "analyst record_assessment(score=8)",
    ]);
    render(<AgentLogViewer agentLogJson={json} />);
    expect(screen.getByText("scout:hiring exa_search(query=...)")).toBeInTheDocument();
    expect(screen.getByText("analyst record_assessment(score=8)")).toBeInTheDocument();
  });

  it("renders an empty state when the log is null", () => {
    render(<AgentLogViewer agentLogJson={null} />);
    expect(screen.getByText(/no agent log/i)).toBeInTheDocument();
  });

  it("renders an empty state when the log is an empty list", () => {
    render(<AgentLogViewer agentLogJson="[]" />);
    expect(screen.getByText(/no agent log/i)).toBeInTheDocument();
  });

  it("renders an empty state on malformed json without throwing", () => {
    render(<AgentLogViewer agentLogJson="{not json" />);
    expect(screen.getByText(/no agent log/i)).toBeInTheDocument();
  });
});
