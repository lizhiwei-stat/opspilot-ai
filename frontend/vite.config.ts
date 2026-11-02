import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In dev, Vite proxies API calls to the backend so the browser stays same-origin.
const backend = process.env.OPSPILOT_BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: { "/api": backend, "/health": backend },
  },
  test: { environment: "jsdom" },
});
