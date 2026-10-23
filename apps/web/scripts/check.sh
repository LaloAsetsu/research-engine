#!/usr/bin/env bash
# Every quality gate of the web app, the same ones CI runs.
# Usage: apps/web/scripts/check.sh [--e2e]
#   `pnpm audit --prod` is not here: it needs the network, so it runs in CI only.
#   --e2e  also run the Playwright smoke test (needs a browser: `pnpm exec playwright install chromium`,
#          or E2E_CHANNEL=chrome to use an installed Chrome; and uv, for the fake API)
set -euo pipefail
cd "$(dirname "$0")/.."
pnpm install --frozen-lockfile
pnpm lint
pnpm format:check
pnpm typecheck
pnpm test --coverage
pnpm build
pnpm size
bash scripts/check-contract.sh
if [[ "${1:-}" == "--e2e" ]]; then
  pnpm e2e
fi
