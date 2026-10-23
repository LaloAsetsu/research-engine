// Bundle budget (scope section 5.10): the entry chunk is at most 250 KiB gzipped, any other
// JavaScript chunk at most 400 KiB, and KaTeX stays out of the entry. Run after `pnpm build`.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const ENTRY_LIMIT = 250 * 1024;
const CHUNK_LIMIT = 400 * 1024;
const dist = new URL("../dist/", import.meta.url).pathname;

const html = readFileSync(join(dist, "index.html"), "utf8");
const entryMatch = /<script[^>]+type="module"[^>]+src="\/(assets\/[^"]+\.js)"/.exec(html);
if (!entryMatch) {
  console.error("size: no entry script in dist/index.html (run `pnpm build` first)");
  process.exit(1);
}
const entry = entryMatch[1].replace("assets/", "");

const kib = (bytes) => `${(bytes / 1024).toFixed(1)} KiB`;
let failed = false;

for (const name of readdirSync(join(dist, "assets"))
  .filter((file) => file.endsWith(".js"))
  .sort()) {
  const source = readFileSync(join(dist, "assets", name));
  const gzipped = gzipSync(source).length;
  const isEntry = name === entry;
  const limit = isEntry ? ENTRY_LIMIT : CHUNK_LIMIT;
  const over = gzipped > limit;
  if (isEntry && source.toString("utf8").toLowerCase().includes("katex")) {
    console.error(`size: KaTeX is in the entry chunk ${name}`);
    failed = true;
  }
  if (over) failed = true;
  console.log(
    `${over ? "FAIL" : "ok  "} ${isEntry ? "entry" : "chunk"} ${name}  ${kib(gzipped)} gzip (limit ${kib(limit)})`,
  );
}

process.exit(failed ? 1 : 0);
