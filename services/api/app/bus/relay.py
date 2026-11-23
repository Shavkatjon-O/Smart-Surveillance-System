"""The live consumer of every API replica: the bus's live fan-out and observations into this process's memory."""

import logging

from app.analytics.observations import Observations, observations
from app.bus.connection import BusConnection, bus
from app.bus.messages import LIVE_KINDS
from app.camera_events.broker import EventBroker
from event_bus import BusError, Envelope, EventType, Listening, subjects
from event_bus.streams import OBSERVATIONS

__all__ = ["LiveRelay"]

logger = logging.getLogger(__name__)


class LiveRelay:
    """Passes every live stream message on the bus to this replica's event broker, and keeps the observations.

    Every replica runs one, so a server-sent event client sees every event whichever replica it is connected to.
    After the connection to the bus comes back, the relay tells its clients to reconnect, because messages sent
    while it was away are lost to them, and reconnecting hands them the current state.

    Attributes:
        connection: The bus connection.
        broker: The broker of this replica's live event streams.
        reported: The observations of this replica.
        live: The live subscription, once started.
        watch: The observation subscription, once started.
    """

    def __init__(self, broker: EventBroker, connection: BusConnection = bus, reported: Observations = observations):
        """Describe the relay.

        Args:
            broker: The broker of this replica's live event streams.
            connection: The bus connection.
            reported: The observations of this replica.
        """
        self.connection = connection
        self.broker = broker
        self.reported = reported
        self.live: Listening | None = None
        self.watch: Listening | None = None

    async def start(self) -> None:
        """Subscribe to the live fan-out and the observations."""
        current = self.connection.current()
        self.live = await current.listen(subjects.LIVE, self.on_live)
        await self.follow()
        current.on_reconnect(self.reconnected)

    async def follow(self) -> None:
        """Read the latest observation of every view and then every new one."""
        if self.watch is not None:
            await self.watch.stop()
            self.watch = None
        try:
            self.watch = await self.connection.current().follow(
                OBSERVATIONS, subjects.OBSERVATIONS, self.on_observation
            )
        except BusError as exc:
            logger.warning("Following the observations failed: %s", exc)

    async def stop(self) -> None:
        """End both subscriptions."""
        for listening in (self.live, self.watch):
            if listening is not None:
                await listening.stop()
        self.live = None
        self.watch = None

    async def on_live(self, envelope: Envelope) -> None:
        """Pass a live stream message to the broker.

        Args:
            envelope: The message.
        """
        kind = LIVE_KINDS.get(envelope.type)
        if kind is not None:
            self.broker.publish(kind, envelope.data)

    async def on_observation(self, envelope: Envelope) -> None:
        """Keep the latest status of a view.

        Args:
            envelope: The observation.
        """
        if envelope.type == EventType.OBSERVATION:
            self.reported.store_view(envelope.data)

    async def reconnected(self) -> None:
        """Tell the live clients to reconnect and follow the observations again."""
        self.broker.reset()
        await self.follow()
