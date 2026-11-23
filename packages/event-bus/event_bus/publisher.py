"""Producers' side of the bus: publish with acknowledgement and bounded retry, and a small local buffer.

A producer hands every message to a Publisher. The publisher tries a few times; when the bus stays away it keeps
the message in a bounded local buffer in order and a background task sends the buffer once the bus answers again,
so a producer survives a short NATS outage without losing events. Messages marked droppable, such as observations
that the next report replaces, are never buffered.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from event_bus.bus import Bus, BusError, BusUnavailable
from event_bus.envelope import Envelope

__all__ = ["Publisher", "PublisherStats", "ThreadedPublisher"]

logger = logging.getLogger(__name__)

BUFFER_LIMIT = 2000
ATTEMPTS = 3
ATTEMPT_DELAYS = (0.2, 0.5)
FLUSH_DELAYS = (0.5, 1.0, 2.0, 5.0, 10.0)
TIMEOUT_SECONDS = 2.0


@dataclass
class PublisherStats:
    """Counters of a publisher, for status pages and logs.

    Attributes:
        published: Messages the bus acknowledged.
        duplicates: Messages the bus already held.
        buffered: Messages waiting in the local buffer now.
        dropped: Messages dropped because the buffer was full or they were droppable.
        rejected: Messages the bus refused for good, such as a subject no stream captures.
        failures: Failed attempts in a row.
        last_error: The latest error, with its time.
    """

    published: int = 0
    duplicates: int = 0
    buffered: int = 0
    dropped: int = 0
    rejected: int = 0
    failures: int = 0
    last_error: str | None = None

    def as_dict(self) -> dict:
        """Return the counters.

        Returns:
            The counters by name.
        """
        return dict(self.__dict__)


class Publisher:
    """Publishes on a bus with acknowledgement, bounded retry and a local buffer that keeps order.

    Attributes:
        bus: The bus.
        limit: Most messages the buffer holds; the oldest is dropped to make room.
        attempts: Tries per message before it goes to the buffer.
        timeout: Seconds to wait for each acknowledgement.
        buffer: Messages waiting for the bus, oldest first, as subject and envelope.
        stats: Counters.
        task: The background flush, if started.
        wakeup: Set when the buffer receives a message.
    """

    def __init__(self, bus: Bus, limit: int = BUFFER_LIMIT, attempts: int = ATTEMPTS, timeout: float = TIMEOUT_SECONDS):
        """Create a publisher; call start to flush the buffer in the background.

        Args:
            bus: The bus.
            limit: Most messages the buffer holds.
            attempts: Tries per message before it goes to the buffer.
            timeout: Seconds to wait for each acknowledgement.
        """
        self.bus = bus
        self.limit = limit
        self.attempts = attempts
        self.timeout = timeout
        self.buffer: deque[tuple[str, Envelope]] = deque()
        self.stats = PublisherStats()
        self.task: asyncio.Task | None = None
        self.wakeup = asyncio.Event()

    async def start(self) -> None:
        """Start flushing the buffer in the background."""
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="bus-publisher")

    async def stop(self) -> None:
        """Try once more to send the buffer, then stop."""
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        try:
            await self.flush()
        except BusError:
            pass
        if self.buffer:
            logger.warning("Stopping with %d messages still buffered", len(self.buffer))

    def keep(self, subject: str, envelope: Envelope) -> None:
        """Put a message in the buffer, dropping the oldest when it is full.

        Args:
            subject: The subject.
            envelope: The message.
        """
        if len(self.buffer) >= self.limit:
            dropped_subject, _ = self.buffer.popleft()
            self.stats.dropped += 1
            logger.warning("The bus buffer is full, dropping the oldest message on %s", dropped_subject)
        self.buffer.append((subject, envelope))
        self.stats.buffered = len(self.buffer)
        self.wakeup.set()

    def failed(self, error: Exception) -> None:
        """Record a failed attempt.

        Args:
            error: The error.
        """
        self.stats.failures += 1
        self.stats.last_error = f"{time.strftime('%H:%M:%S')} {type(error).__name__}: {error}"

    async def send(self, subject: str, envelope: Envelope) -> None:
        """Publish one message with bounded retry.

        Args:
            subject: The subject.
            envelope: The message.

        Raises:
            BusUnavailable: Every attempt failed because the bus was away.
            BusError: The bus refused the message for good.
        """
        for attempt in range(self.attempts):
            try:
                ack = await self.bus.publish(subject, envelope, self.timeout)
            except BusUnavailable as exc:
                self.failed(exc)
                if attempt + 1 >= self.attempts:
                    raise
                await asyncio.sleep(ATTEMPT_DELAYS[min(attempt, len(ATTEMPT_DELAYS) - 1)])
                continue
            self.stats.failures = 0
            if ack.duplicate:
                self.stats.duplicates += 1
            else:
                self.stats.published += 1
            return

    async def publish(self, subject: str, envelope: Envelope, droppable: bool = False) -> bool:
        """Publish a message now, or keep it for later when the bus is away.

        While older messages wait in the buffer, a new one joins them, so the stream receives every producer's
        messages in order.

        Args:
            subject: The subject.
            envelope: The message.
            droppable: True for a message the next one replaces, which is dropped rather than buffered.

        Returns:
            True when the bus acknowledged it now, False when it was buffered, dropped or refused.
        """
        if self.buffer:
            if droppable:
                self.stats.dropped += 1
                return False
            self.keep(subject, envelope)
            return False
        try:
            await self.send(subject, envelope)
        except BusUnavailable:
            if droppable:
                self.stats.dropped += 1
                return False
            self.keep(subject, envelope)
            return False
        except BusError as exc:
            self.failed(exc)
            self.stats.rejected += 1
            logger.warning("The bus refused a message on %s: %s", subject, exc)
            return False
        return True

    async def flush(self) -> int:
        """Send the buffer in order until it is empty or the bus fails.

        Returns:
            Messages sent.

        Raises:
            BusUnavailable: The bus went away again.
        """
        sent = 0
        while self.buffer:
            subject, envelope = self.buffer[0]
            try:
                await self.send(subject, envelope)
            except BusUnavailable:
                raise
            except BusError as exc:
                self.failed(exc)
                self.stats.rejected += 1
                logger.warning("The bus refused a buffered message on %s: %s", subject, exc)
            self.buffer.popleft()
            self.stats.buffered = len(self.buffer)
            sent += 1
        return sent

    async def run(self) -> None:
        """Flush the buffer whenever it holds messages, backing off while the bus is away, until cancelled."""
        failures = 0
        while True:
            if not self.buffer:
                self.wakeup.clear()
                await self.wakeup.wait()
                continue
            try:
                sent = await self.flush()
            except BusUnavailable:
                await asyncio.sleep(FLUSH_DELAYS[min(failures, len(FLUSH_DELAYS) - 1)])
                failures += 1
                continue
            if sent:
                logger.info("Sent %d buffered messages to the bus", sent)
            failures = 0


class ThreadedPublisher:
    """A publisher for producers that run threads rather than asyncio, such as the analytics engine.

    It runs its own event loop in a daemon thread with its own bus connection. submit never blocks: it queues the
    message for the loop, which publishes it with the Publisher's retry and buffer.

    Attributes:
        factory: Builds the bus inside the loop's thread.
        limit: Most messages the local buffer holds.
        intake: Messages handed over by submit, not yet taken by the loop.
        publisher: The publisher, once the loop runs.
        thread: The loop's thread.
        stopping: Set to stop the loop.
        connected: Whether the bus has been connected.
    """

    def __init__(self, factory: Callable[[], Bus], limit: int = BUFFER_LIMIT):
        """Describe the publisher; call start to run it.

        Args:
            factory: Builds the bus.
            limit: Most messages the local buffer holds.
        """
        self.factory = factory
        self.limit = limit
        self.intake: queue.Queue = queue.Queue(maxsize=limit)
        self.publisher: Publisher | None = None
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()
        self.connected = False

    def start(self) -> None:
        """Run the loop in a daemon thread."""
        if self.thread is None:
            self.thread = threading.Thread(target=self.main, name="bus-publisher", daemon=True)
            self.thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the loop to stop and wait for it.

        Args:
            timeout: Seconds to wait.
        """
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def submit(self, subject: str, envelope: Envelope, droppable: bool = False) -> None:
        """Hand a message to the loop without waiting.

        Args:
            subject: The subject.
            envelope: The message.
            droppable: True for a message the next one replaces.
        """
        try:
            self.intake.put_nowait((subject, envelope, droppable))
        except queue.Full:
            if self.publisher is not None:
                self.publisher.stats.dropped += 1
            logger.warning("The bus intake is full, dropping a message on %s", subject)

    def stats(self) -> dict:
        """Return the counters, with the messages still waiting for the loop.

        Returns:
            The counters by name.
        """
        counters = self.publisher.stats.as_dict() if self.publisher is not None else PublisherStats().as_dict()
        counters["waiting"] = self.intake.qsize()
        counters["connected"] = self.connected
        return counters

    def main(self) -> None:
        """Run the event loop of the thread."""
        asyncio.run(self.serve())

    async def serve(self) -> None:
        """Connect, then publish what submit hands over until stopped."""
        bus = self.factory()
        self.publisher = Publisher(bus, limit=self.limit)
        await self.publisher.start()
        connecting = asyncio.create_task(self.connect(bus))
        loop = asyncio.get_running_loop()
        try:
            while not self.stopping.is_set():
                try:
                    subject, envelope, droppable = await loop.run_in_executor(None, self.take)
                except queue.Empty:
                    continue
                if not self.connected:
                    if droppable:
                        self.publisher.stats.dropped += 1
                    else:
                        self.publisher.keep(subject, envelope)
                    continue
                await self.publisher.publish(subject, envelope, droppable)
        finally:
            connecting.cancel()
            await self.publisher.stop()
            await bus.close()

    def take(self) -> tuple[str, Envelope, bool]:
        """Wait briefly for the next submitted message.

        Returns:
            Subject, envelope and droppable flag.

        Raises:
            queue.Empty: Nothing arrived in time.
        """
        return self.intake.get(timeout=0.5)

    async def connect(self, bus: Bus) -> None:
        """Connect the bus, retrying until it answers.

        Args:
            bus: The bus.
        """
        while not self.stopping.is_set():
            try:
                await bus.connect()
            except BusError as exc:
                if self.publisher is not None:
                    self.publisher.failed(exc)
                await asyncio.sleep(2.0)
                continue
            self.connected = True
            if self.publisher is not None:
                self.publisher.wakeup.set()
            return
