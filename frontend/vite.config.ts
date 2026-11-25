import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В Docker backend доступен по имени сервиса, локально — по localhost.
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
