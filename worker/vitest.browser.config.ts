import { defineConfig } from "vitest/config";
export default defineConfig({
  test: {
    include: ["test/**/*.browser.test.ts"],
    allowOnly: false,
    passWithNoTests: false,
  },
});
