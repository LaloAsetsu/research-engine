// Records real answers of the circuit endpoints as test fixtures.
// Usage: node scripts/capture-circuit-fixtures.mjs [http://127.0.0.1:8091]
// Start the API first: uv run research-api serve --fake --port 8091
// The unit tests read src/features/circuits/fixtures/captured.json; the end-to-end test asks the
// API for every request again and fails when an answer differs, so the file never goes stale.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const api = process.argv[2] ?? "http://127.0.0.1:8091";
const out = fileURLToPath(
  new URL("../src/features/circuits/fixtures/captured.json", import.meta.url),
);

const q = (body) => `\\begin{quantikz}\n${body}\n\\end{quantikz}`;
const LATEX = {
  bell: q(
    "\\lstick{\\ket{0}} & \\gate{H} & \\ctrl{1} & \\qw \\\\\n\\lstick{\\ket{0}} & \\qw & \\targ{} & \\qw",
  ),
  ghz: `\\Qcircuit @C=1em @R=1em {\n& \\gate{H} & \\ctrl{1} & \\qw & \\qw \\\\\n& \\qw & \\targ & \\ctrl{1} & \\qw \\\\\n& \\qw & \\qw & \\targ & \\qw\n}`,
  rotation: q("& \\gate{R_y(\\theta)} & \\qw"),
  hzhEqualsX: q("& \\gate{H} & \\gate{Z} & \\gate{H} & \\qw & \\push{=} & \\gate{X} & \\qw"),
  cnotFromCz: q(
    "& \\qw & \\ctrl{1} & \\qw & \\qw & \\push{=} & \\ctrl{1} & \\qw \\\\\n& \\gate{H} & \\control{} & \\gate{H} & \\qw & & \\targ{} & \\qw",
  ),
  qft3: q(
    "& \\gate{H} & \\gate{R_2} & \\gate{R_3} & \\qw & \\qw & \\qw & \\swap{2} & \\qw \\\\\n& \\qw & \\ctrl{-1} & \\qw & \\gate{H} & \\gate{R_2} & \\qw & \\qw & \\qw \\\\\n& \\qw & \\qw & \\ctrl{-2} & \\qw & \\ctrl{-1} & \\gate{H} & \\targX{} & \\qw",
  ),
  measured: q(
    "& \\gate{H} & \\ctrl{1} & \\qw & \\meter{} \\\\\n& \\qw & \\targ{} & \\qw & \\meter{}",
  ),
  partial: q(
    '\\lstick{$q_1$} & \\ctrl{1} & \\qw \\\\\n\\lstick{$q_2$} & \\targ{} & \\ctrl{1} \\wire[l][1]["\\iddots"{below,pos=0.1}]{a} \\\\\n\\lstick{$q_3$} & \\qw & \\targ{}',
  ),
  bundle: q("& \\gate{H} & \\ctrl{1} & \\qw \\\\\n\\lstick{$a$} & \\qwbundle{m} & \\targ{} & \\qw"),
};

// The text of the screen's "Bell pair (circuit/1)" example, which is sent as it is typed.
const BELL_JSON = {
  format: "circuit/1",
  wires: 2,
  parts: [
    [
      [{ kind: "gate", label: "H", targets: [1] }],
      [{ kind: "x", label: "X", targets: [2], controls: [1] }],
    ],
  ],
};

const BELL_JSON_TEXT = JSON.stringify(BELL_JSON, null, 2);
const HZH_JSON_TEXT = JSON.stringify({
  format: "circuit/1",
  wires: 1,
  parts: [
    [
      [{ kind: "gate", label: "H", targets: [1] }],
      [{ kind: "gate", label: "Z", targets: [1] }],
      [{ kind: "gate", label: "H", targets: [1] }],
    ],
  ],
});

async function post(path, body, raw) {
  const response = await fetch(`${api}/api/v1/circuits/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: raw ?? JSON.stringify(body),
  });
  return { status: response.status, response: await response.json() };
}

async function parsed(name) {
  const { response } = await post("parse", { latex: LATEX[name] });
  return response.circuits[0];
}

const cases = [];
async function add(name, path, body, raw) {
  // The screen always sends `unitary` with a run, true or false, so the recorded request does too.
  if (path === "run" && body && body.unitary === undefined) body = { ...body, unitary: false };
  const answer = await post(path, body, raw);
  cases.push({ name, path, request: raw === undefined ? body : { raw }, ...answer });
}

for (const [key, label] of [
  ["bell", "parse bell"],
  ["ghz", "parse ghz qcircuit"],
  ["rotation", "parse rotation"],
  ["hzhEqualsX", "parse hzh equals x"],
  ["cnotFromCz", "parse cnot from cz"],
  ["qft3", "parse qft3"],
  ["measured", "parse measured"],
  ["partial", "parse partial"],
  ["bundle", "parse bundle"],
]) {
  await add(label, "parse", { latex: LATEX[key] });
}
await add("parse two circuits", "parse", { latex: `${LATEX.bell}\n\n${LATEX.hzhEqualsX}` });
await add("parse unparsed", "parse", { latex: "no circuit here" });

const text = async (name) => JSON.stringify(await parsed(name));
await add("run bell", "run", { circuit_json: await text("bell") });
await add("run bell unitary", "run", { circuit_json: await text("bell"), unitary: true });
await add("run ghz", "run", { circuit_json: await text("ghz") });
await add("run bell input 10", "run", { circuit_json: await text("bell"), input: "10" });
await add("run measured", "run", { circuit_json: await text("measured") });
await add("run hzh both sides", "run", { circuit_json: await text("hzhEqualsX") });
await add("run symbolic", "run", { circuit_json: await text("rotation") });
await add("run symbolic bound", "run", {
  circuit_json: await text("rotation"),
  values: { theta: "\\pi/2" },
});
await add("run qft3", "run", { circuit_json: await text("qft3"), unitary: true });
await add("run partial refused", "run", { circuit_json: await text("partial") });
await add("run bundle refused", "run", { circuit_json: await text("bundle") });
await add("run bad input", "run", { circuit_json: BELL_JSON_TEXT, input: "2" });
await add("run not a circuit", "run", { circuit_json: '{"a": 1}' });
await add(
  "run duplicate key",
  "run",
  undefined,
  JSON.stringify({ circuit_json: '{"format":"circuit/1","wires":1,"wires":2,"parts":[]}' }),
);
await add("run thirteen wires", "run", {
  circuit_json: JSON.stringify({
    format: "circuit/1",
    wires: 13,
    parts: [[[{ kind: "gate", label: "H", targets: [1] }]]],
  }),
});

const root = 0.7071067811865476;
const bell = await text("bell");
await add("check bell state equal", "check", {
  circuit_json: bell,
  target: { state: [root, 0, 0, root] },
});
await add("check bell state not equal", "check", { circuit_json: bell, target: { state: "11" } });
await add("check bell json against latex", "check", {
  circuit_json: BELL_JSON_TEXT,
  target: { circuit_json: bell },
});
await add("check hzh drawn", "check", { circuit_json: await text("hzhEqualsX") });
await add("check cnot from cz drawn", "check", { circuit_json: await text("cnotFromCz") });
await add("check hzh unitary x", "check", {
  circuit_json: HZH_JSON_TEXT,
  target: {
    unitary: [
      [0, 1],
      [1, 0],
    ],
  },
});
await add("check no target refused", "check", { circuit_json: BELL_JSON_TEXT });
await add("check unitary wrong size", "check", {
  circuit_json: bell,
  target: {
    unitary: [
      [1, 0],
      [0, 1],
    ],
  },
});

const conventions = await fetch(`${api}/api/v1/circuits/conventions`);
cases.push({
  name: "conventions",
  path: "conventions",
  request: null,
  status: conventions.status,
  response: await conventions.json(),
});

writeFileSync(out, `${JSON.stringify(cases, null, 2)}\n`);
console.log(`wrote ${cases.length} answers to ${out}`);
