#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/python"
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run alembic upgrade head
uv run pytest
uv run detect-secrets scan --all-files --exclude-files '(\.lock$|corpus/|\.pytest_cache/|\.ruff_cache/|\.cache/|\.venv/|\.git/|builds/.*\.(jsonl|err|log)$)' > .cache/secrets.json
uv run python -c 'import json; r=json.load(open(".cache/secrets.json"))["results"]; print("detect-secrets:", r); raise SystemExit(bool(r))'
