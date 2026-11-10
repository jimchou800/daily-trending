import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";
export default defineWorkersConfig({
  test: {
    include: ["test/authority.test.ts"],
    poolOptions: {
      workers: {
        isolatedStorage: false,
        singleWorker: true,
        wrangler: { configPath: "./wrangler.json" },
        miniflare: {
          bindings: {
            PUBLICATION_AUTOMATION_TOKEN: "local-test-secret",
            PUBLICATION_BOOTSTRAP_TOKEN: "local-bootstrap",
            PUBLICATION_INSTALLATION_ID: "test-installation",
          },
        },
      },
    },
    coverage: {
      provider: "istanbul",
      include: ["src/**/*.ts"],
      exclude: ["src/**/*.d.ts"],
      reporter: ["text", "json", "html"],
      thresholds: { lines: 80, branches: 80, functions: 80, statements: 80 },
    },
    allowOnly: false,
    passWithNoTests: false,
  },
});
