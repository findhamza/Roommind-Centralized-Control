import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, "src/main.ts"),
      name: "RoomMindPanel",
      formats: ["iife"],
      fileName: () => "roommind-cc-panel.js",
    },
    outDir: "../custom_components/roommind_cc/frontend",
    emptyOutDir: false,
    rollupOptions: {
      // No external dependencies – everything is bundled
    },
  },
});
