import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import autoprefixer from "autoprefixer";
import tailwindcss from "tailwindcss";
import path from "node:path";
// @ts-expect-error -- the Tailwind config is plain JavaScript without types.
import tailwindConfig from "./tailwind.config.js";

// First-party AINA canvases (design §11): built from src/aina-canvases/<canvas> into the bundled plugin's package,
// where they are served like any AINA 2.0 canvas. `AINA_CANVAS=<canvas> vite build -c vite.canvas.config.ts`.
const PLUGINS: Record<string, string> = {
  "image-recognition": "image_recognition",
};

const canvas = process.env.AINA_CANVAS ?? "";
const plugin = PLUGINS[canvas];
if (!plugin) throw new Error(`AINA_CANVAS must be one of: ${Object.keys(PLUGINS).join(", ")}`);
const root = path.resolve(__dirname, "src/aina-canvases", canvas);

export default defineConfig({
  root,
  // Package files are served from a versioned path, so every reference stays relative.
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {
      "@aina-ui": path.resolve(__dirname, "../spec/aina/2.0/ui/aina-ui.js"),
      "@": path.resolve(__dirname, "src"),
    },
  },
  css: {
    postcss: {
      plugins: [tailwindcss({ ...tailwindConfig, content: [`${root}/**/*.{html,ts,tsx}`] }), autoprefixer()],
    },
  },
  build: {
    outDir: path.resolve(__dirname, "../backend/src/unibot_plugins", plugin, "package/canvas"),
    emptyOutDir: true,
    // The canvas is a single page in a sandboxed frame; one script and one stylesheet keep it simple to serve.
    modulePreload: false,
    chunkSizeWarningLimit: 1024,
  },
});
