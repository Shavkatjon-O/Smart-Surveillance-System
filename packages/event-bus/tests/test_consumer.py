from event_bus import ConsumerRunner, Envelope, EventType, Reject, Retry, subjects
from event_bus.streams import DEAD_LETTERS, EVENT_STORE, EVENTS


def change(name="a"):
    return Envelope.create(EventType.CAMERA_EVENT, "/sss/test", {"id": name}, organisation="o", site="s")


SUBJECT = subjects.camera_event("o", "s", "c")


async def test_a_handled_message_is_acknowledged(bus):
    seen = []

    async def handler(envelope, delivery):
        seen.append(envelope.data["id"])

    runner = ConsumerRunner(bus, EVENT_STORE, handler, fetch_seconds=0.05)
    await bus.publish(SUBJECT, change())
    assert await runner.once() == 1
    assert seen == ["a"]
    assert runner.handled == 1
    assert bus.pending(EVENT_STORE.durable) == 0


async def test_a_failing_message_comes_back_after_the_backoff(bus, clock):
    calls = []

    async def handler(envelope, delivery):
        calls.append(delivery.attempt)
        if delivery.attempt == 1:
            raise RuntimeError("database away")

    runner = ConsumerRunner(bus, EVENT_STORE, handler, fetch_seconds=0.05)
    await bus.publish(SUBJECT, change())
    await runner.once()
    assert runner.retried == 1
    assert await runner.once() == 0
    clock.advance(EVENT_STORE.delay(1) + 0.1)
    await runner.once()
    assert calls == [1, 2]
    assert runner.handled == 1


async def test_a_retry_may_choose_its_own_delay(bus, clock):
    async def handler(envelope, delivery):
        if delivery.attempt == 1:
            raise Retry("not yet", delay=100)

    runner = ConsumerRunner(bus, EVENT_STORE, handler, fetch_seconds=0.05)
    await bus.publish(SUBJECT, change())
    await runner.once()
    clock.advance(50)
    assert await runner.once() == 0
    clock.advance(51)
    assert await runner.once() == 1


async def test_a_message_that_keeps_failing_goes_to_the_dead_letters(bus, clock):
    async def handler(envelope, delivery):
        raise RuntimeError("always")

    runner = ConsumerRunner(bus, EVENT_STORE, handler, fetch_seconds=0.05)
    original = change()
    await bus.publish(SUBJECT, original)
    for _ in range(EVENT_STORE.max_attempts):
        await runner.once()
        clock.advance(max(EVENT_STORE.backoff) + 1)
    assert runner.dead == 1
    assert bus.pending(EVENT_STORE.durable) == 0
    (letter,) = bus.stored(DEAD_LETTERS.name)
    assert letter.type == EventType.DEAD_LETTER
    assert letter.data["envelope"]["id"] == original.id
    assert letter.data["attempts"] == EVENT_STORE.max_attempts
    assert letter.data["consumer"] == EVENT_STORE.durable
    assert letter.organisation == "o"


async def test_a_rejected_message_goes_to_the_dead_letters_at_once(bus):
    async def handler(envelope, delivery):
        raise Reject("unknown camera")

    runner = ConsumerRunner(bus, EVENT_STORE, handler, fetch_seconds=0.05)
    await bus.publish(SUBJECT, change())
    await runner.once()
    assert runner.dead == 1
    assert "unknown camera" in bus.stored(DEAD_LETTERS.name)[0].data["reason"]


async def test_a_payload_that_is_not_an_envelope_goes_to_the_dead_letters(bus):
    stream = bus.streams[EVENTS.name]
    from event_bus.memory import StoredMessage

    stream.last += 1
    stream.messages[stream.last] = StoredMessage(stream.last, SUBJECT, b"garbage", bus.clock())

    async def handler(envelope, delivery):
        raise AssertionError("never called")

    runner = ConsumerRunner(bus, EVENT_STORE, handler, fetch_seconds=0.05)
    await runner.once()
    (letter,) = bus.stored(DEAD_LETTERS.name)
    assert letter.data["payload"] == "garbage"


async def test_a_dead_letter_is_written_once_per_message(bus, clock):
    async def handler(envelope, delivery):
        raise Reject("bad")

    runner = ConsumerRunner(bus, EVENT_STORE, handler, fetch_seconds=0.05)
    envelope = change()
    await bus.publish(SUBJECT, envelope)
    await runner.once()
    deliveries = await bus.fetch(EVENT_STORE, 1, 0.05)
    assert deliveries == []
    assert len(bus.stored(DEAD_LETTERS.name)) == 1
