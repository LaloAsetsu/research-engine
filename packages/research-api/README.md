# research-api

The HTTP API of the research engine. A FastAPI service built in the same hexagonal style as [docingest](../docingest/README.md): the rules sit in a pure core, the libraries sit in adapters, and import-linter contracts keep it that way. It will serve the web app with cited answers, question sets, benchmark runs and the circuit check, using docingest and [quantum-sim](../quantum-sim/README.md) as libraries.

It has the layers, the import contracts, the settings loader, a fake mode, the error format, the OpenAPI export and the circuit check endpoints. Everything else (sign-in, asks, sets, benchmarks) arrives in later pull requests, each adding use cases, ports and adapters inside this structure.

## Run it

```bash
uv sync --locked --all-extras             # from the repository root
cd packages/research-api
uv run research-api serve --fake          # test data, 127.0.0.1:8090 (--port N for another port)
curl -s http://127.0.0.1:8090/api/v1/health
curl -s http://127.0.0.1:8090/api/v1/status
```

`--fake` builds the service from in-memory stand-ins: no settings file, no network, no files, and a clock fixed at 2026-01-01 so documents repeat exactly. It answers on loopback only. It is what the end-to-end tests of the web app will run against.

A real run needs a settings file:

```bash
uv run research-api serve --config /path/to/research-api.toml    # or set RESEARCH_API_CONFIG
```

Start from [`config.example.toml`](config.example.toml). Keep your copy outside the repository.

| Key | Default | Rule |
|---|---|---|
| `bind_host` | `127.0.0.1` | A loopback address or a Tailscale address (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`). `0.0.0.0`, a LAN address or a host name is refused by the settings loader. |
| `port` | `8090` | An integer from 1 to 65535 (`"8090"` and `8090.0` are refused). |
| `public_origin` | `http://<bind_host>:<port>` | The origin people type in the browser: scheme, host and port only (no path, query, fragment or credentials). It is rewritten the way a browser sends it in the `Origin` header: scheme and host in lowercase, an IPv6 host in its short form, no leading zeros in the port, and no port when it is the default one (`HTTP://Tool.Example:080` becomes `http://tool.example`). A value no browser sends is refused: an empty or zero port, a space or other stray character, a trailing dot, a numeric host that is not a plain IPv4 address. Later used to check where a write request came from. |

| `[circuit_caps]` | see [Circuit check](#circuit-check) | A table with `max_body_bytes`, `max_ops` and `timeout_s`. Each is checked against a range, and a key that is not one of the three is an error. |

A key that is not in the table is an error. Later pull requests add their keys here (database path, data folder, model servers, session lifetimes).

To run under another process manager: `uvicorn research_api.entrypoints.http:create_app --factory`, with `RESEARCH_API_CONFIG` (or `RESEARCH_API_FAKE=1`) in the environment. The `serve` command turns the access log off; use it or pass `--no-access-log` to uvicorn, because a raw access log would hold paths and query strings.

The address rule does not depend on how the server is started. The app checks the local address of every connection (`scope["server"]`, set by uvicorn) and answers `403` with a problem document when it is not allowed: loopback only in fake mode; loopback or exactly the `bind_host` address in live mode (the rest of the Tailscale block, `100.64.0.0/10`, is also carrier-grade NAT space that other hosts use, so it is not accepted). So `uvicorn ... --factory --host 0.0.0.0` listens on every interface but serves nothing on the ones it must not. Prefer `--host` set to the address you mean anyway, so the port is not open on the others. A server without an IP address (a Unix socket) is refused too. The check is in `entrypoints/http/guard.py`.

## What it serves now

All paths are under `/api/v1`. There is no sign-in yet, so run it on loopback only until the auth pull request lands. The `Host` header is not checked yet either (a web page can point a name it controls at 127.0.0.1); the auth pull request adds that check, with the hosts taken from `public_origin`, `bind_host` and loopback.

| Method and path | Does |
|---|---|
| `GET /health` | `{"ok": true}`, for a start script or a load check. |
| `GET /status` | `mode` (`live` or `fake`, an enum in the contract), `api_version`, `started_at`, `uptime_s`, `ready`. A stub: index state, model servers and queues join it as their parts arrive. |

The circuit endpoints are described [below](#circuit-check).

No documentation pages and no schema URL are served (the stock pages load scripts from a CDN). The contract is exported instead:

```bash
uv run research-api openapi > openapi.json     # OpenAPI 3.1, keys sorted, same text every run and every hash seed
```

The export is built in fake mode, so it needs no settings file. The [web app](../../apps/web/README.md) commits it (`apps/web/src/api/openapi.json`) and generates its TypeScript client from it, and CI fails when the two drift: after a change to the API, run `uv run research-api openapi -o ../../apps/web/src/api/openapi.json` and then `pnpm gen:api` in `apps/web`.

### Errors

Every error leaves as `application/problem+json` ([RFC 9457](https://www.rfc-editor.org/rfc/rfc9457)):

```json
{"type": "urn:research-api:problem:not-found", "title": "Not Found", "status": 404,
 "detail": "No such set.", "instance": "/api/v1/sets/x"}
```

`errors` (a list of `{path, message}`, `path` a JSON pointer) appears on 422. Fields that do not apply are left out, never sent as `null`, and the published schema says so. `Retry-After` is sent on 429 and, when known, on 503. A 500 carries `error_id`, which is also in the server log together with the stack trace (a domain error with no status of its own is a 500 too); the response never holds a stack trace, and a validation problem never repeats the rejected input.

| Domain error | Status | `type` ends with |
|---|---|---|
| `BadRequestError` (also a body that is not valid JSON) | 400 | `bad-request` |
| `UnauthenticatedError` | 401 | `unauthenticated` |
| `ForbiddenError` | 403 | `forbidden` |
| `NotFoundError` | 404 | `not-found` |
| `ConflictError` | 409 | `conflict` |
| `TooLargeError` | 413 | `too-large` |
| `InvalidInputError` (also a request that fails validation) | 422 | `invalid-input` |
| `RateLimitedError` | 429 | `rate-limited` |
| `UpstreamError` | 502 | `upstream-error` |
| `UnavailableError` | 503 | `unavailable` |
| anything else | 500 | `internal` |

Framework errors (unknown path, wrong method) use the same format with the status phrase as the slug, for example `method-not-allowed`. Domain errors know nothing about HTTP; `entrypoints/http/problems.py` holds the mapping.

## Circuit check

Four endpoints read a circuit drawn in LaTeX, simulate a circuit and compare it with a state, a unitary or another circuit. They use docingest's `gate_list` and [quantum-sim](../quantum-sim/README.md) as libraries, in the same process, on the CPU. They need no model server and no GPU, so fake mode runs the real engine.

| Method and path | Body | Does |
|---|---|---|
| `POST /circuits/parse` | `{"latex": "..."}` | docingest reads the code of one block (quantikz, Qcircuit, yquant, tikz-cd). Returns `status` (`parsed`, `partial` or `unparsed`), `text` (the gate list in words) and `circuits` (each one a `circuit/1` document, `CircuitSteps.to_dict()`). |
| `POST /circuits/run` | `{"circuit_json": "...", "input"?, "values"?, "unitary"?}` | The state and the outcome probabilities, and the unitary when `unitary` is true. `input` is a basis label (`"010"`, wire 1 first) or a list of amplitudes. `values` gives each symbol a number or an angle written as text, such as `\pi/2`. |
| `POST /circuits/check` | `{"circuit_json": "...", "target"?, "input"?, "values"?}` | Equal up to global phase? `target` is exactly one of `{"state": ...}`, `{"unitary": [[...]]}` or `{"circuit_json": "..."}` (a second circuit). With no `target` (leave it out), the two sides of an equality drawn in the circuit are compared; `"target": null` is a `422`, as in quantum-sim. |
| `GET /circuits/conventions` | none | What `quantum-sim conventions` prints: the conventions, the gate table and quantum-sim's own limits. |

**The circuit is text.** `circuit_json` holds the JSON of a `circuit/1` document as a string, not as an object. quantum-sim refuses a key written twice (`"controls"` twice in one item), because the last value would win and the document does not say which is meant. A JSON body parsed by FastAPI would keep the last value without a word, so the API reads the text itself with a parser that refuses repeats. The same check covers the request body (`"unitary": true, "unitary": false`) and the second circuit of a check (`target.circuit_json`). A repeated key, JSON that does not parse, `NaN` and `Infinity` are a `400` that names the input and the key and never repeats the text. The web app sends `JSON.stringify(circuit)` of a circuit that `/circuits/parse` returned, or the text a person pasted.

**The answer is the simulator's.** `run` and `check` return the `quantum-sim-result/1` document as it is (`status`, `conventions`, `input`, `circuits`, `comparisons`, `equal_to_target`, `notes`, `refused`); the API does not re-model it. A circuit the simulator refuses (an unknown gate label, a postselection, a bundle wire, an unparsed part) is a `200` with `"status": "refused"` and the reasons, like the command's exit status 1. A check that finds the circuits different is a `200` with `"equal_to_target": false`. The OpenAPI type of these bodies is a plain object.

**Caps.** The endpoints are synchronous and meant for small inputs. Beyond the caps the answer is a `422` whose `detail` points to the `quantum-sim` command, and `errors` holds the JSON pointer of the circuit field (`/circuit_json`, `/target/circuit_json`). quantum-sim keeps its own, larger limits inside (20 wires, 10,000 operations, 32 Mi characters).

| Cap | Value | Answer | Why |
|---|---|---|---|
| Request body | `max_body_bytes`, 1 MiB | `413`, decided from `Content-Length` or while the body streams in, before it is held in memory | memory |
| Wires for a simulation or a check against a state | 12 | `422` | a state is 2^n amplitudes |
| Wires for anything that builds a unitary: `unitary: true`, a check against a unitary, against a second circuit, or a drawn equality | 8 | `422` | a unitary is 4^n entries and each operation costs 4^n. Measured on 2,000 operations: 12 wires of state take 0.1 s, an exact comparison of 8 wires 0.3 s and of 10 wires 41 s |
| Operations in the circuits of one request | `max_ops`, 2,000 | `422` | time. A gate over several targets counts once for each, barriers and text count none, so the count errs high |
| Time | `timeout_s`, 30 | `422` | a backstop for the caps |
| Jobs at once | 2 | `503` with `Retry-After: 5` | the CPU of a shared machine |

The three settings are in the `[circuit_caps]` table of the settings file ([`config.example.toml`](config.example.toml)). `max_body_bytes` goes up to 32 MiB, `max_ops` up to 10,000 and `timeout_s` up to 600, which are quantum-sim's limits and ten minutes. The wire caps and the number of jobs are constants of the code.

**Time limit, honestly.** A Python thread cannot be stopped. The work runs in a daemon thread; when it outlives `timeout_s` the request is released with the `422` and the thread finishes on its own, still holding one of the two slots. So a slow circuit cannot pile up work without bound (the caps keep each job short, the slots keep them few), and a stopping server does not wait for them. If this ever matters, the next step is a worker process that can be killed.

**No sign-in yet.** Like `/health` and `/status`, these endpoints are open until the auth pull request adds the member role to them. Run on loopback only until then. They log no request body and no circuit text.

## Layout

```text
src/research_api/
  domain/          errors, ServerStatus, the circuit rules (strict JSON, caps). Standard library only.
  ports/           Protocols (Clock, CircuitEngine) and the values that cross them
  application/     use cases (ReportStatus, ParseCircuit, RunCircuit, CheckCircuit,
                   ShowCircuitConventions)
  adapters/        clock, fake (fake mode), circuits (docingest gate lists and quantum-sim, with the
                   thread that bounds the work), and empty folders for the adapters to come:
                   persistence, docingest_ask, bench, hashing, files, models
  bootstrap/       settings.py (the TOML loader and its rules), container.py (wiring, fake mode)
  entrypoints/     http/ (FastAPI app, routes, schemas, problem details), cli.py
```

The six layers import downward only: `entrypoints`, `bootstrap`, `adapters`, `application`, `ports`, `domain`. Only `bootstrap` and `entrypoints` import adapters. Use cases take their ports as keyword-only constructor arguments, as in docingest.

## Import contracts

Seven rules, enforced by import-linter (`uv run lint-imports`, a gate in `scripts/check.sh` and in the Linux CI lane). Rule 5 is written as four `forbidden` contracts, one per library. Each one covers the whole package (`source_modules = ["research_api"]`) and lists as exceptions only the adapters that own the library, so the package root, `adapters/__init__.py` and any adapter added later are checked too.

| # | Rule | Type |
|---|---|---|
| 1 | The six layers above, outer to inner | `layers` |
| 2 | The nine adapter areas (`clock`, `fake`, `persistence`, `docingest_ask`, `circuits`, `bench`, `hashing`, `files`, `models`) do not import each other; a test fails when a folder or module under `adapters/` is missing from the list | `independence` |
| 3 | `domain` imports no `fastapi`, `starlette`, `sqlalchemy`, `alembic`, `pwdlib`, `argon2`, `httpx`, `uvicorn`, `docingest`, `docingest_index`, `quantum_sim`, `qa_bench` or `jsonschema` | `forbidden` |
| 4 | `application` and `ports` import none of those: they reach docingest, quantum-sim and qa-bench only through ports | `forbidden` |
| 5 | Only `adapters.docingest_ask` and `adapters.circuits` import `docingest`; only `adapters.circuits` imports `quantum_sim`; only `adapters.bench` imports `qa_bench`; only `adapters.persistence` imports `sqlalchemy` and `alembic` | `forbidden` (four) |
| 6 | Only `bootstrap` and `entrypoints` import `adapters` | `protected` |
| 7 | `research_api` never imports `docingest_index` (docingest finds its plugin by entry point) | `forbidden` |

The reverse direction is a contract in each of the other packages: docingest, docingest-index and quantum-sim never import `research_api` or `qa_bench`.

Rules 3 to 5 also name libraries the package does not import yet (sqlalchemy, qa_bench); the circuits adapter is the first real user of the docingest and quantum_sim exceptions. The rule is in place before the first line that would break it. `tests/test_contracts.py` copies the package to a temporary folder, adds one forbidden import per case and checks that the right contract breaks, with controls for the imports that must stay allowed. A contract that silently stopped working would otherwise pass forever.

When a contract fails, fix the import; do not relax the contract. Adding an adapter area means adding it to rule 2 (`tests/test_contracts.py` fails until you do) and, if it owns one of the libraries of rule 5, to that contract's `ignore_imports` (both the `adapters.<name> -> lib` and the `adapters.<name>.** -> lib` form), and saying so in the pull request.

## Tests and gates

```bash
./scripts/check.sh        # ruff, ruff format, lint-imports, pyright, pytest with coverage (floor 85%)
```

`tests/` holds one file per concern: `test_settings.py`, `test_container.py`, `test_status.py`, `test_http.py` (routes, problem details, the OpenAPI text), `test_guard.py` (the address rule), `test_cli.py`, `test_contracts.py`, and four for the circuit check: `test_circuits_domain.py` (strict JSON, counting, caps), `test_circuits_lane.py` (the thread, the timeout, the adapter), `test_circuits_http.py` (the four routes, duplicate keys, refusals, caps, the contract) and `test_circuits_bridge.py`. The last one sends every LaTeX source of `packages/quantum-sim/tests/fixtures/bridge/` through `/circuits/parse`, `/circuits/run` and `/circuits/check` and expects what quantum-sim's `test_bridge.py` expects: the saved document, the refusals, the hand-written unitaries, the drawn identities and the three-wire QFT equal to the DFT. HTTP tests use FastAPI's `TestClient` over the fake container, so they need no server and no files. Test dependencies come from the `dev` group; `TestClient` needs `httpx`.

CI runs the package in the Linux lane (ruff, format, lint-imports, pytest) and the macOS lane (pyright, pytest), like the other packages.
