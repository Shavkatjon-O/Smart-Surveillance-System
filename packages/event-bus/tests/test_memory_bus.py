import pytest

from event_bus import BusError, BusUnavailable, Envelope, EventType, subjects
from event_bus.streams import ALERTS, EVENT_STORE, EVENTS, OBSERVATIONS


def change(event="e1"):
    return Envelope.create(EventType.CAMERA_EVENT, "/sss/test", {"id": event}, subject=event)


async def test_the_stream_keeps_one_message_per_id(bus):
    envelope = change()
    first = await bus.publish(subjects.camera_event("o", "s", "c"), envelope)
    again = await bus.publish(subjects.camera_event("o", "s", "c"), envelope)
    assert not first.duplicate
    assert again.duplicate
    assert len(bus.stored(EVENTS.name)) == 1


async def test_an_id_is_forgotten_after_the_duplicate_window(bus, clock):
    envelope = change()
    await bus.publish(subjects.camera_event("o", "s", "c"), envelope)
    clock.advance(EVENTS.duplicate_window + 1)
    again = await bus.publish(subjects.camera_event("o", "s", "c"), envelope)
    assert not again.duplicate


async def test_a_subject_no_stream_captures_is_refused(bus):
    with pytest.raises(BusError):
        await bus.publish("sss.nothing", change())


async def test_an_offline_bus_is_unavailable(bus):
    await bus.set_online(False)
    with pytest.raises(BusUnavailable):
        await bus.publish(subjects.camera_event("o", "s", "c"), change())


async def test_a_consumer_receives_messages_in_order_and_once_acknowledged(bus):
    for name in ("a", "b", "c"):
        await bus.publish(subjects.camera_event("o", "s", "c"), change(name))
    deliveries = await bus.fetch(EVENT_STORE, 10, 0.1)
    assert [delivery.envelope.data["id"] for delivery in deliveries] == ["a", "b", "c"]
    for delivery in deliveries:
        await delivery.ack()
    assert await bus.fetch(EVENT_STORE, 10, 0.05) == []
    assert bus.pending(EVENT_STORE.durable) == 0


async def test_a_nak_delays_the_next_delivery(bus, clock):
    await bus.publish(subjects.camera_event("o", "s", "c"), change())
    (delivery,) = await bus.fetch(EVENT_STORE, 10, 0.1)
    await delivery.nak(30)
    assert await bus.fetch(EVENT_STORE, 10, 0.05) == []
    clock.advance(31)
    (again,) = await bus.fetch(EVENT_STORE, 10, 0.1)
    assert again.attempt == 2


async def test_an_unacknowledged_message_returns_after_the_ack_wait(bus, clock):
    await bus.publish(subjects.camera_event("o", "s", "c"), change())
    await bus.fetch(EVENT_STORE, 10, 0.1)
    clock.advance(EVENT_STORE.ack_wait + 1)
    (again,) = await bus.fetch(EVENT_STORE, 10, 0.1)
    assert again.attempt == 2


async def test_the_server_stops_after_its_delivery_limit(bus, clock):
    await bus.publish(subjects.camera_event("o", "s", "c"), change())
    for _ in range(EVENT_STORE.max_deliver):
        (delivery,) = await bus.fetch(EVENT_STORE, 10, 0.1)
        await delivery.nak(0)
    assert await bus.fetch(EVENT_STORE, 10, 0.05) == []


async def test_listeners_receive_broadcasts_and_stored_messages(bus):
    received = []

    async def handler(envelope):
        received.append(envelope.data["id"])

    await bus.listen(subjects.LIVE, handler)
    await bus.listen(subjects.CAMERA_EVENTS, handler)
    await bus.broadcast(subjects.live("o", "s"), change("live"))
    await bus.publish(subjects.camera_event("o", "s", "c"), change("stored"))
    assert received == ["live", "stored"]


async def test_follow_starts_from_the_last_message_of_every_subject(bus):
    for view, value in (("v1", 1), ("v1", 2), ("v2", 3)):
        envelope = Envelope.create(EventType.OBSERVATION, "/sss/test", {"at": value, "view": {"id": view}})
        await bus.publish(subjects.analytics_observation("o", "s", view), envelope)
    seen = []

    async def handler(envelope):
        seen.append(envelope.data["at"])

    await bus.follow(OBSERVATIONS, subjects.OBSERVATIONS, handler)
    assert sorted(seen) == [2, 3]
    await bus.publish(
        subjects.analytics_observation("o", "s", "v3"),
        Envelope.create(EventType.OBSERVATION, "/sss/test", {"at": 4, "view": {"id": "v3"}}),
    )
    assert seen[-1] == 4


async def test_the_observation_stream_keeps_only_the_latest_per_view(bus):
    for value in range(5):
        envelope = Envelope.create(EventType.OBSERVATION, "/sss/test", {"at": value, "view": {"id": "v"}})
        await bus.publish(subjects.analytics_observation("o", "s", "v"), envelope)
    assert [envelope.data["at"] for envelope in bus.stored(OBSERVATIONS.name)] == [4]


async def test_streams_with_overlapping_subjects_are_refused(bus):
    from event_bus.streams import StreamSpec

    with pytest.raises(BusError):
        await bus.ensure_stream(StreamSpec(name="OTHER", subjects=("sss.*.alert",), max_age=60, max_bytes=1024))
    assert ALERTS.name in bus.streams
