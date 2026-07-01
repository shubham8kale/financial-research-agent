import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
  resolve: {
    // Mirror tsconfig "@/*": ["./*"] so imports resolve in tests.
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
});
