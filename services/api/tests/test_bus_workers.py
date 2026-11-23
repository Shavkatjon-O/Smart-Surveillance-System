from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.bus.messages import alert_envelope, announce, live_envelope
from app.bus.relay import LiveRelay
from app.camera_events.broker import EventBroker
from app.core.exceptions import Unavailable
from app.modules.alerting.services import ChannelResult
from app.modules.analytics.schemas import AnalyticsEventIn, ObservationReport
from app.modules.analytics.services import AnalyticsIngestService
from app.workers.event_store import EventStoreWorker
from app.workers.metering import MeteringWorker
from app.workers.notifier import ChannelWorker
from event_bus import Envelope, EventType, Reject, Retry, stable_id, subjects
from event_bus.streams import ALERTS, EVENTS, NOTIFIER_TELEGRAM, OBSERVATIONS, USAGE
from tests.bus_fakes import Owners, StoreSession, memory_connection, sessions_of
from tests.test_event_store import analytics, stored

pytestmark = pytest.mark.asyncio


class Attempt:
    def __init__(self, attempt=1):
        self.attempt = attempt


def event_data(alert=True):
    return {
        "uuid": str(uuid4()),
        "camera_uuid": str(uuid4()),
        "kind": "intrusion",
        "source": "analytics",
        "alert": alert,
        "started_at": "2026-11-23T10:00:00+00:00",
        "ongoing": True,
    }


async def test_the_event_store_announces_the_stored_event_and_raises_its_alert_once():
    connection, bus = await memory_connection()
    session = StoreSession(scalar=[None, "Gate"])
    worker = EventStoreWorker(connection, sessions_of(session), Owners())
    live = []

    async def listener(envelope):
        live.append(envelope)

    await bus.listen(subjects.LIVE, listener)
    change = analytics(alert=True)
    await worker.handle(change, Attempt())
    assert [envelope.type for envelope in live] == [EventType.LIVE_EVENT]
    assert live[0].data["uuid"] == change.data["id"]
    (alert,) = bus.stored(ALERTS.name)
    assert alert.id == stable_id("alert", change.data["id"])
    assert alert.organisation == "org-1" and alert.site == "site-1"


async def test_a_redelivered_change_of_an_alerting_event_raises_the_alert_again_but_it_is_kept_once():
    connection, bus = await memory_connection()
    row = stored(alert=True)
    session = StoreSession(scalar=[row, "Gate", row, "Gate"])
    worker = EventStoreWorker(connection, sessions_of(session), Owners())
    change = analytics(id=row.uuid, alert=True)
    await worker.handle(change, Attempt(1))
    assert bus.stored(ALERTS.name) == []
    await worker.handle(change, Attempt(2))
    await worker.handle(change, Attempt(3))
    assert len(bus.stored(ALERTS.name)) == 1


async def test_the_event_store_rejects_a_change_it_can_never_store():
    connection, _ = await memory_connection()
    worker = EventStoreWorker(connection, sessions_of(StoreSession()), Owners())
    with pytest.raises(Reject):
        await worker.handle(analytics(kind="no-such-kind"), Attempt())


class FlakyChannel:
    name = "telegram"

    def __init__(self, recipients, failing_attempts=1):
        self.recipients = recipients
        self.failing_attempts = failing_attempts
        self.calls = 0
        self.sent_to = []

    async def send(self, event, done):
        self.calls += 1
        sent = pending = 0
        for recipient in self.recipients:
            if recipient in done:
                continue
            if recipient == "flaky" and self.calls <= self.failing_attempts:
                pending += 1
                continue
            done.add(recipient)
            self.sent_to.append(recipient)
            sent += 1
        return ChannelResult(sent=sent, pending=pending)


async def test_a_channel_retries_only_the_recipients_that_failed_and_reports_what_it_sent():
    connection, bus = await memory_connection()
    channel = FlakyChannel(["good", "flaky"])
    worker = ChannelWorker(channel, NOTIFIER_TELEGRAM, connection)
    alert = alert_envelope(event_data(), "org-1", "site-1")
    with pytest.raises(Retry):
        await worker.handle(alert, Attempt(1))
    await worker.handle(alert, Attempt(2))
    assert channel.sent_to == ["good", "flaky"]
    usage = bus.stored(USAGE.name)
    assert [item.data["amount"] for item in usage] == [1, 1]
    assert {item.data["channel"] for item in usage} == {"telegram"}
    assert usage[0].organisation == "org-1"


async def test_a_channel_ignores_messages_that_are_not_alerts():
    connection, bus = await memory_connection()
    channel = FlakyChannel(["good"])
    await ChannelWorker(channel, NOTIFIER_TELEGRAM, connection).handle(
        Envelope.create(EventType.USAGE, "/t", {}), Attempt()
    )
    assert channel.calls == 0


async def test_metering_refuses_invalid_usage():
    worker = MeteringWorker(sessions_of(StoreSession()))
    with pytest.raises(Reject):
        await worker.handle(Envelope.create(EventType.USAGE, "/t", {"metric": "nothing", "amount": 1}), Attempt())


async def test_the_intake_publishes_a_posted_change_on_its_view_subject():
    connection, bus = await memory_connection()
    service = AnalyticsIngestService(connection, Owners().of_camera)
    data = AnalyticsEventIn.model_validate(analytics(alert=True).data)
    await service.event(data)
    (envelope,) = bus.stored(EVENTS.name)
    subject, _ = bus.published[-1]
    assert subject == subjects.analytics_event("org-1", "site-1", data.view)
    assert envelope.type == EventType.ANALYTICS_EVENT and envelope.data["id"] == str(data.id)


async def test_the_intake_publishes_one_observation_per_view():
    connection, bus = await memory_connection()
    service = AnalyticsIngestService(connection, Owners().of_camera)
    views = [{"id": "v1", "camera": str(uuid4()), "state": "live"}, {"id": "v2", "camera": str(uuid4())}]
    await service.observe(ObservationReport(at=1.0, pipeline={"state": "running"}, gpu={}, views=views))
    assert [envelope.data["view"]["id"] for envelope in bus.stored(OBSERVATIONS.name)] == ["v1", "v2"]


async def test_the_intake_answers_unavailable_while_the_bus_is_away():
    connection, bus = await memory_connection()
    await bus.set_online(False)
    service = AnalyticsIngestService(connection, Owners().of_camera)
    with pytest.raises(Unavailable):
        await service.event(AnalyticsEventIn.model_validate(analytics().data))


async def test_the_live_relay_feeds_this_replicas_streams_and_observations():
    from app.analytics.observations import Observations

    connection, bus = await memory_connection()
    broker = EventBroker()
    reported = Observations()
    relay = LiveRelay(broker, connection, reported)
    await relay.start()
    with broker.subscribe() as queue:
        data = event_data(alert=False)
        await bus.broadcast(subjects.live("o", "s"), live_envelope("event", data, "o", "s"))
        await bus.broadcast(subjects.live("o", "s"), live_envelope("source_removed", {"camera_uuid": "c"}, "o", "s"))
        assert queue.get_nowait() == {"type": "event", "data": data}
        assert queue.get_nowait() == {"type": "source_removed", "data": {"camera_uuid": "c"}}
        observation = Envelope.create(
            EventType.OBSERVATION, "/t", {"at": 1.0, "pipeline": None, "gpu": {}, "view": {"id": "v", "fps": 7.5}}
        )
        await bus.publish(subjects.analytics_observation("o", "s", "v"), observation)
        assert reported.view("v")["fps"] == 7.5
        await bus.set_online(False)
        await bus.set_online(True)
        assert queue.get_nowait() == {"type": "overflow", "data": {}}
    await relay.stop()


async def test_announce_raises_an_alert_only_for_an_alerting_event(monkeypatch):
    from app.bus import messages

    connection, bus = await memory_connection()
    monkeypatch.setattr(messages, "bus", connection)
    await announce("event", event_data(alert=False), "o", "s")
    assert bus.stored(ALERTS.name) == []
    data = event_data(alert=True)
    await announce("event", data, "o", "s")
    await announce("event", data, "o", "s")
    assert len(bus.stored(ALERTS.name)) == 1
    assert [subject for subject, _ in bus.published].count(subjects.live("o", "s")) == 3


async def test_the_camera_engine_publishes_whole_changes_on_the_camera_subject():
    from app.camera_events.engine import CameraEventsEngine
    from app.camera_events.tracker import Closing, OpenEvent
    from camera_adapters.onvif.notifications import Notification

    connection, bus = await memory_connection()
    engine = CameraEventsEngine(None, EventBroker(), 30, 400, connection)
    camera = uuid4()
    engine.owners[camera] = ("o", "s")
    opened = OpenEvent(camera_uuid=camera, kind="motion", topic="tns1:Motion", started_at=datetime.now(UTC))
    message = Notification(topic="tns1:Motion", operation="Changed", source={}, data={"State": "true"})
    await engine.create_open(opened, message, None)
    await engine.close([Closing(event=opened, ended_at=datetime.now(UTC), capped=True)])
    for task in list(engine.background):
        task.cancel()
    first, second = bus.stored(EVENTS.name)
    assert bus.published[0][0] == subjects.camera_event("o", "s", camera)
    assert first.data["action"] == "open" and first.data["alert"] is False
    assert second.data["action"] == "close" and second.data["details"]["capped"] is True
    assert second.data["details"]["messages"] == first.data["details"]["messages"]
    assert second.data["started_at"] == first.data["started_at"]
