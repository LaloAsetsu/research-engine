import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const here = path.dirname(fileURLToPath(import.meta.url));

// The API and the web app share one origin in production. In development and in the end-to-end
// tests the dev and preview servers forward /api to the API, so the browser never makes a
// cross-origin request and the API needs no CORS middleware.
const api = process.env.RESEARCH_API_URL ?? "http://127.0.0.1:8090";
const proxy = { "/api": { target: api, changeOrigin: false } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(here, "src") } },
  server: { proxy },
  preview: { proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/test/**",
        "src/shared/api/schema.d.ts",
        "src/shared/ui/**",
        "src/main.tsx",
      ],
      thresholds: { lines: 80 },
    },
  },
});
