"""Event bus of Smart Surveillance System: envelopes, subjects, streams, a publisher and a consumer runner.

Producers and consumers depend on the Bus interface only; NatsBus implements it on NATS JetStream and MemoryBus in
memory for tests, and a Kafka adapter would implement the same interface.
"""

from event_bus.bus import Bus, BusError, BusUnavailable, Delivery, Handler, Listening, PublishAck
from event_bus.consumer import ConsumerRunner, Reject, Retry, dead_letter_envelope
from event_bus.envelope import Envelope, EnvelopeError, stable_id
from event_bus.memory import MemoryBus
from event_bus.publisher import Publisher, PublisherStats, ThreadedPublisher
from event_bus.streams import CONSUMERS, STREAMS, ConsumerSpec, StreamSpec, provision
from event_bus.subjects import EventType

__all__ = [
    "CONSUMERS",
    "STREAMS",
    "Bus",
    "BusError",
    "BusUnavailable",
    "ConsumerRunner",
    "ConsumerSpec",
    "Delivery",
    "Envelope",
    "EnvelopeError",
    "EventType",
    "Handler",
    "Listening",
    "MemoryBus",
    "PublishAck",
    "Publisher",
    "PublisherStats",
    "Reject",
    "Retry",
    "StreamSpec",
    "ThreadedPublisher",
    "dead_letter_envelope",
    "provision",
    "stable_id",
]
