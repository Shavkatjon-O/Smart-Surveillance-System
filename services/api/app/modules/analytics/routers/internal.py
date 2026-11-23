"""Internal routes the analytics service calls with the shared token; nginx never proxies them."""

from fastapi import APIRouter, Depends, Response, status

from app.modules.analytics.deps import get_config_service, get_ingest_service
from app.modules.analytics.schemas import AnalyticsEventIn, ObservationReport
from app.modules.analytics.services import AnalyticsConfigService, AnalyticsIngestService

router = APIRouter()


@router.get("/config")
async def read_config(service: AnalyticsConfigService = Depends(get_config_service)) -> dict:
    """Return the configuration the analytics service runs on.

    Args:
        service: Configuration service.

    Returns:
        The arm mode and every enabled camera with its views, zones and lines.
    """
    return await service.build()


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
async def ingest_event(
    data: AnalyticsEventIn, service: AnalyticsIngestService = Depends(get_ingest_service)
) -> Response:
    """Publish one event change on the event bus, for an engine that posts instead of publishing itself.

    Args:
        data: The event change.
        service: Intake service.

    Returns:
        Empty response.
    """
    await service.event(data)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/observations", status_code=status.HTTP_204_NO_CONTENT)
async def ingest_observations(
    report: ObservationReport,
    service: AnalyticsIngestService = Depends(get_ingest_service),
) -> Response:
    """Publish the latest observation of every view on the event bus.

    Args:
        report: The report.
        service: Intake service.

    Returns:
        Empty response.
    """
    await service.observe(report)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
