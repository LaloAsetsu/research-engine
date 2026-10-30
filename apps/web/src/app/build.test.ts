import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

/** Builds the app in memory with the real config; nothing is written. */
async function bundle() {
  const result = await build({
    root,
    configFile: path.join(root, "vite.config.ts"),
    logLevel: "silent",
    build: { write: false },
  });
  const outputs = Array.isArray(result) ? result : "output" in result ? [result] : [];
  return outputs.flatMap((entry) => entry.output);
}

describe("the production build", () => {
  it("serves every font as a file the CSP allows, never as a data: URI", async () => {
    const output = await bundle();
    const styles = output
      .filter((file) => file.type === "asset" && file.fileName.endsWith(".css"))
      .map((file) => String((file as { source: string | Uint8Array }).source));
    expect(styles.length).toBeGreaterThan(0);
    for (const style of styles) {
      expect(style).not.toMatch(/url\(\s*["']?data:/);
    }
    const fonts = output.map((file) => file.fileName).filter((name) => name.endsWith(".woff2"));
    // KaTeX has twenty fonts, the smallest of them under the 4 KiB inline limit of Vite
    expect(fonts.length).toBeGreaterThanOrEqual(20);
    expect(fonts.some((name) => name.includes("KaTeX_Size3-Regular"))).toBe(true);
  }, 60_000);

  it("ships KaTeX as woff2 only", async () => {
    const names = (await bundle()).map((file) => file.fileName);
    expect(names.filter((name) => /\.(?:woff|ttf)$/.test(name))).toEqual([]);
  }, 60_000);
});
