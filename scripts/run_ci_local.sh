#!/usr/bin/env bash
# Run the same gates as .github/workflows/ci.yml locally (no remote push required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "== ruff =="
uv run ruff check -q . && uv run ruff format --check -q .
echo "== pytest =="
uv run pytest -q
echo "== frontend lint/tsc/test/build =="
cd frontend
npx eslint . --max-warnings=0
npx tsc --noEmit -p .
npx vitest run
npm run -s build
cd ..
echo "== alembic heads =="
uv run alembic -c alembic.ini heads >/dev/null
echo "== pip-audit (if available) =="
if uv run pip-audit -h >/dev/null 2>&1; then
  uv run pip-audit -r <(uv export --frozen --no-dev --no-emit-project -q) || true
else
  echo "pip-audit not installed; skipping (CI workflow installs it)"
fi
echo "== npm audit (frontend) =="
cd frontend && npm audit --audit-level=high || true
cd ..
echo "CI local gates finished."
