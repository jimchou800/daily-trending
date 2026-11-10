import js from "@eslint/js";
import ts from "typescript-eslint";
export default ts.config(
  { ignores: ["node_modules/**", "coverage/**", ".wrangler/**"] },
  js.configs.recommended,
  ...ts.configs.recommended,
  {
    rules: {
      "no-warning-comments": [
        "error",
        { terms: ["TODO", "FIXME"], location: "anywhere" },
      ],
    },
  },
  {
    files: ["test/**/*.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "MemberExpression[property.name=/^(skip|skipIf|todo|only|fails|runIf)$/]",
          message:
            "Required scenarios must run; skipped, exclusive and expected-failure tests are forbidden.",
        },
      ],
    },
  },
);
