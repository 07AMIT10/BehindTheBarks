import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": import.meta.dirname } },
  test: {
    include: ["lib/**/*.test.ts", "lib/**/*.test.tsx", "components/**/*.test.tsx"],
    // Default node environment for the pure lib tests; DOM-dependent tests opt in per file
    // with a `// @vitest-environment happy-dom` docblock.
    environment: "node",
  },
});
