import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server stays on loopback; /api is proxied to the local FastAPI backend.
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", assetsDir: "assets", emptyOutDir: true },
});
