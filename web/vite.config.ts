import { defineConfig } from "vitest/config";
import preact from "@preact/preset-vite";

// The dev server proxies the API to a local jarvis-live (JARVIS_LIVE_URL, default :8000).
const target = process.env.JARVIS_LIVE_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [preact()],
  build: { outDir: "dist", sourcemap: false },
  server: {
    proxy: {
      "/v1": { target, ws: true, changeOrigin: true },
      "/healthz": target,
    },
  },
  test: { include: ["tests/**/*.test.ts"], environment: "node" },
});
