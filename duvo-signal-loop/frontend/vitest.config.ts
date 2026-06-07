import { mergeConfig, defineConfig } from "vitest/config";

import viteConfig from "./vite.config";

// `pnpm test` -> `vitest run` (non-watch, offline). Reuses the Vite app config.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
      css: true,
    },
  }),
);
