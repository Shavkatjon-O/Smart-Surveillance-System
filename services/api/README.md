# API

The FastAPI platform API of Smart Surveillance System: the camera registry, live view signalling, PTZ and camera settings, events, analytics configuration, people, counts, alerting and administration. It serves /api/v1 to the console and the TV app, and /internal to the analytics service and the go2rtc TV composer only. The root README describes the product; docs/architecture.md describes the domain modules.

## Run

The compose service backend builds the image, applies the Alembic migrations and starts uvicorn on port 8000 inside the stack network; from deploy/server:

```
docker compose up -d --build backend
docker compose logs -f backend
```

Write the OpenAPI document to packages/contracts/openapi.json with scripts/export-openapi.sh.

Create a migration from this folder against a database already at head, and never edit it by hand:

```
alembic upgrade head
alembic revision --autogenerate -m "<message>"
```

## Test

Run from the repository root:

```
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uv-cache -v $PWD:/repo -w /repo/services/api ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c "uv sync --frozen --all-packages && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen pytest -q"
```

tests/test_architecture.py runs the import-linter contracts of pyproject.toml, which enforce the dependency rules.

## Configuration

Settings come from SSS_ environment variables and the .env file of the deploy target, such as deploy/server/.env. The full list with defaults is the Settings class in app/core/config.py; the groups are:

| Group | Variables |
|---|---|
| Database | SSS_DB_HOST, SSS_DB_PORT, SSS_DB_NAME, SSS_DB_USER, SSS_DB_PASSWORD |
| Accounts | SSS_SECRET_KEY, SSS_ADMIN_USERNAME, SSS_ADMIN_PASSWORD, SSS_SESSION_HOURS, SSS_REGISTRATION_ENABLED, SSS_PUBLIC_URL |
| Tokens | SSS_ACCESS_TOKEN_MINUTES, SSS_ACCESS_TOKEN_KEY, SSS_REFRESH_IDLE_DAYS, SSS_REFRESH_MAX_DAYS |
| Mail | SSS_SMTP_HOST, SSS_SMTP_PORT, SSS_SMTP_USERNAME, SSS_SMTP_PASSWORD, SSS_MAIL_FROM |
| Streaming | SSS_GO2RTC_URL, SSS_TRANSCODE_HARDWARE, SSS_TV_RTSP_USERNAME, SSS_TV_RTSP_PASSWORD |
| Connectors | SSS_CONNECTOR_CODE_MINUTES, SSS_CONNECTOR_POLL_SECONDS, SSS_CONNECTOR_OFFLINE_SECONDS |
| Push | SSS_FCM_PROJECT_ID, SSS_FCM_SERVICE_ACCOUNT_FILE, SSS_NTFY_URL, SSS_NTFY_TOKEN |
| Retention | SSS_EVENTS_DIR, SSS_EVENTS_RETENTION_DAYS, SSS_COUNTS_RETENTION_DAYS, SSS_HEALTH_RETENTION_DAYS |
| Analytics | SSS_ANALYTICS_URL, SSS_ANALYTICS_TOKEN |
| Cameras | SSS_DISCOVERY_SUBNET, SSS_MONITOR_INTERVAL_SECONDS, SSS_HEALTH_INTERVAL_SECONDS, SSS_REPLAY_ENABLED, SSS_TIMEZONE |
