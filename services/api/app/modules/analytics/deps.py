"""FastAPI dependencies of the analytics module."""

import hmac

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import Unauthorized, Unavailable
from app.modules.analytics.services import (
    AnalyticsConfigService,
    AnalyticsIngestService,
    AnalyticsStatusService,
    AnalyticsViewService,
)
from app.modules.analytics.services.follow import FollowPtzService
from app.modules.tenancy.deps import get_site_access
from app.modules.tenancy.services.access import SiteAccess


def get_view_service(
    session: AsyncSession = Depends(get_db),
    access: SiteAccess = Depends(get_site_access),
) -> AnalyticsViewService:
    """Build the view service for a signed-in request.

    Args:
        session: Request-scoped database session.
        access: Access of the signed-in member to the request's site.

    Returns:
        The service.
    """
    return AnalyticsViewService(session, access.user)


def require_analytics_token(authorization: str = Header("")) -> None:
    """Require the analytics service's shared token.

    Args:
        authorization: The Authorization header.

    Raises:
        Unavailable: No token is configured, so the internal endpoints are off.
        Unauthorized: The header does not carry the token.
    """
    if not settings.analytics_token:
        raise Unavailable("analytics_disabled", "SSS_ANALYTICS_TOKEN is not set")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {settings.analytics_token}".encode()):
        raise Unauthorized("invalid_token", "The analytics token is missing or wrong")


def get_ingest_service(authorized: None = Depends(require_analytics_token)) -> AnalyticsIngestService:
    """Build the intake service for a request of an engine that posts.

    Args:
        authorized: Result of the token check.

    Returns:
        The service.
    """
    return AnalyticsIngestService()


def get_follow_service(
    session: AsyncSession = Depends(get_db),
    authorized: None = Depends(require_analytics_token),
) -> FollowPtzService:
    """Build the follow PTZ service for a command of the analytics service.

    Args:
        session: Request-scoped database session.
        authorized: Result of the token check.

    Returns:
        The service.
    """
    return FollowPtzService(session)


def get_config_service(
    session: AsyncSession = Depends(get_db),
    authorized: None = Depends(require_analytics_token),
) -> AnalyticsConfigService:
    """Build the configuration service for a poll of the analytics service.

    Args:
        session: Request-scoped database session.
        authorized: Result of the token check.

    Returns:
        The service.
    """
    return AnalyticsConfigService(session)


def get_status_service(
    session: AsyncSession = Depends(get_db),
    access: SiteAccess = Depends(get_site_access),
) -> AnalyticsStatusService:
    """Build the status service for a member of the request's site.

    Args:
        session: Request-scoped database session, limited to the site by the access.
        access: Access of the signed-in member to the request's site.

    Returns:
        The service.
    """
    return AnalyticsStatusService(session)
