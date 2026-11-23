FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY services/api/pyproject.toml services/api/
COPY services/connector/pyproject.toml services/connector/
COPY packages/camera-adapters packages/camera-adapters
COPY packages/event-bus packages/event-bus
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package smart-surveillance-backend --no-install-project

FROM python:3.13.11-slim-bookworm

ARG REVISION=unknown
LABEL org.opencontainers.image.title="smart-surveillance-api" \
      org.opencontainers.image.description="Platform API of Smart Surveillance System" \
      org.opencontainers.image.source="https://github.com/Shavkatjon-O/Smart-Surveillance-System" \
      org.opencontainers.image.revision="${REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

COPY --from=build /opt/venv /opt/venv

WORKDIR /app

COPY services/api/alembic.ini ./
COPY services/api/migrations ./migrations
COPY services/api/app ./app

RUN groupadd --system --gid 10001 cameras \
    && useradd --system --uid 10001 --gid cameras --no-create-home --shell /usr/sbin/nologin cameras \
    && mkdir -p /data/events \
    && chown cameras:cameras /data/events

USER cameras

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]
