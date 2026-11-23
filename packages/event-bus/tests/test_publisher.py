import asyncio

from event_bus import Envelope, EventType, Publisher, ThreadedPublisher, subjects
from event_bus.memory import MemoryBus
from event_bus.streams import EVENTS, OBSERVATIONS, provision


def change(name):
    return Envelope.create(EventType.ANALYTICS_EVENT, "/sss/test", {"id": name})


SUBJECT = subjects.analytics_event("o", "s", "v")


async def test_a_message_is_published_with_its_acknowledgement(bus):
    publisher = Publisher(bus)
    assert await publisher.publish(SUBJECT, change("a"))
    assert publisher.stats.published == 1
    assert [envelope.data["id"] for envelope in bus.stored(EVENTS.name)] == ["a"]


async def test_messages_wait_in_order_while_the_bus_is_away(bus):
    publisher = Publisher(bus, attempts=1)
    await publisher.start()
    await bus.set_online(False)
    for name in ("a", "b", "c"):
        assert not await publisher.publish(SUBJECT, change(name))
    assert publisher.stats.buffered == 3
    await bus.set_online(True)
    for _ in range(100):
        if not publisher.buffer:
            break
        await asyncio.sleep(0.05)
    await publisher.stop()
    assert [envelope.data["id"] for envelope in bus.stored(EVENTS.name)] == ["a", "b", "c"]
    assert publisher.stats.buffered == 0


async def test_a_new_message_queues_behind_buffered_ones(bus):
    publisher = Publisher(bus, attempts=1)
    await bus.set_online(False)
    await publisher.publish(SUBJECT, change("a"))
    await bus.set_online(True)
    assert not await publisher.publish(SUBJECT, change("b"))
    assert await publisher.flush() == 2
    assert [envelope.data["id"] for envelope in bus.stored(EVENTS.name)] == ["a", "b"]


async def test_droppable_messages_are_never_buffered(bus):
    publisher = Publisher(bus, attempts=1)
    await bus.set_online(False)
    observation = Envelope.create(EventType.OBSERVATION, "/sss/test", {"at": 1, "view": {"id": "v"}})
    assert not await publisher.publish(subjects.analytics_observation("o", "s", "v"), observation, droppable=True)
    assert publisher.stats.dropped == 1
    assert not publisher.buffer


async def test_a_full_buffer_drops_its_oldest_message(bus):
    publisher = Publisher(bus, limit=2, attempts=1)
    await bus.set_online(False)
    for name in ("a", "b", "c"):
        await publisher.publish(SUBJECT, change(name))
    assert [envelope.data["id"] for _, envelope in publisher.buffer] == ["b", "c"]
    assert publisher.stats.dropped == 1


async def test_a_refused_message_is_counted_and_not_retried(bus):
    publisher = Publisher(bus)
    assert not await publisher.publish("sss.nothing", change("a"))
    assert publisher.stats.rejected == 1
    assert not publisher.buffer


async def test_a_repeated_envelope_is_stored_once(bus):
    publisher = Publisher(bus)
    envelope = change("a")
    await publisher.publish(SUBJECT, envelope)
    await publisher.publish(SUBJECT, envelope)
    assert publisher.stats.duplicates == 1
    assert len(bus.stored(EVENTS.name)) == 1


def test_the_threaded_publisher_buffers_until_it_connects():
    shared = {}

    def factory():
        memory = MemoryBus()
        memory.online = False
        shared["bus"] = memory
        return memory

    publisher = ThreadedPublisher(factory, limit=10)
    publisher.start()
    publisher.submit(SUBJECT, change("a"))
    observation = Envelope.create(EventType.OBSERVATION, "/sss/test", {"at": 1, "view": {"id": "v"}})
    publisher.submit(subjects.analytics_observation("o", "s", "v"), observation, droppable=True)
    import time

    for _ in range(100):
        if publisher.stats()["buffered"] == 1 and publisher.intake.empty():
            break
        time.sleep(0.02)
    stats = publisher.stats()
    assert stats["buffered"] == 1
    assert stats["dropped"] == 1
    memory = shared["bus"]
    memory.online = True

    async def ready():
        await provision(memory)

    asyncio.run(ready())
    for _ in range(200):
        if memory.streams and memory.stored(EVENTS.name):
            break
        time.sleep(0.02)
    publisher.stop()
    assert [envelope.data["id"] for envelope in memory.stored(EVENTS.name)] == ["a"]
    assert memory.stored(OBSERVATIONS.name) == []
