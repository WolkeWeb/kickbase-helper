# One image for both the web app and the worker (different commands).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config
COPY app ./app
COPY docker/app-entrypoint.sh /usr/local/bin/app-entrypoint
COPY scripts/setup.py /srv/setup.py

# Default non-root user; Compose can match the host file owner with APP_UID/APP_GID.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin app
USER 1000:1000

EXPOSE 8000
ENTRYPOINT ["app-entrypoint"]
CMD ["web"]
