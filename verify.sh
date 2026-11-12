#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/python"
export npm_config_cache="$PWD/.cache/npm"
export BUILDX_CONFIG="$PWD/.cache/buildx"
fault=false
case "${1:-}" in
  "") ;;
  --fault) fault=true ;;
  *) echo "Usage: ./verify.sh [--fault]" >&2; exit 2 ;;
esac
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python scripts/pack_exchange.py --check
npm --prefix web ci
npm --prefix web run build
docker compose --profile test build
if "$fault"; then
  docker compose up -d --wait
fi
uv run alembic upgrade head
test_paths=(tests --ignore=tests/fault)
if "$fault"; then
  test_paths=(tests)
fi
uv run pytest "${test_paths[@]}" --cov=docket.core --cov-branch --cov-report=term-missing --cov-report=json:.cache/coverage.json
uv run python scripts/check_coverage.py .cache/coverage.json
uv run python scripts/reproduce_eval.py
# The scan skips .env, which holds local keys, so it must stay ignored by git.
if ! git check-ignore -q .env; then
  echo "FAIL: .env is not ignored by git, so the secret scan cannot skip it" >&2
  exit 1
fi
uv run detect-secrets scan --all-files --exclude-files '(^\.env$|\.lock$|package-lock\.json$|node_modules/|web/dist/|web/test-results/|corpus/|\.pytest_cache/|\.ruff_cache/|\.cache/|\.venv/|\.git/|builds/.*\.(jsonl|err|log)$)' > .cache/secrets.json
uv run python scripts/check_secrets.py .cache/secrets.json
