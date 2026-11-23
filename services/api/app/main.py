"""FastAPI application: lifespan, error handlers, health routes and router mounting."""

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1_routers import v1_routers
from app.bus.connection import bus
from app.bus.relay import LiveRelay
from app.camera_events.engine import camera_events
from app.core.config import APP_NAME, APP_VERSION
from app.core.database import async_engine, verify_db_connection
from app.core.exceptions import ServiceError, Unavailable
from app.core.logging import setup_logging
from app.core.security import require_secret_key
from app.health.prober import health_prober
from app.modules.alerting.services import telegram_bot
from app.modules.analytics.routers import analytics_internal_router
from app.modules.billing.services import sync_plans
from app.modules.identity.services.seed import seed_admin
from app.modules.investigation.routers import people_internal_router
from app.modules.operations.services import audit_pruner
from app.modules.tenancy.services.bootstrap import run_bootstrap
from app.modules.tv.routers import tv_internal_router
from app.streaming.supervisor import supervisor

setup_logging()
logger = logging.getLogger(__name__)

live_relay = LiveRelay(camera_events.broker)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Start the background engines after the database checks and stop them on shutdown.

    The bus comes first, because the engines publish on it and the live relay fills this replica's event streams
    from it; the workers do the rest of the event pipeline in their own containers.

    Args:
        application: The application.

    Yields:
        Control while the application serves requests.
    """
    require_secret_key()
    await verify_db_connection()
    await seed_admin()
    await sync_plans()
    await run_bootstrap()
    await bus.start()
    await live_relay.start()
    await supervisor.start()
    await health_prober.start()
    await camera_events.start()
    await audit_pruner.start()
    await telegram_bot.start()
    logger.info("%s %s started", APP_NAME, APP_VERSION)
    yield
    await telegram_bot.stop()
    await audit_pruner.stop()
    await camera_events.stop()
    await health_prober.stop()
    await supervisor.stop()
    await live_relay.stop()
    await bus.stop()
    await async_engine.dispose()


class ErrorDetail(BaseModel):
    """Machine and human readable reason of a failed request.

    Attributes:
        code: Stable error code, such as camera_not_found.
        message: Explanation for people.
    """

    code: str
    message: str


class ErrorBody(BaseModel):
    """Body of every error response.

    Attributes:
        detail: The reason.
    """

    detail: ErrorDetail


ERROR_RESPONSES = {
    status: {"model": ErrorBody, "description": description}
    for status, description in (
        (401, "Not signed in"),
        (403, "Signed in without the role or site this needs"),
        (404, "Not found"),
        (409, "Conflicts with the current state"),
        (422, "Invalid request"),
        (429, "Too many requests"),
        (502, "A camera, connector or go2rtc did not answer"),
        (503, "Not available right now"),
    )
}


def operation_id(route: APIRoute) -> str:
    """Name an operation after its route function, which stays the same as long as the function keeps its name.

    Args:
        route: The route.

    Returns:
        The function name, such as list_cameras.
    """
    return route.name


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
    generate_unique_id_function=operation_id,
    responses=ERROR_RESPONSES,
)


def error_body(code: str, message: str) -> dict:
    """Build the error body every error response uses.

    Args:
        code: Machine readable error code.
        message: Human readable message.

    Returns:
        The body.
    """
    return {"detail": {"code": code, "message": message}}


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Answer a service error with its status and code.

    Args:
        request: The request.
        exc: The error.

    Returns:
        The error response.
    """
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Answer an HTTP error in the common error shape.

    Args:
        request: The request.
        exc: The error.

    Returns:
        The error response.
    """
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, f"http_{exc.status_code}")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content=error_body(code, message), headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Answer a validation error with every problem in one message.

    Args:
        request: The request.
        exc: The error.

    Returns:
        The error response.
    """
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path"))
        parts.append(f"{location}: {error.get('msg')}" if location else str(error.get("msg")))
    return JSONResponse(status_code=422, content=error_body("validation_error", "; ".join(parts)))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log an unexpected error and answer without its details.

    Args:
        request: The request.
        exc: The error.

    Returns:
        The error response.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content=error_body("internal_error", "Something went wrong"))


async def health_status() -> dict:
    """Check the database.

    Returns:
        The health body.

    Raises:
        Unavailable: The database does not answer.
    """
    try:
        await verify_db_connection()
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Health check failed: %s", exc)
        raise Unavailable("database_unavailable", "The database does not answer")
    return {"status": "ok"}


health_router = APIRouter()


class HealthRead(BaseModel):
    """Whether the backend and its database work.

    Attributes:
        status: ok.
    """

    status: str


class ServiceRead(BaseModel):
    """Name and version of the service.

    Attributes:
        service: Service name.
        version: Service version.
    """

    service: str
    version: str


@health_router.get("/health", tags=["health"], response_model=HealthRead)
async def get_service_health() -> dict:
    """Report whether the backend and its database work.

    Returns:
        The health body.
    """
    return await health_status()


@app.get("/", tags=["health"], response_model=ServiceRead, include_in_schema=False)
async def root() -> dict:
    """Report the service name and version.

    Returns:
        Name and version.
    """
    return {"service": APP_NAME, "version": APP_VERSION}


@app.get("/api/v1/version", tags=["health"], response_model=ServiceRead)
async def get_service_version() -> dict:
    """Report the service name and version.

    Returns:
        Name and version.
    """
    return {"service": APP_NAME, "version": APP_VERSION}


SECTION_HEADINGS = ("Args:", "Returns:", "Raises:", "Yields:")


def public_description(text: str) -> str:
    """Keep the part of a route's docstring that describes the operation, without its code sections.

    Args:
        text: The docstring FastAPI took as the description.

    Returns:
        The paragraphs before the first Args, Returns, Raises or Yields section.
    """
    paragraphs = []
    for paragraph in text.split("\n\n"):
        if paragraph.strip().startswith(SECTION_HEADINGS):
            break
        paragraphs.append(paragraph)
    return "\n\n".join(paragraphs).strip()


generate_openapi = app.openapi


def openapi_document() -> dict:
    """Build the OpenAPI document once, with operation descriptions that leave out the code sections.

    Returns:
        The document.
    """
    if app.openapi_schema is None:
        document = generate_openapi()
        for operations in document.get("paths", {}).values():
            for operation in operations.values():
                if operation.get("description"):
                    operation["description"] = public_description(operation["description"])
    return app.openapi_schema


app.openapi = openapi_document

app.include_router(health_router, include_in_schema=False)
app.include_router(health_router, prefix="/api/v1")
app.include_router(v1_routers, prefix="/api/v1")
app.include_router(tv_internal_router, prefix="/internal/tv", include_in_schema=False)
app.include_router(analytics_internal_router, prefix="/internal/analytics", include_in_schema=False)
app.include_router(people_internal_router, prefix="/internal/people", include_in_schema=False)
