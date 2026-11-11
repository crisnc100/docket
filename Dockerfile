FROM node:22-bookworm-slim AS web-deps
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM web-deps AS web
COPY web/ ./
RUN npm run build

FROM web-deps AS browser
RUN npx playwright install --with-deps chromium --no-shell
COPY web/ ./
COPY corpus/seed-11 /corpus/seed-11
CMD ["npm", "test"]

FROM ghcr.io/astral-sh/uv:0.9.11-python3.12-bookworm-slim
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev
COPY --from=web /web/dist /app/web/dist
CMD ["python", "-m", "docket.worker"]
