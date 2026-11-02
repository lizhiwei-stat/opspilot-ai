# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY scripts ./scripts
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && useradd -r -u 10001 app && chown -R app /app
USER app
EXPOSE 8000
# Migrate, then serve. Seeding is an explicit, separate step (make seed).
CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && uv run --no-sync uvicorn opspilot.main:create_app --factory --host 0.0.0.0 --port 8000"]
