import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the UI runs on :5173 and proxies API calls to the FastAPI server on :8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
