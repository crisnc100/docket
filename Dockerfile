# Both base images are pinned by the digest of their multi-arch index, so a rebuild gets the
# same layers on Apple Silicon and on an x86_64 host.
FROM node:22.23.5-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS web-deps
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM web-deps AS web
COPY web/ ./
RUN npm run build

# The browser tests only: installing Chromium's system packages needs root.
FROM web-deps AS browser
RUN npx playwright install --with-deps chromium --no-shell
COPY web/ ./
COPY corpus/seed-11 /corpus/seed-11
CMD ["npm", "test"]

FROM ghcr.io/astral-sh/uv:0.9.11-python3.12-bookworm-slim@sha256:ef305979f94d4729707a0fc98444ebf1bc0e63a2d2919a62d28fcc3e42957508
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev
COPY --from=web /web/dist /app/web/dist
# The API, the worker and the reset write nothing under /app; the worker's ready file is in /tmp.
RUN useradd --system --uid 10001 --home-dir /nonexistent docket
USER docket
CMD ["python", "-m", "docket.worker"]
