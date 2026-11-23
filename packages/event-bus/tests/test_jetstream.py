import asyncio
import os
from uuid import uuid4

import pytest

from event_bus import ConsumerRunner, Envelope, EventType, Publisher, subjects
from event_bus.streams import DEAD_LETTERS, EVENT_STORE, EVENTS, OBSERVATIONS, ConsumerSpec, provision

URL = os.environ.get("SSS_TEST_NATS_URL")

pytestmark = pytest.mark.skipif(not URL, reason="SSS_TEST_NATS_URL names no NATS server")


@pytest.fixture
async def nats_bus():
    from event_bus.jetstream import NatsBus

    bus = NatsBus(URL, name="event-bus-tests", connect_wait=10)
    await bus.connect()
    js = bus.context()
    for stream in (EVENTS, OBSERVATIONS, DEAD_LETTERS):
        try:
            await js.delete_stream(stream.name)
        except Exception:
            pass
    await provision(bus)
    yield bus
    await bus.close()


def change(name):
    return Envelope.create(EventType.CAMERA_EVENT, "/sss/test", {"id": name}, organisation="o", site="s")


async def test_publishing_twice_stores_once(nats_bus):
    envelope = change("a")
    subject = subjects.camera_event("o", "s", "c")
    first = await nats_bus.publish(subject, envelope)
    again = await nats_bus.publish(subject, envelope)
    assert first.stream == EVENTS.name
    assert again.duplicate
    info = await nats_bus.context().stream_info(EVENTS.name)
    assert info.state.messages == 1


async def test_a_durable_consumer_redelivers_after_a_nak_and_dead_letters_at_the_end(nats_bus):
    spec = ConsumerSpec(
        stream=EVENTS.name, durable=f"test-{uuid4().hex[:8]}", subjects=EVENTS.subjects, ack_wait=5, backoff=(0.2, 0.2)
    )
    attempts = []

    async def handler(envelope, delivery):
        attempts.append(delivery.attempt)
        raise RuntimeError("always")

    runner = ConsumerRunner(nats_bus, spec, handler, fetch_seconds=0.5)
    await nats_bus.ensure_consumer(spec)
    await nats_bus.publish(subjects.camera_event("o", "s", "c"), change("b"))
    for _ in range(20):
        await runner.once()
        if runner.dead:
            break
    assert attempts == [1, 2, 3]
    assert runner.dead == 1
    info = await nats_bus.context().stream_info(DEAD_LETTERS.name)
    assert info.state.messages == 1
    await nats_bus.context().delete_consumer(EVENTS.name, spec.durable)


async def test_follow_reads_the_last_observation_of_each_view(nats_bus):
    for view, value in (("v1", 1), ("v1", 2), ("v2", 3)):
        envelope = Envelope.create(EventType.OBSERVATION, "/sss/test", {"at": value, "view": {"id": view}})
        await nats_bus.publish(subjects.analytics_observation("o", "s", view), envelope)
    seen = []

    async def handler(envelope):
        seen.append(envelope.data["at"])

    listening = await nats_bus.follow(OBSERVATIONS, subjects.OBSERVATIONS, handler)
    for _ in range(50):
        if len(seen) >= 2:
            break
        await asyncio.sleep(0.05)
    await listening.stop()
    assert sorted(seen) == [2, 3]


async def test_listeners_receive_the_live_fan_out(nats_bus):
    seen = []

    async def handler(envelope):
        seen.append(envelope.data["id"])

    listening = await nats_bus.listen(subjects.LIVE, handler)
    await nats_bus.broadcast(subjects.live("o", "s"), change("live"))
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0.05)
    await listening.stop()
    assert seen == ["live"]


async def test_the_publisher_buffers_through_an_unreachable_server():
    from event_bus.jetstream import NatsBus

    bus = NatsBus("nats://127.0.0.1:1", connect_wait=0.5)
    publisher = Publisher(bus, attempts=1)
    assert not await publisher.publish(subjects.camera_event("o", "s", "c"), change("c"))
    assert publisher.stats.buffered == 1
    assert EVENT_STORE.max_deliver == EVENT_STORE.max_attempts + 2
