"""The bus interface producers and consumers use, so a Kafka adapter can replace NATS without touching either."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from event_bus.envelope import Envelope
from event_bus.streams import ConsumerSpec, StreamSpec

__all__ = ["Bus", "BusError", "BusUnavailable", "Delivery", "Handler", "Listening", "PublishAck"]


class BusError(Exception):
    """The bus refused or failed an operation."""


class BusUnavailable(BusError):
    """The bus cannot be reached right now; the operation may succeed later."""


@dataclass(frozen=True)
class PublishAck:
    """The stream's acknowledgement of a published message.

    Attributes:
        stream: Stream that stored it.
        sequence: Its sequence in that stream.
        duplicate: True when the stream already held a message with the same id and dropped this one.
    """

    stream: str
    sequence: int
    duplicate: bool = False


class Delivery(ABC):
    """One message handed to a durable consumer, which must acknowledge, delay or end it.

    Attributes:
        subject: Subject the message was published on.
        attempt: How many times it has been delivered, from 1.
        payload: The raw message.
    """

    subject: str
    attempt: int
    payload: bytes

    @property
    def envelope(self) -> Envelope:
        """Envelope: The decoded envelope; raises EnvelopeError when the payload is not one."""
        return Envelope.from_bytes(self.payload)

    @abstractmethod
    async def ack(self) -> None:
        """Confirm the message, so it is never delivered to this consumer again."""

    @abstractmethod
    async def nak(self, delay: float) -> None:
        """Hand the message back to be delivered again later.

        Args:
            delay: Seconds before the next delivery.
        """

    @abstractmethod
    async def term(self) -> None:
        """End the message without success, so it is never delivered to this consumer again."""

    @abstractmethod
    async def working(self) -> None:
        """Tell the server the message is still being worked on, which restarts its acknowledgement wait."""


Handler = Callable[[Envelope], Awaitable[None]]


class Listening(ABC):
    """A live subscription that can be ended."""

    @abstractmethod
    async def stop(self) -> None:
        """End the subscription."""


class Bus(ABC):
    """A message bus with durable streams, durable pull consumers and plain fan-out.

    Attributes:
        name: Name of the process on the bus, for its connection.
    """

    name: str = "sss"

    @property
    @abstractmethod
    def connected(self) -> bool:
        """bool: Whether the bus is connected right now."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect, waiting until the server answers.

        Raises:
            BusUnavailable: The server could not be reached in time.
        """

    @abstractmethod
    async def close(self) -> None:
        """Flush what is pending and disconnect."""

    @abstractmethod
    def on_reconnect(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Run a callback whenever the connection comes back after a loss.

        Args:
            callback: Coroutine function to run.
        """

    @abstractmethod
    async def ensure_stream(self, spec: StreamSpec) -> None:
        """Create a stream or bring an existing one in line with its specification.

        Args:
            spec: The stream.
        """

    @abstractmethod
    async def ensure_consumer(self, spec: ConsumerSpec) -> None:
        """Create a durable consumer or bring an existing one in line with its specification.

        Args:
            spec: The consumer.
        """

    @abstractmethod
    async def publish(self, subject: str, envelope: Envelope, timeout: float = 2.0) -> PublishAck:
        """Store a message in the stream that captures its subject and wait for the acknowledgement.

        The envelope id is the message id, so publishing the same envelope again is dropped by the stream.

        Args:
            subject: The subject.
            envelope: The message.
            timeout: Seconds to wait for the acknowledgement.

        Returns:
            The acknowledgement.

        Raises:
            BusUnavailable: The bus could not be reached or did not answer in time.
            BusError: No stream captures the subject or the stream refused the message.
        """

    @abstractmethod
    async def broadcast(self, subject: str, envelope: Envelope) -> None:
        """Send a message to every current listener of its subject without storing it.

        Args:
            subject: The subject.
            envelope: The message.

        Raises:
            BusUnavailable: The bus could not be reached.
        """

    @abstractmethod
    async def fetch(self, spec: ConsumerSpec, batch: int, timeout: float) -> list[Delivery]:
        """Take up to a batch of messages from a durable consumer, waiting up to a timeout for the first.

        Args:
            spec: The consumer.
            batch: Most messages to take.
            timeout: Seconds to wait.

        Returns:
            The deliveries, empty when none arrived in time or the bus is away.
        """

    @abstractmethod
    async def listen(self, subject: str, handler: Handler) -> Listening:
        """Receive every message sent or published on matching subjects from now on, without durability.

        Args:
            subject: Subject pattern.
            handler: Called with each envelope; messages that are not envelopes are skipped.

        Returns:
            The subscription.
        """

    @abstractmethod
    async def follow(self, stream: StreamSpec, subject: str, handler: Handler) -> Listening:
        """Receive the last stored message of every matching subject, then every new one, without durability.

        Args:
            stream: Stream that keeps the messages.
            subject: Subject pattern within it.
            handler: Called with each envelope.

        Returns:
            The subscription.
        """
