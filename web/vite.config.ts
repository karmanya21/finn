import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Pull research runs in the Python API (backend/server.py).
    proxy: { "/api": "http://localhost:8787" },
  },
});
