"""The bus on NATS with JetStream, through the official nats-py client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import nats
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.errors import ConnectionClosedError, ConnectionReconnectingError, NoRespondersError, NoServersError
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    DiscardPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import APIError, NotFoundError

from event_bus.bus import Bus, BusError, BusUnavailable, Delivery, Handler, Listening, PublishAck
from event_bus.envelope import CONTENT_TYPE, Envelope, EnvelopeError
from event_bus.streams import ConsumerSpec, StreamSpec

__all__ = ["NatsBus", "stream_config"]

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 2.0
RECONNECT_WAIT = 1.0
DRAIN_SECONDS = 3.0
UNREACHABLE = (
    ConnectionClosedError,
    ConnectionReconnectingError,
    NoServersError,
    NatsTimeoutError,
    asyncio.TimeoutError,
    OSError,
)


def stream_config(spec: StreamSpec) -> StreamConfig:
    """Translate a stream specification into the JetStream configuration.

    Args:
        spec: The stream.

    Returns:
        The configuration.
    """
    return StreamConfig(
        name=spec.name,
        description=spec.description or None,
        subjects=list(spec.subjects),
        retention=RetentionPolicy.LIMITS,
        max_age=spec.max_age,
        max_bytes=spec.max_bytes,
        max_msgs_per_subject=spec.max_msgs_per_subject,
        discard=DiscardPolicy.OLD,
        storage=StorageType.MEMORY if spec.memory else StorageType.FILE,
        duplicate_window=spec.duplicate_window,
        num_replicas=1,
    )


def consumer_config(spec: ConsumerSpec) -> ConsumerConfig:
    """Translate a consumer specification into the JetStream configuration.

    Args:
        spec: The consumer.

    Returns:
        The configuration of a durable pull consumer with explicit acknowledgements.
    """
    return ConsumerConfig(
        durable_name=spec.durable,
        description=spec.description or None,
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=spec.ack_wait,
        max_deliver=spec.max_deliver,
        filter_subjects=list(spec.subjects),
        max_ack_pending=spec.max_ack_pending,
    )


class NatsDelivery(Delivery):
    """A JetStream message handed to a durable consumer.

    Attributes:
        message: The nats-py message.
        subject: Subject it was published on.
        attempt: How many times it has been delivered, from 1.
        payload: The raw message.
    """

    def __init__(self, message: Msg):
        """Wrap a message.

        Args:
            message: The nats-py message.
        """
        self.message = message
        self.subject = message.subject
        self.payload = message.data
        try:
            self.attempt = int(message.metadata.num_delivered)
        except (AttributeError, TypeError, ValueError):
            self.attempt = 1

    async def ack(self) -> None:
        """Confirm the message."""
        await self.message.ack()

    async def nak(self, delay: float) -> None:
        """Deliver the message again after a delay.

        Args:
            delay: Seconds before the next delivery.
        """
        await self.message.nak(delay=delay)

    async def term(self) -> None:
        """End the message without success."""
        await self.message.term()

    async def working(self) -> None:
        """Restart the message's acknowledgement wait."""
        await self.message.in_progress()


class NatsListening(Listening):
    """A nats-py subscription.

    Attributes:
        subscription: The subscription.
    """

    def __init__(self, subscription):
        """Wrap a subscription.

        Args:
            subscription: A nats-py core or JetStream push subscription.
        """
        self.subscription = subscription

    async def stop(self) -> None:
        """Unsubscribe, ignoring a connection that is already gone."""
        try:
            await self.subscription.unsubscribe()
        except Exception as exc:
            logger.debug("Unsubscribing failed: %s", exc)


def envelope_callback(handler: Handler) -> Callable[[Msg], Awaitable[None]]:
    """Wrap a handler of envelopes as a nats-py message callback that skips messages that are not envelopes.

    Args:
        handler: Called with each envelope.

    Returns:
        The callback.
    """

    async def callback(message: Msg) -> None:
        """Decode the message and hand it to the handler, logging a failure.

        Args:
            message: The message.
        """
        try:
            envelope = Envelope.from_bytes(message.data)
        except EnvelopeError as exc:
            logger.warning("Skipping a message on %s that is not an envelope: %s", message.subject, exc)
            return
        try:
            await handler(envelope)
        except Exception:
            logger.exception("Handling a message on %s failed", message.subject)

    return callback


class NatsBus(Bus):
    """The bus on one NATS server or cluster.

    Attributes:
        servers: NATS server URLs.
        name: Connection name shown by the server.
        connect_wait: Seconds connect keeps trying before it gives up, 0 to try forever.
        client: The connection, None until connected.
        js: The JetStream context, None until connected.
        pulls: Pull subscriptions bound to durable consumers, by durable name.
        reconnects: Callbacks run after a reconnect.
        closing: Whether close is running, so the disconnect it causes is not reported as a loss.
    """

    def __init__(self, servers: str | list[str], name: str = "sss", connect_wait: float = 30.0):
        """Describe the connection without opening it.

        Args:
            servers: One URL, a comma-separated list or a list, such as nats://nats:4222.
            name: Connection name shown by the server.
            connect_wait: Seconds connect keeps trying before it gives up, 0 to try forever.
        """
        if isinstance(servers, str):
            servers = [item.strip() for item in servers.split(",") if item.strip()]
        self.servers = servers
        self.name = name
        self.connect_wait = connect_wait
        self.client: Client | None = None
        self.js: JetStreamContext | None = None
        self.pulls: dict[str, JetStreamContext.PullSubscription] = {}
        self.reconnects: list[Callable[[], Awaitable[None]]] = []
        self.closing = False

    @property
    def connected(self) -> bool:
        """bool: Whether the connection is up."""
        return self.client is not None and self.client.is_connected

    async def connect(self) -> None:
        """Connect, retrying until the server answers or connect_wait runs out.

        Raises:
            BusUnavailable: The server could not be reached in time.
        """
        if self.client is not None and not self.client.is_closed:
            return
        self.closing = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.connect_wait if self.connect_wait else None
        delay = 0.5
        while True:
            try:
                self.client = await nats.connect(
                    servers=self.servers,
                    name=self.name,
                    connect_timeout=CONNECT_TIMEOUT,
                    reconnect_time_wait=RECONNECT_WAIT,
                    max_reconnect_attempts=-1,
                    allow_reconnect=True,
                    reconnected_cb=self.reconnected,
                    disconnected_cb=self.disconnected,
                    error_cb=self.failed,
                )
                break
            except UNREACHABLE as exc:
                if deadline is not None and loop.time() + delay > deadline:
                    raise BusUnavailable(f"NATS at {', '.join(self.servers)} does not answer: {exc}") from None
                logger.info("Waiting for NATS at %s: %s", ", ".join(self.servers), exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)
        self.js = self.client.jetstream()
        self.pulls.clear()
        logger.info("Connected to NATS at %s", self.client.connected_url.netloc if self.client.connected_url else "")

    async def close(self) -> None:
        """Drain the connection, which sends what is pending, and close it within a few seconds."""
        client, self.client, self.js = self.client, None, None
        self.pulls.clear()
        self.closing = True
        if client is None or client.is_closed:
            return
        try:
            await asyncio.wait_for(client.drain(), DRAIN_SECONDS)
        except Exception as exc:
            logger.debug("Draining the NATS connection failed: %s", exc)
        if not client.is_closed:
            await client.close()

    def on_reconnect(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Run a callback after every reconnect.

        Args:
            callback: Coroutine function to run.
        """
        self.reconnects.append(callback)

    async def reconnected(self) -> None:
        """Log a reconnect and run the reconnect callbacks."""
        logger.info("Reconnected to NATS")
        for callback in list(self.reconnects):
            try:
                await callback()
            except Exception:
                logger.exception("A reconnect callback failed")

    async def disconnected(self) -> None:
        """Log a lost connection, unless close caused it."""
        if not self.closing:
            logger.warning("Lost the connection to NATS")

    async def failed(self, error: Exception) -> None:
        """Log a connection error.

        Args:
            error: The error.
        """
        logger.warning("NATS connection error: %s", error)

    def context(self) -> JetStreamContext:
        """Return the JetStream context.

        Returns:
            The context.

        Raises:
            BusUnavailable: The bus is not connected.
        """
        if self.js is None or self.client is None or self.client.is_closed:
            raise BusUnavailable("The bus is not connected")
        return self.js

    async def ensure_stream(self, spec: StreamSpec) -> None:
        """Create a stream or update the existing one.

        Args:
            spec: The stream.

        Raises:
            BusUnavailable: The bus could not be reached.
            BusError: The server refused the configuration.
        """
        js = self.context()
        config = stream_config(spec)
        try:
            try:
                await js.stream_info(spec.name)
            except NotFoundError:
                await js.add_stream(config)
                logger.info("Created stream %s", spec.name)
                return
            await js.update_stream(config)
        except UNREACHABLE as exc:
            raise BusUnavailable(f"Provisioning stream {spec.name} failed: {exc}") from None
        except APIError as exc:
            raise BusError(f"NATS refused stream {spec.name}: {exc.description}") from None

    async def ensure_consumer(self, spec: ConsumerSpec) -> None:
        """Create a durable consumer or update the existing one.

        Args:
            spec: The consumer.

        Raises:
            BusUnavailable: The bus could not be reached.
            BusError: The server refused the configuration.
        """
        js = self.context()
        try:
            await js.add_consumer(spec.stream, consumer_config(spec))
        except UNREACHABLE as exc:
            raise BusUnavailable(f"Provisioning consumer {spec.durable} failed: {exc}") from None
        except APIError as exc:
            raise BusError(f"NATS refused consumer {spec.durable}: {exc.description}") from None

    async def publish(self, subject: str, envelope: Envelope, timeout: float = 2.0) -> PublishAck:
        """Store a message and wait for the stream's acknowledgement.

        Args:
            subject: The subject.
            envelope: The message; its id is sent as Nats-Msg-Id.
            timeout: Seconds to wait for the acknowledgement.

        Returns:
            The acknowledgement.

        Raises:
            BusUnavailable: The bus could not be reached or did not answer in time.
            BusError: No stream captures the subject or the stream refused the message.
        """
        js = self.context()
        headers = {"Nats-Msg-Id": envelope.id, "Content-Type": CONTENT_TYPE}
        try:
            ack = await js.publish(subject, envelope.to_bytes(), timeout=timeout, headers=headers)
        except NoRespondersError:
            raise BusError(f"No stream captures {subject}") from None
        except UNREACHABLE as exc:
            raise BusUnavailable(f"Publishing on {subject} failed: {type(exc).__name__}") from None
        except APIError as exc:
            raise BusError(f"The stream refused a message on {subject}: {exc.description}") from None
        return PublishAck(stream=ack.stream, sequence=ack.seq, duplicate=bool(ack.duplicate))

    async def broadcast(self, subject: str, envelope: Envelope) -> None:
        """Send a message to the current listeners.

        Args:
            subject: The subject.
            envelope: The message.

        Raises:
            BusUnavailable: The bus could not be reached.
        """
        if self.client is None or self.client.is_closed:
            raise BusUnavailable("The bus is not connected")
        try:
            await self.client.publish(subject, envelope.to_bytes(), headers={"Content-Type": CONTENT_TYPE})
        except UNREACHABLE as exc:
            raise BusUnavailable(f"Sending on {subject} failed: {type(exc).__name__}") from None

    async def fetch(self, spec: ConsumerSpec, batch: int, timeout: float) -> list[Delivery]:
        """Take up to a batch of messages from a durable consumer.

        Args:
            spec: The consumer.
            batch: Most messages to take.
            timeout: Seconds to wait for the first.

        Returns:
            The deliveries, empty when none arrived in time or the bus is away.
        """
        try:
            js = self.context()
        except BusUnavailable:
            await asyncio.sleep(min(timeout, 1.0))
            return []
        subscription = self.pulls.get(spec.durable)
        try:
            if subscription is None:
                subscription = await js.pull_subscribe_bind(durable=spec.durable, stream=spec.stream)
                self.pulls[spec.durable] = subscription
            messages = await subscription.fetch(batch, timeout=timeout)
        except NatsTimeoutError:
            return []
        except NotFoundError:
            self.pulls.pop(spec.durable, None)
            await self.ensure_consumer(spec)
            return []
        except UNREACHABLE as exc:
            self.pulls.pop(spec.durable, None)
            logger.debug("Fetching from %s failed: %s", spec.durable, exc)
            await asyncio.sleep(min(timeout, 1.0))
            return []
        return [NatsDelivery(message) for message in messages]

    async def listen(self, subject: str, handler: Handler) -> Listening:
        """Subscribe to matching subjects without durability.

        Args:
            subject: Subject pattern.
            handler: Called with each envelope.

        Returns:
            The subscription, which the client renews after a reconnect.

        Raises:
            BusUnavailable: The bus is not connected.
        """
        if self.client is None or self.client.is_closed:
            raise BusUnavailable("The bus is not connected")
        return NatsListening(await self.client.subscribe(subject, cb=envelope_callback(handler)))

    async def follow(self, stream: StreamSpec, subject: str, handler: Handler) -> Listening:
        """Read the last message of every matching subject and then every new one, through an ordered consumer.

        Args:
            stream: Stream that keeps the messages.
            subject: Subject pattern within it.
            handler: Called with each envelope.

        Returns:
            The subscription.

        Raises:
            BusUnavailable: The bus could not be reached.
        """
        js = self.context()
        try:
            subscription = await js.subscribe(
                subject,
                stream=stream.name,
                cb=envelope_callback(handler),
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
            )
        except UNREACHABLE as exc:
            raise BusUnavailable(f"Following {subject} failed: {exc}") from None
        return NatsListening(subscription)
