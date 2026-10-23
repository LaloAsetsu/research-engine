// Records real answers of the ask and paper endpoints as test fixtures.
// Usage: node scripts/capture-ask-fixtures.mjs [http://127.0.0.1:8091]
// Start the API first: uv run research-api serve --fake --port 8091 --ask-pause 0
// The unit tests read src/features/ask/fixtures/captured.json. They replay the recorded events
// through a stand-in for EventSource; the end-to-end tests use the real thing against the API.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const api = process.argv[2] ?? "http://127.0.0.1:8091";
const out = fileURLToPath(new URL("../src/features/ask/fixtures/captured.json", import.meta.url));

// A seeded test account of the API in fake mode (packages/research-api/tests/fixtures/seed-users.json).
const ACCOUNT = { username: "dani", password: "test-password-dani-03" };

const login = await fetch(`${api}/api/v1/auth/login`, {
  method: "POST",
  headers: { "content-type": "application/json", origin: api },
  body: JSON.stringify(ACCOUNT),
});
if (login.status !== 204) throw new Error(`sign-in answered ${login.status}`);
const cookies = login.headers.getSetCookie().map((line) => line.split(";")[0]);
const csrf = decodeURIComponent(
  cookies.find((c) => c.startsWith("re_csrf="))?.slice("re_csrf=".length) ?? "",
);
const headers = { cookie: cookies.join("; "), origin: api, "x-csrf-token": csrf };

async function json(method, path, body) {
  const response = await fetch(`${api}/api/v1${path}`, {
    method,
    headers: { ...headers, ...(body ? { "content-type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}

function parseStream(text) {
  const events = [];
  for (const block of text.split("\n\n")) {
    const fields = { event: "message", data: [], id: null };
    let any = false;
    for (const line of block.split("\n")) {
      if (line === "" || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const name = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (name === "event") fields.event = value;
      else if (name === "data") fields.data.push(value);
      else if (name === "id") fields.id = value;
      any = true;
    }
    if (any) events.push({ event: fields.event, data: fields.data.join("\n"), id: fields.id });
  }
  return events;
}

const QUESTIONS = [
  { name: "transmon", request: { question: "What limits T1 in transmons?" } },
  { name: "surface code", request: { question: "surface code threshold" } },
  { name: "teleportation", request: { question: "teleportation circuit" } },
  { name: "spanish", request: { question: "¿Qué limita la coherencia de los transmones?" } },
  { name: "no evidence", request: { question: "How do octopuses taste?" } },
  {
    name: "without index",
    request: { question: "Grover search noisy oracle", use_index: false },
  },
  {
    name: "options",
    request: {
      question: "VQE barren plateau",
      bm25: "always",
      whole_blocks: true,
    },
  },
  { name: "unavailable", request: { question: "simulate-unavailable please" } },
  { name: "upstream", request: { question: "simulate-upstream please" } },
];

const asks = [];
const docIds = new Set();
for (const { name, request } of QUESTIONS) {
  const body = {
    use_index: true,
    bm25: null,
    whole_blocks: null,
    return_contexts: true,
    ...request,
  };
  const accepted = await json("POST", "/asks", body);
  if (accepted.status !== 202) throw new Error(`${name}: ${accepted.status}`);
  const stream = await fetch(`${api}/api/v1/asks/${accepted.body.ask_id}/events`, { headers });
  const events = parseStream(await stream.text());
  const view = await json("GET", `/asks/${accepted.body.ask_id}`);
  for (const event of events) {
    if (event.event === "answer") {
      for (const id of JSON.parse(event.data).sources) docIds.add(id);
    }
  }
  asks.push({ name, request: body, accepted: accepted.body, events, view: view.body });
}

const documents = {};
const pages = {};
const list = await json("GET", "/documents?limit=100");
for (const item of list.body.items) docIds.add(item.doc_id);
for (const docId of docIds) {
  const detail = await json("GET", `/documents/${docId}`);
  documents[docId] = detail.body;
  for (const page of detail.body.pages) {
    pages[`${docId}:${page.page}`] = (
      await json("GET", `/documents/${docId}/pages/${page.page}`)
    ).body;
  }
}
const missing = await json("GET", `/documents/${[...docIds][0]}/pages/99`);
const status = await json("GET", "/status");

writeFileSync(
  out,
  `${JSON.stringify({ list: list.body, documents, pages, missingPage: missing, status: status.body, asks }, null, 2)}\n`,
);
console.log(`wrote ${asks.length} asks, ${Object.keys(documents).length} papers to ${out}`);
