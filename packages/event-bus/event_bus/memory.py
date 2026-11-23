"""An in-memory bus with the same semantics as the JetStream one, for tests and for running without a server."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from event_bus.bus import Bus, BusError, BusUnavailable, Delivery, Handler, Listening, PublishAck
from event_bus.envelope import Envelope
from event_bus.streams import ConsumerSpec, StreamSpec
from event_bus.subjects import matches

__all__ = ["MemoryBus"]

logger = logging.getLogger(__name__)


@dataclass
class StoredMessage:
    """A message kept in a stream.

    Attributes:
        sequence: Its sequence in the stream.
        subject: Subject it was published on.
        payload: The raw message.
        stored_at: Monotonic time it was stored.
    """

    sequence: int
    subject: str
    payload: bytes
    stored_at: float


@dataclass
class StoredStream:
    """A stream and its messages.

    Attributes:
        spec: The stream.
        messages: Messages by sequence.
        ids: Message ids within the duplicate window, with the monotonic time they were stored.
        last: Next sequence minus one.
    """

    spec: StreamSpec
    messages: dict[int, StoredMessage] = field(default_factory=dict)
    ids: dict[str, float] = field(default_factory=dict)
    last: int = 0


@dataclass
class Pending:
    """Delivery state of one message of a consumer.

    Attributes:
        deliveries: How many times it has been delivered.
        due: Monotonic time from which it may be delivered again.
        done: Whether it was acknowledged or ended.
    """

    deliveries: int = 0
    due: float = 0.0
    done: bool = False


@dataclass
class StoredConsumer:
    """A durable consumer and the delivery state of its messages.

    Attributes:
        spec: The consumer.
        state: Delivery state by stream sequence.
        terminated: Sequences ended without success.
    """

    spec: ConsumerSpec
    state: dict[int, Pending] = field(default_factory=dict)
    terminated: list[int] = field(default_factory=list)


class MemoryDelivery(Delivery):
    """A message handed out by the in-memory bus.

    Attributes:
        bus: The bus.
        consumer: The consumer it was handed to.
        sequence: Its stream sequence.
        subject: Subject it was published on.
        attempt: How many times it has been delivered.
        payload: The raw message.
    """

    def __init__(self, bus: MemoryBus, consumer: StoredConsumer, message: StoredMessage, attempt: int):
        """Wrap a stored message.

        Args:
            bus: The bus.
            consumer: The consumer.
            message: The message.
            attempt: The delivery count.
        """
        self.bus = bus
        self.consumer = consumer
        self.sequence = message.sequence
        self.subject = message.subject
        self.payload = message.payload
        self.attempt = attempt

    async def ack(self) -> None:
        """Confirm the message."""
        self.consumer.state[self.sequence].done = True
        self.bus.wake()

    async def nak(self, delay: float) -> None:
        """Deliver the message again after a delay.

        Args:
            delay: Seconds before the next delivery.
        """
        self.consumer.state[self.sequence].due = self.bus.clock() + delay
        self.bus.wake()

    async def term(self) -> None:
        """End the message without success."""
        self.consumer.state[self.sequence].done = True
        self.consumer.terminated.append(self.sequence)
        self.bus.wake()

    async def working(self) -> None:
        """Restart the acknowledgement wait."""
        self.consumer.state[self.sequence].due = self.bus.clock() + self.consumer.spec.ack_wait


class MemoryListening(Listening):
    """A listener registered on the in-memory bus.

    Attributes:
        bus: The bus.
        entry: The registration.
    """

    def __init__(self, bus: MemoryBus, entry: tuple[str, Handler]):
        """Wrap a registration.

        Args:
            bus: The bus.
            entry: Pattern and handler.
        """
        self.bus = bus
        self.entry = entry

    async def stop(self) -> None:
        """Remove the listener."""
        if self.entry in self.bus.listeners:
            self.bus.listeners.remove(self.entry)


class MemoryBus(Bus):
    """Streams, durable consumers and listeners in memory, with message ids, redelivery and delivery limits.

    The bus can be taken offline, so a test can show a producer surviving an outage.

    Attributes:
        name: Name of the process on the bus.
        streams: Streams by name.
        consumers: Consumers by durable name.
        listeners: Patterns and handlers of plain listeners.
        online: Whether the bus answers.
        is_connected: Whether connect was called and close was not.
        clock: Returns the monotonic time, replaced in tests.
        reconnects: Callbacks run when the bus comes back online.
        changed: Set when a message arrives or a delivery state changes.
        published: Every message published or broadcast, as subject and envelope, for assertions.
    """

    def __init__(self, name: str = "memory", clock: Callable[[], float] = time.monotonic):
        """Create an empty bus.

        Args:
            name: Name of the process on the bus.
            clock: Returns the monotonic time.
        """
        self.name = name
        self.streams: dict[str, StoredStream] = {}
        self.consumers: dict[str, StoredConsumer] = {}
        self.listeners: list[tuple[str, Handler]] = []
        self.online = True
        self.is_connected = False
        self.clock = clock
        self.reconnects: list[Callable[[], Awaitable[None]]] = []
        self.changed = asyncio.Event()
        self.published: list[tuple[str, Envelope]] = []

    @property
    def connected(self) -> bool:
        """bool: Whether the bus is connected and online."""
        return self.is_connected and self.online

    def wake(self) -> None:
        """Wake fetches that wait for a message."""
        self.changed.set()

    def check(self) -> None:
        """Fail when the bus is offline.

        Raises:
            BusUnavailable: The bus is offline.
        """
        if not self.online:
            raise BusUnavailable("The in-memory bus is offline")

    async def set_online(self, online: bool) -> None:
        """Take the bus offline or bring it back, running the reconnect callbacks when it returns.

        Args:
            online: Whether the bus answers.
        """
        returning = online and not self.online
        self.online = online
        if returning:
            for callback in list(self.reconnects):
                await callback()
            self.wake()

    async def connect(self) -> None:
        """Connect.

        Raises:
            BusUnavailable: The bus is offline.
        """
        self.check()
        self.is_connected = True

    async def close(self) -> None:
        """Disconnect."""
        self.is_connected = False

    def on_reconnect(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Run a callback when the bus comes back online.

        Args:
            callback: Coroutine function to run.
        """
        self.reconnects.append(callback)

    async def ensure_stream(self, spec: StreamSpec) -> None:
        """Create a stream or replace its specification.

        Args:
            spec: The stream.

        Raises:
            BusError: Its subjects overlap another stream's.
        """
        self.check()
        for other in self.streams.values():
            if other.spec.name == spec.name:
                continue
            for mine in spec.subjects:
                for theirs in other.spec.subjects:
                    if overlap(mine, theirs):
                        raise BusError(f"Stream {spec.name} overlaps {other.spec.name} on {mine}")
        stored = self.streams.get(spec.name)
        if stored is None:
            self.streams[spec.name] = StoredStream(spec=spec)
        else:
            stored.spec = spec

    async def ensure_consumer(self, spec: ConsumerSpec) -> None:
        """Create a durable consumer or replace its specification.

        Args:
            spec: The consumer.

        Raises:
            BusError: Its stream does not exist.
        """
        self.check()
        if spec.stream not in self.streams:
            raise BusError(f"Stream {spec.stream} does not exist")
        stored = self.consumers.get(spec.durable)
        if stored is None:
            self.consumers[spec.durable] = StoredConsumer(spec=spec)
        else:
            stored.spec = spec

    def stream_for(self, subject: str) -> StoredStream | None:
        """Find the stream that captures a subject.

        Args:
            subject: The subject.

        Returns:
            The stream, or None.
        """
        for stored in self.streams.values():
            if any(matches(pattern, subject) for pattern in stored.spec.subjects):
                return stored
        return None

    def expire(self, stored: StoredStream) -> None:
        """Drop messages past the stream's age and the ids past its duplicate window.

        Args:
            stored: The stream.
        """
        now = self.clock()
        for sequence, message in list(stored.messages.items()):
            if now - message.stored_at > stored.spec.max_age:
                del stored.messages[sequence]
        for message_id, at in list(stored.ids.items()):
            if now - at > stored.spec.duplicate_window:
                del stored.ids[message_id]

    async def publish(self, subject: str, envelope: Envelope, timeout: float = 2.0) -> PublishAck:
        """Store a message, dropping one whose id the stream saw within its duplicate window.

        Args:
            subject: The subject.
            envelope: The message.
            timeout: Ignored.

        Returns:
            The acknowledgement.

        Raises:
            BusUnavailable: The bus is offline.
            BusError: No stream captures the subject.
        """
        self.check()
        stored = self.stream_for(subject)
        if stored is None:
            raise BusError(f"No stream captures {subject}")
        self.expire(stored)
        if envelope.id in stored.ids:
            return PublishAck(stream=stored.spec.name, sequence=0, duplicate=True)
        stored.last += 1
        now = self.clock()
        stored.messages[stored.last] = StoredMessage(stored.last, subject, envelope.to_bytes(), now)
        stored.ids[envelope.id] = now
        if stored.spec.max_msgs_per_subject > 0:
            same = [sequence for sequence, message in stored.messages.items() if message.subject == subject]
            for sequence in same[: -stored.spec.max_msgs_per_subject]:
                del stored.messages[sequence]
        self.published.append((subject, envelope))
        await self.notify(subject, envelope)
        self.wake()
        return PublishAck(stream=stored.spec.name, sequence=stored.last)

    async def notify(self, subject: str, envelope: Envelope) -> None:
        """Hand a message to the matching listeners.

        Args:
            subject: The subject.
            envelope: The message.
        """
        for pattern, handler in list(self.listeners):
            if matches(pattern, subject):
                try:
                    await handler(envelope)
                except Exception:
                    logger.exception("A listener of %s failed", subject)

    async def broadcast(self, subject: str, envelope: Envelope) -> None:
        """Hand a message to the current listeners without storing it.

        Args:
            subject: The subject.
            envelope: The message.

        Raises:
            BusUnavailable: The bus is offline.
        """
        self.check()
        self.published.append((subject, envelope))
        await self.notify(subject, envelope)

    def ready(self, consumer: StoredConsumer, batch: int) -> list[MemoryDelivery]:
        """Hand out the messages a consumer may receive now, in stream order.

        Args:
            consumer: The consumer.
            batch: Most messages to hand out.

        Returns:
            The deliveries.
        """
        stored = self.streams[consumer.spec.stream]
        self.expire(stored)
        now = self.clock()
        handed = []
        for sequence in sorted(stored.messages):
            if len(handed) >= batch:
                break
            message = stored.messages[sequence]
            if not any(matches(pattern, message.subject) for pattern in consumer.spec.subjects):
                continue
            pending = consumer.state.setdefault(sequence, Pending())
            if pending.done or pending.due > now or pending.deliveries >= consumer.spec.max_deliver:
                continue
            pending.deliveries += 1
            pending.due = now + consumer.spec.ack_wait
            handed.append(MemoryDelivery(self, consumer, message, pending.deliveries))
        return handed

    async def fetch(self, spec: ConsumerSpec, batch: int, timeout: float) -> list[Delivery]:
        """Take up to a batch of messages, waiting up to a timeout for the first.

        Args:
            spec: The consumer.
            batch: Most messages to take.
            timeout: Seconds to wait.

        Returns:
            The deliveries, empty when none is ready in time or the bus is offline.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if self.online and spec.durable in self.consumers:
                handed = self.ready(self.consumers[spec.durable], batch)
                if handed:
                    return handed
            remaining = deadline - loop.time()
            if remaining <= 0:
                return []
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), min(remaining, 0.05))
            except TimeoutError:
                pass

    async def listen(self, subject: str, handler: Handler) -> Listening:
        """Register a listener.

        Args:
            subject: Subject pattern.
            handler: Called with each envelope.

        Returns:
            The registration.
        """
        entry = (subject, handler)
        self.listeners.append(entry)
        return MemoryListening(self, entry)

    async def follow(self, stream: StreamSpec, subject: str, handler: Handler) -> Listening:
        """Hand over the last stored message of every matching subject, then listen for new ones.

        Args:
            stream: Stream that keeps the messages.
            subject: Subject pattern within it.
            handler: Called with each envelope.

        Returns:
            The registration.
        """
        stored = self.streams.get(stream.name)
        if stored is not None:
            self.expire(stored)
            last: dict[str, StoredMessage] = {}
            for sequence in sorted(stored.messages):
                message = stored.messages[sequence]
                if matches(subject, message.subject):
                    last[message.subject] = message
            for message in last.values():
                await handler(Envelope.from_bytes(message.payload))
        return await self.listen(subject, handler)

    def pending(self, durable: str) -> int:
        """Count the messages a consumer has not acknowledged or ended yet.

        Args:
            durable: The consumer.

        Returns:
            The count.
        """
        consumer = self.consumers[durable]
        stored = self.streams[consumer.spec.stream]
        return sum(
            1
            for sequence, message in stored.messages.items()
            if any(matches(pattern, message.subject) for pattern in consumer.spec.subjects)
            and not consumer.state.get(sequence, Pending()).done
        )

    def stored(self, stream: str) -> list[Envelope]:
        """List the envelopes a stream holds, in order.

        Args:
            stream: Stream name.

        Returns:
            The envelopes.
        """
        messages = self.streams[stream].messages
        return [Envelope.from_bytes(messages[sequence].payload) for sequence in sorted(messages)]


def overlap(first: str, second: str) -> bool:
    """Tell whether two subject patterns can match a common subject.

    Args:
        first: A pattern.
        second: Another pattern.

    Returns:
        True when some subject matches both.
    """
    left, right = first.split("."), second.split(".")
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a == ">" or b == ">":
            return a is not None and b is not None
        if a is None or b is None:
            return False
        if a != b and a != "*" and b != "*":
            return False
    return True
