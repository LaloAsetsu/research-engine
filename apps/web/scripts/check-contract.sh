#!/usr/bin/env bash
# The API contract has not drifted from the web app: the committed OpenAPI document equals what
# `research-api openapi` prints now, and the committed TypeScript types equal what
# openapi-typescript generates from it. Run from anywhere; needs uv and pnpm.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

(cd ../.. && uv run --frozen research-api openapi) > "$tmp/openapi.json"
if ! diff -u src/api/openapi.json "$tmp/openapi.json"; then
  echo "src/api/openapi.json is out of date. Regenerate it, then the types:" >&2
  echo "  uv run research-api openapi -o apps/web/src/api/openapi.json && (cd apps/web && pnpm gen:api)" >&2
  exit 1
fi

pnpm exec openapi-typescript src/api/openapi.json -o "$tmp/schema.d.ts" > /dev/null
if ! diff -u src/shared/api/schema.d.ts "$tmp/schema.d.ts"; then
  echo "src/shared/api/schema.d.ts is out of date. Run: pnpm gen:api" >&2
  exit 1
fi
echo "contract: openapi.json and schema.d.ts match the API"
