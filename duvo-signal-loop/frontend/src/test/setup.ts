import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount React trees + reset jsdom between tests (no shared state, fully offline).
afterEach(() => {
  cleanup();
});
