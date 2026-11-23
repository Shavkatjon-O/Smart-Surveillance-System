"""The streams and durable consumers of the platform, and their provisioning.

The limits keep the bus small enough for a home server or a Jetson and long enough to outlast an outage of the
database or a worker; packages/event-bus/README.md gives the reasoning for each value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from event_bus import subjects

if TYPE_CHECKING:
    from event_bus.bus import Bus

__all__ = [
    "ALERTS",
    "CONSUMERS",
    "DEAD_LETTERS",
    "EVENTS",
    "EVENT_STORE",
    "METERING",
    "NOTIFIER_PUSH",
    "NOTIFIER_TELEGRAM",
    "OBSERVATIONS",
    "STREAMS",
    "TELEMETRY",
    "USAGE",
    "ConsumerSpec",
    "StreamSpec",
    "provision",
]

MINUTE = 60.0
HOUR = 60 * MINUTE
DAY = 24 * HOUR
MIB = 1024 * 1024


@dataclass(frozen=True)
class StreamSpec:
    """A JetStream stream: what it captures and how long it keeps it.

    Attributes:
        name: Stream name.
        subjects: Subject patterns it captures.
        max_age: Seconds a message is kept at most.
        max_bytes: Bytes the stream keeps at most; the oldest messages go first.
        duplicate_window: Seconds a message id is remembered to drop a repeated publish.
        max_msgs_per_subject: Messages kept per subject, -1 for no limit.
        memory: Whether the stream lives in memory rather than on disk.
        description: What the stream carries.
    """

    name: str
    subjects: tuple[str, ...]
    max_age: float
    max_bytes: int
    duplicate_window: float = 2 * MINUTE
    max_msgs_per_subject: int = -1
    memory: bool = False
    description: str = ""


@dataclass(frozen=True)
class ConsumerSpec:
    """A durable pull consumer, shared by every replica of one worker.

    A failed message waits the next delay of the backoff before it is delivered again; after the last delay it goes
    to the dead letter stream. The server's own delivery limit sits two deliveries above that, so a message that
    keeps crashing its worker stops being delivered even when the worker never reaches its dead letter path.

    Attributes:
        stream: Stream it reads.
        durable: Durable name, which every replica of the worker binds to.
        subjects: Subject filters within the stream.
        ack_wait: Seconds the server waits for an acknowledgement before it delivers again.
        backoff: Seconds to wait before each further delivery of a failed message.
        max_ack_pending: Messages delivered and not yet acknowledged at most, across replicas.
        description: What the consumer does.
    """

    stream: str
    durable: str
    subjects: tuple[str, ...]
    ack_wait: float = 30.0
    backoff: tuple[float, ...] = field(default=(1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0))
    max_ack_pending: int = 256
    description: str = ""

    @property
    def max_attempts(self) -> int:
        """int: Deliveries a message gets before it goes to the dead letter stream."""
        return len(self.backoff) + 1

    @property
    def max_deliver(self) -> int:
        """int: The server's delivery limit, two above the dead letter path."""
        return self.max_attempts + 2

    def delay(self, attempt: int) -> float:
        """Return how long a message that failed on a delivery waits before the next one.

        Args:
            attempt: The delivery that failed, from 1.

        Returns:
            Seconds.
        """
        if not self.backoff:
            return 0.0
        return self.backoff[min(max(attempt, 1), len(self.backoff)) - 1]


EVENTS = StreamSpec(
    name="SSS_EVENTS",
    subjects=(subjects.CAMERA_EVENTS, subjects.ANALYTICS_EVENTS),
    max_age=3 * DAY,
    max_bytes=512 * MIB,
    duplicate_window=10 * MINUTE,
    description="Changes of camera and analytics events, read by the event store",
)

OBSERVATIONS = StreamSpec(
    name="SSS_OBSERVATIONS",
    subjects=(subjects.OBSERVATIONS,),
    max_age=10 * MINUTE,
    max_bytes=16 * MIB,
    max_msgs_per_subject=1,
    memory=True,
    description="The latest status of every analytics view, one message per view",
)

ALERTS = StreamSpec(
    name="SSS_ALERTS",
    subjects=(subjects.ALERTS,),
    max_age=1 * DAY,
    max_bytes=64 * MIB,
    duplicate_window=1 * HOUR,
    description="Alerts, one per event, read by the notifier channels",
)

USAGE = StreamSpec(
    name="SSS_USAGE",
    subjects=(subjects.USAGE,),
    max_age=7 * DAY,
    max_bytes=64 * MIB,
    duplicate_window=1 * HOUR,
    description="Counted usage, read by metering",
)

TELEMETRY = StreamSpec(
    name="SSS_TELEMETRY",
    subjects=(subjects.TELEMETRY,),
    max_age=1 * DAY,
    max_bytes=64 * MIB,
    max_msgs_per_subject=1000,
    description="Connector telemetry, reserved for the connectors",
)

DEAD_LETTERS = StreamSpec(
    name="SSS_DEADLETTER",
    subjects=(subjects.DEAD_LETTERS,),
    max_age=14 * DAY,
    max_bytes=128 * MIB,
    duplicate_window=1 * HOUR,
    description="Messages a consumer gave up on, with the reason",
)

STREAMS = (EVENTS, OBSERVATIONS, ALERTS, USAGE, TELEMETRY, DEAD_LETTERS)

EVENT_STORE = ConsumerSpec(
    stream=EVENTS.name,
    durable="event-store",
    subjects=EVENTS.subjects,
    ack_wait=30.0,
    description="Stores every event change in Postgres",
)

NOTIFIER_TELEGRAM = ConsumerSpec(
    stream=ALERTS.name,
    durable="notifier-telegram",
    subjects=ALERTS.subjects,
    ack_wait=120.0,
    backoff=(5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 900.0, 900.0, 900.0),
    max_ack_pending=64,
    description="Sends every alert to the organisation's Telegram chats",
)

NOTIFIER_PUSH = ConsumerSpec(
    stream=ALERTS.name,
    durable="notifier-push",
    subjects=ALERTS.subjects,
    ack_wait=120.0,
    backoff=(5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 900.0, 900.0, 900.0),
    max_ack_pending=64,
    description="Pushes every alert to the members' devices",
)

METERING = ConsumerSpec(
    stream=USAGE.name,
    durable="metering",
    subjects=USAGE.subjects,
    ack_wait=30.0,
    description="Adds counted usage to the hourly rollups",
)

CONSUMERS = (EVENT_STORE, NOTIFIER_TELEGRAM, NOTIFIER_PUSH, METERING)


async def provision(
    bus: Bus, streams: tuple[StreamSpec, ...] = STREAMS, consumers: tuple[ConsumerSpec, ...] = CONSUMERS
) -> None:
    """Create or update the streams and durable consumers; safe to run from every process at every start.

    Args:
        bus: A connected bus.
        streams: Streams to provision.
        consumers: Durable consumers to provision, after their streams.
    """
    for stream in streams:
        await bus.ensure_stream(stream)
    for consumer in consumers:
        await bus.ensure_consumer(consumer)
