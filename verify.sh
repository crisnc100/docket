#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/python"
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
docker compose build
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
uv run detect-secrets scan --all-files --exclude-files '(\.lock$|corpus/|\.pytest_cache/|\.ruff_cache/|\.cache/|\.venv/|\.git/|builds/.*\.(jsonl|err|log)$)' > .cache/secrets.json
uv run python scripts/check_secrets.py .cache/secrets.json
