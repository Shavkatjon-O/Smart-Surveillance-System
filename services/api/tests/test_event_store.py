from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models import Event
from app.modules.events.services import EventStoreService, InvalidChange
from event_bus import Envelope, EventType
from tests.bus_fakes import StoreSession

CAMERA = uuid4()
VIEW = uuid4()


def analytics(**fields):
    data = {
        "id": str(fields.pop("id", uuid4())),
        "action": "open",
        "camera": str(CAMERA),
        "view": str(VIEW),
        "view_name": "Gate",
        "kind": "intrusion",
        "label": "person",
        "started_at": 1763892000.0,
        "zone": "z1",
        "zone_name": "Entrance",
        "objects": [4],
        "count": 1,
        "alert": False,
        **fields,
    }
    return Envelope.create(EventType.ANALYTICS_EVENT, "/sss/test", data)


def camera(**fields):
    data = {
        "id": str(fields.pop("id", uuid4())),
        "action": "open",
        "camera": str(CAMERA),
        "kind": "motion",
        "source_topic": "tns1:VideoSource/MotionAlarm",
        "alert": False,
        "started_at": "2026-11-23T10:00:00+00:00",
        "details": {"messages": [{"topic": "motion"}]},
        **fields,
    }
    return Envelope.create(EventType.CAMERA_EVENT, "/sss/test", data)


def stored(**fields):
    values = {
        "id": 7,
        "uuid": uuid4(),
        "camera_uuid": CAMERA,
        "kind": "intrusion",
        "source": "analytics",
        "source_topic": "analytics/intrusion",
        "label": "person",
        "alert": False,
        "view_uuid": VIEW,
        "started_at": datetime(2026, 11, 23, 10, tzinfo=UTC),
        "details": {"zone": "z1", "plates": [{"text": "01A123BC"}]},
        "site_id": 3,
        "created_at": datetime(2026, 11, 23, 10, tzinfo=UTC),
        "updated_at": datetime(2026, 11, 23, 10, tzinfo=UTC),
        **fields,
    }
    return Event(**values)


async def test_a_first_analytics_change_creates_the_event():
    session = StoreSession(scalar=[None, "Gate camera"])
    result = await EventStoreService(session).apply(analytics(alert=True))
    (row,) = session.added
    assert row.source == "analytics" and row.source_topic == "analytics/intrusion"
    assert row.details["zone_name"] == "Entrance" and row.details["boxes"] == []
    assert session.commits == 1
    assert result.event.camera_name == "Gate camera"
    assert result.raised is True


async def test_a_later_change_merges_and_never_turns_the_alert_off():
    row = stored(alert=True)
    session = StoreSession(scalar=[row, "Gate camera"])
    result = await EventStoreService(session).apply(
        analytics(id=row.uuid, action="close", ended_at=1763892060.0, alert=False, count=2)
    )
    assert row.alert is True
    assert row.ended_at == datetime.fromtimestamp(1763892060.0, UTC)
    assert row.details["count"] == 2
    assert row.details["plates"] == [{"text": "01A123BC"}]
    assert result.raised is False
    assert result.site_id == 3


async def test_media_paths_are_added_and_never_removed():
    row = stored(snapshot_path="analytics/2026/11/23/old.jpg")
    session = StoreSession(scalar=[row, "Gate"])
    path = f"analytics/2026/11/23/{row.uuid}.mp4"
    await EventStoreService(session).apply(analytics(id=row.uuid, action="media", clip_path=path))
    assert row.clip_path == path
    assert row.snapshot_path == "analytics/2026/11/23/old.jpg"


async def test_applying_the_same_change_twice_leaves_the_same_event():
    row = stored()
    change = analytics(id=row.uuid, action="update", count=3, alert=True)
    session = StoreSession(scalar=[row, "Gate", row, "Gate"])
    first = await EventStoreService(session).apply(change)
    details = dict(row.details)
    second = await EventStoreService(session).apply(change)
    assert row.details == details
    assert first.raised is True and second.raised is False


async def test_a_camera_change_creates_its_event_and_a_close_merges_into_it():
    session = StoreSession(scalar=[None, "Yard"])
    opened = camera(alert=True)
    await EventStoreService(session).apply(opened)
    (row,) = session.added
    assert row.source == "camera" and row.details["messages"]
    closing = camera(
        id=opened.data["id"], action="close", ended_at="2026-11-23T10:00:30+00:00", details={"topics": ["a"]}
    )
    session.scalar_results = [row, "Yard"]
    await EventStoreService(session).apply(closing)
    assert row.ended_at == datetime(2026, 11, 23, 10, 0, 30, tzinfo=UTC)
    assert row.details["topics"] == ["a"] and row.details["messages"]


async def test_an_interrupted_close_applies_only_to_an_open_event():
    open_row = stored()
    session = StoreSession(scalar=[open_row, "Gate"])
    data = {"id": str(open_row.uuid), "ended_at": "2026-11-23T10:05:00+00:00"}
    result = await EventStoreService(session).apply(Envelope.create(EventType.EVENT_INTERRUPTED, "/t", data))
    assert open_row.details["interrupted"] is True
    assert result is not None
    closed = stored(ended_at=datetime(2026, 11, 23, 10, 1, tzinfo=UTC))
    session = StoreSession(scalar=[closed])
    data = {"id": str(closed.uuid), "ended_at": "2026-11-23T10:05:00+00:00"}
    assert await EventStoreService(session).apply(Envelope.create(EventType.EVENT_INTERRUPTED, "/t", data)) is None
    assert closed.ended_at == datetime(2026, 11, 23, 10, 1, tzinfo=UTC)
    assert "interrupted" not in closed.details


async def test_invalid_changes_are_refused_for_good():
    with pytest.raises(InvalidChange):
        await EventStoreService(StoreSession()).apply(Envelope.create(EventType.USAGE, "/t", {}))
    with pytest.raises(InvalidChange):
        await EventStoreService(StoreSession()).apply(analytics(kind="no-such-kind"))
