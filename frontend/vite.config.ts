import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const basePath = `${(process.env.VITE_BASE_PATH ?? "/").replace(/\/+$/, "")}/`;
const apiPrefix = `${basePath}api`;

export default defineConfig({
  base: basePath,
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      [apiPrefix]: {
        target: process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (requestPath) => requestPath.slice(apiPrefix.length),
      },
    },
  },
});
