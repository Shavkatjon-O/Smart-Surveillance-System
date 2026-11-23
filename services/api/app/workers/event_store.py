"""The event-store worker: every event change on the bus becomes a stored event, announced and alerted once."""

from app.bus.connection import BusConnection, bus
from app.bus.messages import alert_envelope, live_envelope
from app.bus.owners import OwnerCache, owners
from app.core.database import async_session_maker
from app.modules.events.services import EventStoreService, InvalidChange
from event_bus import Delivery, Envelope, Reject, subjects
from event_bus.streams import EVENT_STORE

__all__ = ["SOURCE", "EventStoreWorker"]

SOURCE = "/sss/event-store"


class EventStoreWorker:
    """Stores each change, sends the stored event to every API replica and raises its alert.

    An alert goes out when the change turned the event's alert on, and again on every later delivery of a change
    of an alerting event, because the alert of a change that failed after it was stored would otherwise be lost;
    the alert's id derives from the event, so the alert stream keeps one.

    Attributes:
        connection: The bus connection.
        sessions: Opens database sessions.
        cache: Organisation and site ids of sites.
        spec: The durable consumer.
    """

    def __init__(self, connection: BusConnection = bus, sessions=async_session_maker, cache: OwnerCache = owners):
        """Describe the worker.

        Args:
            connection: The bus connection.
            sessions: Opens database sessions.
            cache: Organisation and site ids of sites.
        """
        self.connection = connection
        self.sessions = sessions
        self.cache = cache
        self.spec = EVENT_STORE

    async def handle(self, envelope: Envelope, delivery: Delivery) -> None:
        """Store one change, then announce and alert.

        Args:
            envelope: The change.
            delivery: Its delivery, for the attempt number.

        Raises:
            Reject: The change can never be stored.
        """
        async with self.sessions() as session:
            try:
                stored = await EventStoreService(session).apply(envelope)
            except InvalidChange as exc:
                raise Reject(str(exc))
            if stored is None:
                return
            organisation, site = await self.cache.of_site(session, stored.site_id)
        data = stored.event.model_dump(mode="json")
        current = self.connection.current()
        await current.broadcast(
            subjects.live(organisation, site), live_envelope("event", data, organisation, site, SOURCE)
        )
        if stored.event.alert and (stored.raised or delivery.attempt > 1):
            await current.publish(subjects.alert(organisation), alert_envelope(data, organisation, site, SOURCE))
