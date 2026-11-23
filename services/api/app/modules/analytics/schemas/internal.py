"""Schemas of the internal endpoints the analytics service calls."""

from pydantic import BaseModel, Field

from app.modules.events.schemas.changes import AnalyticsEventChange

__all__ = ["AnalyticsEventIn", "ObservationReport"]


class AnalyticsEventIn(AnalyticsEventChange):
    """An event change an engine posts to the intake route, which publishes it on the bus unchanged.

    The fields are those of AnalyticsEventChange, the data of sss.analytics.event.v1.
    """


class ObservationReport(BaseModel):
    """Periodic report of the analytics service.

    Attributes:
        at: Time of the report, seconds since the epoch.
        pipeline: Pipeline state.
        gpu: GPU reading.
        views: Status of every view.
    """

    at: float
    pipeline: dict | None = None
    gpu: dict = Field(default_factory=dict)
    views: list[dict] = Field(default_factory=list)
