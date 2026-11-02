# syntax=docker/dockerfile:1
# Python minor version matches .python-version / CI (3.11); uv version matches local tooling.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
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
# Run the venv binaries directly (no `uv run` at runtime: the non-root user has no uv cache dir).
# Migrate, then serve. Seeding is an explicit, separate step (see README).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn opspilot.main:create_app --factory --host 0.0.0.0 --port 8000"]
