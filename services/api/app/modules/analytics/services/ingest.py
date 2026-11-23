"""The intake of engines that post: their event changes and observations go onto the bus unchanged.

The analytics engine publishes on the bus itself. An engine that cannot, such as a later edge engine on a site
without a bus, posts to the internal routes instead, and this service publishes what it posts, so there is exactly
one way into the event pipeline: the bus, which the event store reads.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.bus.connection import BusConnection, bus
from app.bus.owners import owners
from app.modules.analytics.schemas import AnalyticsEventIn, ObservationReport
from event_bus import Envelope, EventType, subjects

__all__ = ["INTAKE_SOURCE", "AnalyticsIngestService"]

INTAKE_SOURCE = "/sss/api/intake"


class AnalyticsIngestService:
    """Publishes what an engine posts.

    Attributes:
        connection: The bus connection.
        owner_of: Returns the organisation and site ids of a camera.
    """

    def __init__(
        self,
        connection: BusConnection = bus,
        owner_of: Callable[[UUID], Awaitable[tuple[str | None, str | None]]] = owners.of_camera,
    ):
        """Bind the service to the bus.

        Args:
            connection: The bus connection.
            owner_of: Returns the organisation and site ids of a camera.
        """
        self.connection = connection
        self.owner_of = owner_of

    async def event(self, data: AnalyticsEventIn) -> None:
        """Publish one event change and wait until the bus has stored it.

        Args:
            data: The event change.

        Raises:
            Unavailable: The bus did not take it, so the engine retries.
        """
        organisation, site = await self.owner_of(data.camera)
        envelope = Envelope.create(
            EventType.ANALYTICS_EVENT,
            INTAKE_SOURCE,
            data.model_dump(mode="json"),
            subject=data.id,
            organisation=organisation,
            site=site,
        )
        await self.connection.publish(subjects.analytics_event(organisation, site, data.view), envelope)

    async def observe(self, report: ObservationReport) -> None:
        """Publish the observation of every view in a report.

        Args:
            report: The report.

        Raises:
            Unavailable: The bus did not take one of them.
        """
        for view in report.views:
            camera = view.get("camera")
            organisation, site = await self.owner_of(camera) if camera else (None, None)
            envelope = Envelope.create(
                EventType.OBSERVATION,
                INTAKE_SOURCE,
                {"at": report.at, "pipeline": report.pipeline, "gpu": report.gpu, "view": view},
                subject=view.get("id"),
                organisation=organisation,
                site=site,
            )
            await self.connection.publish(subjects.analytics_observation(organisation, site, view.get("id")), envelope)
