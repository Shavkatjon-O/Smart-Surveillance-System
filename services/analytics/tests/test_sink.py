import asyncio
import time

from event_bus import EventType, MemoryBus, provision, subjects
from event_bus.streams import EVENTS, OBSERVATIONS

from app.sink import EventSink

EVENT = {
    "id": "7c3a59c4-6f39-4e0e-9a3b-3d0c3e0f3a11",
    "action": "open",
    "camera": "0f8f2c1e-1111-4222-8333-944455556666",
    "view": "1a2b3c4d-1111-4222-8333-944455556666",
    "kind": "intrusion",
    "label": "person",
    "started_at": 1763892000.0,
    "alert": True,
}


class FakeOutbox:
    def __init__(self):
        self.sent = []

    def send(self, path, body, droppable=False):
        self.sent.append((path, body, droppable))


def wait_for(condition, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def memory_sink(online=True):
    bus = MemoryBus()
    if online:
        asyncio.run(provision(bus))
    bus.online = online
    sink = EventSink("", FakeOutbox(), factory=lambda: bus)
    sink.start()
    return sink, bus


def test_an_event_change_is_published_on_its_view_subject_with_its_owner():
    sink, bus = memory_sink()
    sink.event(EVENT, ("org-1", "site-1"))
    assert wait_for(lambda: bus.published)
    sink.stop()
    subject, envelope = bus.published[0]
    assert subject == subjects.analytics_event("org-1", "site-1", EVENT["view"])
    assert envelope.type == EventType.ANALYTICS_EVENT
    assert envelope.subject == EVENT["id"]
    assert envelope.organisation == "org-1" and envelope.site == "site-1"
    assert envelope.data == EVENT
    assert sink.outbox.sent == []


def test_every_view_of_a_report_becomes_one_observation():
    sink, bus = memory_sink()
    body = {"at": 1.0, "pipeline": {"state": "running"}, "gpu": {}, "views": [{"id": "a"}, {"id": "b"}]}
    sink.observations(body, lambda view: ("o", f"site-{view}"))
    assert wait_for(lambda: len(bus.stored(OBSERVATIONS.name)) == 2)
    sink.stop()
    first, second = bus.stored(OBSERVATIONS.name)
    assert first.data["view"] == {"id": "a"} and first.site == "site-a"
    assert second.data["pipeline"] == {"state": "running"}


def test_changes_wait_in_the_buffer_while_the_bus_is_away_and_go_out_in_order():
    sink, bus = memory_sink(online=False)
    for action in ("open", "update", "close"):
        sink.event({**EVENT, "action": action}, ("o", "s"))
    sink.observations({"at": 1.0, "pipeline": None, "gpu": {}, "views": [{"id": "a"}]}, lambda view: ("o", "s"))
    assert wait_for(lambda: sink.status()["buffered"] == 3 and sink.status()["waiting"] == 0)
    assert sink.status()["dropped"] == 1
    bus.online = True
    asyncio.run(provision(bus))
    assert wait_for(lambda: len(bus.published) == 3, seconds=15)
    sink.stop()
    assert [envelope.data["action"] for envelope in bus.stored(EVENTS.name)] == ["open", "update", "close"]
    assert bus.stored(OBSERVATIONS.name) == []


def test_without_a_bus_the_backend_receives_changes_and_observations():
    outbox = FakeOutbox()
    sink = EventSink("", outbox)
    sink.event(EVENT, (None, None))
    sink.observations({"at": 1.0, "pipeline": None, "gpu": {}, "views": []}, lambda view: (None, None))
    assert [path for path, _, _ in outbox.sent] == ["/internal/analytics/events", "/internal/analytics/observations"]
    assert outbox.sent[1][2] is True
    assert sink.status() is None


def test_running_from_a_file_sends_nothing():
    sink = EventSink("", None)
    assert not sink.active
    sink.event(EVENT, (None, None))
