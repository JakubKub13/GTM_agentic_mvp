import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Vite app config. Vitest config lives in vitest.config.ts (it merges this file).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    // Dev-only proxy to the FastAPI backend (plan #16: backend serves the SPA in prod).
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/auth": { target: "http://localhost:8000", changeOrigin: true },
      "/runs": { target: "http://localhost:8000", changeOrigin: true },
      "/me": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
