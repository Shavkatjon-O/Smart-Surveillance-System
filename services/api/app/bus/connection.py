"""One bus connection per process, with a buffered publisher for engines and direct publishing for routes."""

import logging

from app.core.config import APP_NAME, settings
from app.core.exceptions import Unavailable
from event_bus import Bus, BusError, Envelope, PublishAck, Publisher, provision

__all__ = ["BusConnection", "bus"]

logger = logging.getLogger(__name__)

CONNECT_SECONDS = 60.0


def nats_bus(name: str) -> Bus:
    """Build the NATS bus of the settings.

    Args:
        name: Connection name shown by the server.

    Returns:
        The bus, not yet connected.
    """
    from event_bus.jetstream import NatsBus

    return NatsBus(settings.nats_url, name=name, connect_wait=CONNECT_SECONDS)


class BusConnection:
    """The process's bus, its publisher and whether it has started.

    Attributes:
        name: Connection name shown by the server.
        bus: The bus, built on first use.
        publisher: Publisher with retry and the local buffer, for engines that must not lose a message.
        started: Whether start has run.
    """

    def __init__(self, name: str = APP_NAME):
        """Describe the connection.

        Args:
            name: Connection name shown by the server.
        """
        self.name = name
        self.bus: Bus | None = None
        self.publisher: Publisher | None = None
        self.started = False

    def use(self, bus: Bus) -> None:
        """Use a given bus, such as the in-memory one in tests, instead of NATS.

        Args:
            bus: The bus.
        """
        self.bus = bus
        self.publisher = Publisher(bus)
        self.started = False

    def current(self) -> Bus:
        """Return the bus, building the NATS one on first use.

        Returns:
            The bus.
        """
        if self.bus is None:
            self.use(nats_bus(self.name))
        return self.bus

    async def start(self, provisioned: bool = True) -> None:
        """Connect, provision the streams and consumers, and start the publisher's buffer.

        Args:
            provisioned: Whether to create or update the streams and consumers.

        Raises:
            BusUnavailable: The bus could not be reached in time.
        """
        bus = self.current()
        await bus.connect()
        if provisioned:
            await provision(bus)
            bus.on_reconnect(self.reprovision)
        await self.publisher.start()
        self.started = True

    async def reprovision(self) -> None:
        """Provision again after a reconnect, because a restarted server has lost its memory streams."""
        try:
            await provision(self.current())
        except BusError as exc:
            logger.warning("Provisioning after a reconnect failed: %s", exc)

    async def stop(self) -> None:
        """Send what is buffered and disconnect."""
        if self.bus is None:
            return
        if self.publisher is not None:
            await self.publisher.stop()
        await self.bus.close()
        self.started = False

    async def publish(self, subject: str, envelope: Envelope) -> PublishAck:
        """Publish now and wait for the acknowledgement, for a route that must tell its caller about a failure.

        Args:
            subject: The subject.
            envelope: The message.

        Returns:
            The acknowledgement.

        Raises:
            Unavailable: The bus did not take the message.
        """
        try:
            return await self.current().publish(subject, envelope)
        except BusError as exc:
            logger.warning("Publishing on %s failed: %s", subject, exc)
            raise Unavailable("bus_unavailable", "The event bus does not answer")

    async def emit(self, subject: str, envelope: Envelope, droppable: bool = False) -> bool:
        """Publish with retry, keeping the message in the local buffer while the bus is away.

        Args:
            subject: The subject.
            envelope: The message.
            droppable: True for a message the next one replaces.

        Returns:
            True when the bus acknowledged it now.
        """
        self.current()
        return await self.publisher.publish(subject, envelope, droppable)

    async def broadcast(self, subject: str, envelope: Envelope) -> None:
        """Send a message to the current listeners, logging rather than raising when the bus is away.

        Args:
            subject: The subject.
            envelope: The message.
        """
        try:
            await self.current().broadcast(subject, envelope)
        except BusError as exc:
            logger.warning("Sending on %s failed: %s", subject, exc)


bus = BusConnection()
