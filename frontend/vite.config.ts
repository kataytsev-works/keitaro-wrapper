import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The backend (uvicorn on :8000) serves /api; the browser talks only to the dev server.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
