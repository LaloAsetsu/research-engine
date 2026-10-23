import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const features = ["auth", "status", "ask", "benchmarks", "qsets", "circuits"];

// A feature owns its screen, hooks and stores. It may use `shared`, never another feature or
// the app shell, and it keeps to its own folder (no `../`).
const featureBoundary = (own) => ({
  files: [`src/features/${own}/**/*.{ts,tsx}`],
  rules: {
    "no-restricted-imports": [
      "error",
      {
        patterns: [
          {
            group: features
              .filter((f) => f !== own)
              .map((f) => `@/features/${f}`)
              .flatMap((p) => [p, `${p}/*`]),
            message:
              "A feature does not import another feature. Move what both need to src/shared.",
          },
          { group: ["@/app", "@/app/*"], message: "A feature does not import the app shell." },
          { group: ["../*"], message: "Stay inside the feature folder, or import from @/shared." },
        ],
      },
    ],
  },
});

export default tseslint.config(
  {
    ignores: [
      "dist",
      "coverage",
      "playwright-report",
      "test-results",
      "src/shared/api/schema.d.ts",
      "public",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  jsxA11y.flatConfigs.recommended,
  reactHooks.configs.flat["recommended-latest"],
  {
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "@typescript-eslint/no-misused-promises": [
        "error",
        { checksVoidReturn: { attributes: false } },
      ],
      // Components do not call fetch: only the hooks in features/*/api.ts, through the client.
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "Use the client in src/shared/api." },
      ],
    },
  },
  ...features.map(featureBoundary),
  {
    files: ["src/shared/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            { group: ["@/features", "@/features/*"], message: "shared does not import features." },
            { group: ["@/app", "@/app/*"], message: "shared does not import the app shell." },
          ],
        },
      ],
    },
  },
  {
    files: ["src/shared/ui/**/*.tsx"],
    rules: {
      // shadcn/ui components are generated code: keep them as the CLI writes them.
      "@typescript-eslint/consistent-type-imports": "off",
      "@typescript-eslint/no-unnecessary-condition": "off",
      "jsx-a11y/anchor-has-content": "off",
    },
  },
  { files: ["**/*.{js,mjs}"], ...tseslint.configs.disableTypeChecked },
  { files: ["**/*.{js,mjs}"], languageOptions: { globals: { ...globals.node } } },
  {
    files: ["vite.config.ts", "playwright.config.ts", "e2e/**/*.ts", "scripts/**"],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    // Scripts run in Node and talk to the API directly; the fetch rule is for the screens.
    files: ["scripts/**"],
    rules: { "no-restricted-globals": "off" },
  },
  {
    files: ["src/**/*.test.{ts,tsx}", "src/test/**/*.{ts,tsx}", "e2e/**/*.ts"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "no-restricted-globals": "off",
    },
  },
);
