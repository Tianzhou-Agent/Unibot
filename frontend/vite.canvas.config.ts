import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import autoprefixer from "autoprefixer";
import tailwindcss from "tailwindcss";
import path from "node:path";
// @ts-expect-error -- the Tailwind config is plain JavaScript without types.
import tailwindConfig from "./tailwind.config.js";

// Optional example canvases are built only on request, outside the host bundle.
const root = path.resolve(__dirname, "../examples/image-recognition-aina/canvas");

export default defineConfig({
  root,
  // Package files are served from a versioned path, so every reference stays relative.
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {
      "@aina-ui": path.resolve(__dirname, "../spec/aina/2.0/ui/aina-ui.js"),
      // The example reuses the frontend's development dependencies, never its application code.
      ...Object.fromEntries(["react", "react-dom", "react-i18next", "i18next", "lucide-react"].map(
        (name) => [name, path.resolve(__dirname, "node_modules", name)],
      )),
    },
  },
  css: {
    postcss: {
      plugins: [tailwindcss({ ...tailwindConfig, content: [`${root}/**/*.{html,ts,tsx}`] }), autoprefixer()],
    },
  },
  build: {
    outDir: path.resolve(__dirname, "../examples/image-recognition-aina/package/canvas"),
    emptyOutDir: true,
    // The canvas is a single page in a sandboxed frame; one script and one stylesheet keep it simple to serve.
    modulePreload: false,
    chunkSizeWarningLimit: 1024,
  },
});
