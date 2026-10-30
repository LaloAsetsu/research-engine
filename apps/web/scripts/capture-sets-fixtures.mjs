// Records real answers of the question-set endpoints as test fixtures.
// Usage: node scripts/capture-sets-fixtures.mjs [http://127.0.0.1:8091]
// Start the API first: uv run research-api serve --fake --port 8091
// It signs in with the seeded test accounts of the fake API and only reads or sends requests
// that the server refuses, so the demo sets are left as they were. The unit tests read
// src/features/qsets/fixtures/captured.json; the end-to-end test asks the API again and fails
// when an answer differs, so the file never goes stale.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const api = process.argv[2] ?? "http://127.0.0.1:8091";
const out = fileURLToPath(new URL("../src/features/qsets/fixtures/captured.json", import.meta.url));
const BASE = `${api}/api/v1`;

const ACCOUNTS = {
  ana: "test-password-ana-01",
  dani: "test-password-dani-03",
};

async function signIn(username) {
  const response = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json", origin: api },
    body: JSON.stringify({ username, password: ACCOUNTS[username] }),
  });
  if (response.status !== 204) throw new Error(`sign-in of ${username}: ${response.status}`);
  const cookies = response.headers.getSetCookie().map((line) => line.split(";")[0]);
  const csrf = cookies.find((c) => c.startsWith("re_csrf="))?.slice("re_csrf=".length);
  return { cookie: cookies.join("; "), csrf };
}

function caller(session) {
  return async (method, path, { body, headers = {}, raw } = {}) => {
    const response = await fetch(`${BASE}${path}`, {
      method,
      headers: {
        cookie: session.cookie,
        origin: api,
        ...(method === "GET" ? {} : { "x-csrf-token": session.csrf }),
        ...(body !== undefined && raw === undefined ? { "content-type": "application/json" } : {}),
        ...headers,
      },
      ...(raw !== undefined
        ? { body: raw }
        : body !== undefined
          ? { body: JSON.stringify(body) }
          : {}),
    });
    const text = await response.text();
    return {
      status: response.status,
      etag: response.headers.get("etag"),
      response: text ? JSON.parse(text) : null,
    };
  };
}

const cases = [];
function add(name, method, path, request, answer) {
  cases.push({
    name,
    method,
    path,
    request: request ?? null,
    status: answer.status,
    response: answer.response,
  });
}

const ana = caller(await signIn("ana"));
const dani = caller(await signIn("dani"));

const catalog = await ana("GET", "/sets");
add("catalog", "GET", "/sets", null, catalog);
add("shapes", "GET", "/sets/import/shapes", null, await ana("GET", "/sets/import/shapes"));

const versions = catalog.response.flatMap((entry) => entry.versions);
for (const v of versions.filter((x) => x.name.startsWith("demo-"))) {
  const base = `/sets/${v.name}/versions/${v.version}`;
  const page = await ana("GET", `${base}?limit=200`);
  add(`page ${v.name}/${v.version}`, "GET", base, null, page);
  for (const item of page.response.items) {
    add(
      `item ${v.name}/${v.version}/${item.id}`,
      "GET",
      `${base}/items/${item.id}`,
      null,
      await ana("GET", `${base}/items/${item.id}`),
    );
  }
  add(
    `freeze ${v.name}/${v.version}`,
    "GET",
    `${base}/freeze`,
    null,
    await ana("GET", `${base}/freeze`),
  );
}

// Refusals, all of which leave the demo sets as they are.
const P = "/sets/demo-papers/versions/v2";
const detail = (await ana("GET", `${P}/items/DP-003`)).response;
const etag = detail.version.etag;
const stored = detail.item;
add(
  "put 422",
  "PUT",
  `${P}/items/DP-003`,
  {
    id: "DP-003",
    lang: "fr",
    form: "single",
    corpus: "c",
    gold_papers: [{ doc_id: "zz" }],
    colour: "x",
  },
  await ana("PUT", `${P}/items/DP-003`, {
    body: {
      id: "DP-003",
      lang: "fr",
      form: "single",
      corpus: "c",
      gold_papers: [{ doc_id: "zz" }],
      colour: "x",
    },
    headers: { "if-match": etag },
  }),
);
add(
  "put 409 stale",
  "PUT",
  `${P}/items/DP-003`,
  stored,
  await ana("PUT", `${P}/items/DP-003`, { body: stored, headers: { "if-match": '"stale"' } }),
);
add(
  "put 428",
  "PUT",
  `${P}/items/DP-003`,
  stored,
  await ana("PUT", `${P}/items/DP-003`, { body: stored }),
);
const decided = (await ana("GET", `${P}/items/DP-001`)).response;
const changed = { ...decided.item, question: "A changed question." };
add(
  "put 409 decisions",
  "PUT",
  `${P}/items/DP-001`,
  changed,
  await ana("PUT", `${P}/items/DP-001`, {
    body: changed,
    headers: { "if-match": decided.version.etag },
  }),
);
add(
  "put 403 member",
  "PUT",
  `${P}/items/DP-003`,
  stored,
  await dani("PUT", `${P}/items/DP-003`, { body: stored, headers: { "if-match": etag } }),
);
add(
  "review 409 stale",
  "POST",
  `${P}/reviews`,
  { item_id: "DP-003", verdict: "accept" },
  await ana("POST", `${P}/reviews`, {
    body: { item_id: "DP-003", verdict: "accept" },
    headers: { "if-match": '"stale"' },
  }),
);
add(
  "review 422 note",
  "POST",
  `${P}/reviews`,
  { item_id: "DP-003", verdict: "reject" },
  await ana("POST", `${P}/reviews`, {
    body: { item_id: "DP-003", verdict: "reject" },
    headers: { "if-match": etag },
  }),
);
add(
  "freeze 409 stale",
  "POST",
  `${P}/freeze`,
  null,
  await ana("POST", `${P}/freeze`, { headers: { "if-match": '"stale"' } }),
);
add("freeze plan as a member", "GET", `${P}/freeze`, null, await dani("GET", `${P}/freeze`));
add(
  "validate bad",
  "POST",
  "/sets/validate",
  { item: { id: "X", lang: "fr" } },
  await ana("POST", "/sets/validate", { body: { item: { id: "X", lang: "fr" } } }),
);
add(
  "validate ok",
  "POST",
  "/sets/validate",
  { item: stored },
  await ana("POST", "/sets/validate", { body: { item: stored } }),
);
const bad = '{"format":"qset/1"}\nnot json\n';
add(
  "import 422",
  "POST",
  "/sets/import?name=refused",
  bad,
  await ana("POST", "/sets/import?name=refused", {
    raw: bad,
    headers: { "content-type": "application/octet-stream" },
  }),
);

writeFileSync(out, `${JSON.stringify(cases, null, 2)}\n`);
console.log(`${cases.length} answers written to ${out}`);
