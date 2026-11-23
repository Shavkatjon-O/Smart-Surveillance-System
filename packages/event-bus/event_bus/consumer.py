"""Consumers' side of the bus: a runner that hands each message of a durable consumer to a handler.

The handler returns to acknowledge the message. It raises Retry, or any other error, to have the message delivered
again after the consumer's next backoff delay, and Reject for a message that can never succeed. A message that
fails on its last allowed delivery, or is rejected, is published to the dead letter subject of its stream and
consumer with the reason, and ended.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable

from event_bus import subjects
from event_bus.bus import Bus, BusError, Delivery
from event_bus.envelope import Envelope, EnvelopeError, stable_id
from event_bus.streams import ConsumerSpec

__all__ = ["ConsumerRunner", "Reject", "Retry", "dead_letter_envelope"]

logger = logging.getLogger(__name__)

BATCH = 16
FETCH_SECONDS = 5.0
DEAD_LETTER_SOURCE = "/sss/consumer"
PAYLOAD_PREVIEW = 4096


class Retry(Exception):
    """The message should be delivered again later.

    Attributes:
        delay: Seconds to wait, None for the consumer's backoff.
    """

    def __init__(self, reason: str, delay: float | None = None):
        """Create the signal.

        Args:
            reason: Why the message failed.
            delay: Seconds to wait, None for the consumer's backoff.
        """
        super().__init__(reason)
        self.delay = delay


class Reject(Exception):
    """The message can never succeed and goes to the dead letter subject at once."""


def dead_letter_envelope(spec: ConsumerSpec, delivery: Delivery, reason: str) -> Envelope:
    """Describe a message a consumer gave up on.

    Args:
        spec: The consumer.
        delivery: The failed delivery.
        reason: Why it failed.

    Returns:
        The dead letter, whose id is stable per consumer and message so repeating it is harmless.
    """
    try:
        original = delivery.envelope
    except EnvelopeError:
        original = None
    data = {
        "stream": spec.stream,
        "consumer": spec.durable,
        "subject": delivery.subject,
        "attempts": delivery.attempt,
        "reason": reason[:1000],
    }
    if original is not None:
        data["envelope"] = original.to_dict()
        key = original.id
    else:
        data["payload"] = delivery.payload[:PAYLOAD_PREVIEW].decode("utf-8", errors="replace")
        key = f"{delivery.subject}:{hashlib.sha256(delivery.payload).hexdigest()}"
    return Envelope.create(
        type=subjects.EventType.DEAD_LETTER,
        source=DEAD_LETTER_SOURCE,
        data=data,
        subject=original.id if original is not None else None,
        organisation=original.organisation if original is not None else None,
        site=original.site if original is not None else None,
        id=stable_id("dead-letter", spec.durable, key),
    )


class ConsumerRunner:
    """Runs one durable consumer: fetch, handle, then acknowledge, delay or dead-letter each message.

    Several processes may run the same consumer; the server shares the messages among them.

    Attributes:
        bus: The bus.
        spec: The consumer.
        handler: Coroutine function called with each envelope and its delivery.
        batch: Messages fetched at once.
        fetch_seconds: Seconds a fetch waits for the first message.
        on_tick: Called after every fetch, such as a liveness heartbeat.
        handled: Messages acknowledged.
        retried: Messages handed back for a later delivery.
        dead: Messages sent to the dead letter subject.
        last_error: The latest handler error.
        last_tick: Monotonic time of the last fetch.
    """

    def __init__(
        self,
        bus: Bus,
        spec: ConsumerSpec,
        handler: Callable[[Envelope, Delivery], Awaitable[None]],
        batch: int = BATCH,
        fetch_seconds: float = FETCH_SECONDS,
        on_tick: Callable[[], None] | None = None,
    ):
        """Describe the runner.

        Args:
            bus: The bus.
            spec: The consumer.
            handler: Called with each envelope and its delivery.
            batch: Messages fetched at once.
            fetch_seconds: Seconds a fetch waits for the first message.
            on_tick: Called after every fetch.
        """
        self.bus = bus
        self.spec = spec
        self.handler = handler
        self.batch = batch
        self.fetch_seconds = fetch_seconds
        self.on_tick = on_tick
        self.handled = 0
        self.retried = 0
        self.dead = 0
        self.last_error: str | None = None
        self.last_tick = time.monotonic()

    async def run(self) -> None:
        """Make sure the consumer exists, then handle messages until cancelled."""
        while True:
            try:
                await self.bus.ensure_consumer(self.spec)
                break
            except BusError as exc:
                logger.warning("Provisioning consumer %s failed, retrying: %s", self.spec.durable, exc)
                await asyncio.sleep(2.0)
        logger.info("Consuming %s from %s", self.spec.durable, self.spec.stream)
        while True:
            await self.once()

    async def once(self) -> int:
        """Fetch one batch and handle it.

        Returns:
            Messages handled in the batch, successful or not.
        """
        deliveries = await self.bus.fetch(self.spec, self.batch, self.fetch_seconds)
        self.last_tick = time.monotonic()
        if self.on_tick is not None:
            self.on_tick()
        for delivery in deliveries:
            await self.process(delivery)
        return len(deliveries)

    async def process(self, delivery: Delivery) -> None:
        """Handle one delivery and settle it.

        Args:
            delivery: The delivery.
        """
        try:
            envelope = delivery.envelope
        except EnvelopeError as exc:
            await self.give_up(delivery, f"Not an envelope: {exc}")
            return
        try:
            await self.handler(envelope, delivery)
        except Reject as exc:
            await self.give_up(delivery, f"Rejected: {exc}")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self.last_error = reason
            if delivery.attempt >= self.spec.max_attempts:
                logger.warning("Giving up on %s after %d deliveries: %s", envelope.id, delivery.attempt, reason)
                await self.give_up(delivery, reason)
                return
            delay = exc.delay if isinstance(exc, Retry) and exc.delay is not None else self.spec.delay(delivery.attempt)
            if isinstance(exc, Retry):
                logger.info("Retrying %s in %.0f s: %s", envelope.id, delay, reason)
            else:
                logger.exception("Handling %s failed, retrying in %.0f s", envelope.id, delay)
            await self.settle(delivery.nak(delay))
            self.retried += 1
            return
        await self.settle(delivery.ack())
        self.handled += 1

    async def give_up(self, delivery: Delivery, reason: str) -> None:
        """Publish a delivery to the dead letter subject and end it, or deliver it again when that fails.

        Args:
            delivery: The delivery.
            reason: Why it failed.
        """
        letter = dead_letter_envelope(self.spec, delivery, reason)
        try:
            await self.bus.publish(subjects.dead_letter(self.spec.stream, self.spec.durable), letter)
        except BusError as exc:
            logger.warning("Writing a dead letter failed, delivering again later: %s", exc)
            await self.settle(delivery.nak(self.spec.delay(len(self.spec.backoff))))
            return
        await self.settle(delivery.term())
        self.dead += 1

    async def settle(self, action: Awaitable[None]) -> None:
        """Run an acknowledgement action, logging a connection that went away meanwhile.

        Args:
            action: ack, nak or term of a delivery.
        """
        try:
            await action
        except Exception as exc:
            logger.warning("Settling a message failed, the server will deliver it again: %s", exc)
