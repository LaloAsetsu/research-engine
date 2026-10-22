# research-api

The HTTP API of the research engine. A FastAPI service built in the same hexagonal style as [docingest](../docingest/README.md): the rules sit in a pure core, the libraries sit in adapters, and import-linter contracts keep it that way. It will serve the web app with cited answers, question sets, benchmark runs and the circuit check, using docingest and [quantum-sim](../quantum-sim/README.md) as libraries.

This is the skeleton. It has the layers, the import contracts, the settings loader, a fake mode, two endpoints, the error format and the OpenAPI export. Everything else (sign-in, asks, sets, benchmarks, circuits) arrives in later pull requests, each adding use cases, ports and adapters inside this structure.

## Run it

```bash
uv sync --locked --all-extras             # from the repository root
cd packages/research-api
uv run research-api serve --fake          # test data, 127.0.0.1:8090
curl -s http://127.0.0.1:8090/api/v1/health
curl -s http://127.0.0.1:8090/api/v1/status
```

`--fake` builds the service from in-memory stand-ins: no settings file, no network, no files, and a clock fixed at 2026-01-01 so documents repeat exactly. It only binds a loopback address. It is what the end-to-end tests of the web app will run against.

A real run needs a settings file:

```bash
uv run research-api serve --config /path/to/research-api.toml    # or set RESEARCH_API_CONFIG
```

Start from [`config.example.toml`](config.example.toml). Keep your copy outside the repository.

| Key | Default | Rule |
|---|---|---|
| `bind_host` | `127.0.0.1` | A loopback address or a Tailscale address (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`). `0.0.0.0`, a LAN address or a host name is refused, so the service is never reachable from a shared network by accident. |
| `port` | `8090` | 1 to 65535. |
| `public_origin` | `http://<bind_host>:<port>` | The origin people type in the browser: scheme, host and port only. Later used to check where a write request came from. |

A key that is not in the table is an error. Later pull requests add their keys here (database path, data folder, model servers, session lifetimes).

To run under another process manager: `uvicorn research_api.entrypoints.http:create_app --factory`, with `RESEARCH_API_CONFIG` (or `RESEARCH_API_FAKE=1`) in the environment. The `serve` command turns the access log off; use it or pass `--no-access-log` to uvicorn, because a raw access log would hold paths and query strings.

## What it serves now

All paths are under `/api/v1`. There is no sign-in yet, so run it on loopback only until the auth pull request lands.

| Method and path | Does |
|---|---|
| `GET /health` | `{"ok": true}`, for a start script or a load check. |
| `GET /status` | `mode` (`live` or `fake`), `api_version`, `started_at`, `uptime_s`, `ready`. A stub: index state, model servers and queues join it as their parts arrive. |

No documentation pages and no schema URL are served (the stock pages load scripts from a CDN). The contract is exported instead:

```bash
uv run research-api openapi > openapi.json     # OpenAPI 3.1, keys sorted, same text every run
```

The export is built in fake mode, so it needs no settings file. The web app will commit it and generate its TypeScript client from it, and CI will fail when the two drift.

### Errors

Every error leaves as `application/problem+json` ([RFC 9457](https://www.rfc-editor.org/rfc/rfc9457)):

```json
{"type": "urn:research-api:problem:not-found", "title": "Not Found", "status": 404,
 "detail": "No such set.", "instance": "/api/v1/sets/x"}
```

`errors` (a list of `{path, message}`, `path` a JSON pointer) appears on 422. `Retry-After` is sent on 429 and, when known, on 503. A 500 carries `error_id`, which is also in the server log together with the stack trace; the response never holds a stack trace, and a validation problem never repeats the rejected input.

| Domain error | Status | `type` ends with |
|---|---|---|
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

## Layout

```text
src/research_api/
  domain/          errors, ServerStatus. Standard library only.
  ports/           Protocols (Clock) and the values that cross them
  application/     use cases (ReportStatus)
  adapters/        clock, fake (fake mode), and empty folders for the adapters to come:
                   persistence, docingest_ask, circuits, bench, hashing, files, models
  bootstrap/       settings.py (the TOML loader and its rules), container.py (wiring, fake mode)
  entrypoints/     http/ (FastAPI app, routes, schemas, problem details), cli.py
```

The six layers import downward only: `entrypoints`, `bootstrap`, `adapters`, `application`, `ports`, `domain`. Only `bootstrap` and `entrypoints` import adapters. Use cases take their ports as keyword-only constructor arguments, as in docingest.

## Import contracts

Seven rules, enforced by import-linter (`uv run lint-imports`, a gate in `scripts/check.sh` and in the Linux CI lane). Rule 5 is written as four `forbidden` contracts, one per library, each listing every module except the adapter that owns the library.

| # | Rule | Type |
|---|---|---|
| 1 | The six layers above, outer to inner | `layers` |
| 2 | The seven adapter areas (`persistence`, `docingest_ask`, `circuits`, `bench`, `hashing`, `files`, `models`) do not import each other | `independence` |
| 3 | `domain` imports no `fastapi`, `starlette`, `sqlalchemy`, `alembic`, `pwdlib`, `argon2`, `httpx`, `uvicorn`, `docingest`, `docingest_index`, `quantum_sim`, `qa_bench` or `jsonschema` | `forbidden` |
| 4 | `application` and `ports` import none of those: they reach docingest, quantum-sim and qa-bench only through ports | `forbidden` |
| 5 | Only `adapters.docingest_ask` and `adapters.circuits` import `docingest`; only `adapters.circuits` imports `quantum_sim`; only `adapters.bench` imports `qa_bench`; only `adapters.persistence` imports `sqlalchemy` and `alembic` | `forbidden` (four) |
| 6 | Only `bootstrap` and `entrypoints` import `adapters` | `protected` |
| 7 | `research_api` never imports `docingest_index` (docingest finds its plugin by entry point) | `forbidden` |

The reverse direction is a contract in each of the other packages: docingest, docingest-index and quantum-sim never import `research_api` or `qa_bench`.

Rules 3 to 5 name libraries the package does not import yet. That is deliberate: the rule is in place before the first line that would break it. `tests/test_contracts.py` copies the package to a temporary folder, adds one forbidden import per case and checks that the right contract breaks, with controls for the imports that must stay allowed. A contract that silently stopped working would otherwise pass forever.

When a contract fails, fix the import; do not relax the contract. Adding an adapter area means adding it to rule 2 and to the module lists of rule 5, and saying so in the pull request.

## Tests and gates

```bash
./scripts/check.sh        # ruff, ruff format, lint-imports, pyright, pytest with coverage (floor 85%)
```

`tests/` holds one file per concern: `test_settings.py`, `test_container.py`, `test_status.py`, `test_http.py` (routes, problem details, the OpenAPI text), `test_cli.py`, `test_contracts.py`. HTTP tests use FastAPI's `TestClient` over the fake container, so they need no server and no files. Test dependencies come from the `dev` group; `TestClient` needs `httpx`.

CI runs the package in the Linux lane (ruff, format, lint-imports, pytest) and the macOS lane (pyright, pytest), like the other packages.
