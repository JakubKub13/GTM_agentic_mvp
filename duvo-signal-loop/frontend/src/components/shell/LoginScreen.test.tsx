import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { LoginScreen, loginUrl } from "./LoginScreen";

describe("LoginScreen", () => {
  it("renders a Google sign-in link pointing at the backend OAuth start (plan #13)", () => {
    render(<LoginScreen />);
    const link = screen.getByRole("link", { name: /sign in with google/i });
    expect(link).toHaveAttribute("href", loginUrl());
    expect(loginUrl()).toContain("/auth/login");
  });

  it("surfaces an optional reason message", () => {
    render(<LoginScreen reason="Your session expired." />);
    expect(screen.getByText("Your session expired.")).toBeInTheDocument();
  });
});
