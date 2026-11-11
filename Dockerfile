FROM ghcr.io/astral-sh/uv:0.9.11-python3.12-bookworm-slim
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev
CMD ["python", "-m", "docket.worker"]
