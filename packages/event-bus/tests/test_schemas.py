import json
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from event_bus import Envelope, EventType

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "events"
CHECKER = FormatChecker()


def validator(name):
    schema = json.loads((SCHEMAS / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=CHECKER)


def event_read():
    return {
        "uuid": str(uuid4()),
        "camera_uuid": str(uuid4()),
        "camera_name": "Gate",
        "kind": "person",
        "source": "analytics",
        "source_topic": "analytics/person",
        "label": "person",
        "alert": True,
        "view_uuid": str(uuid4()),
        "started_at": "2026-11-23T10:00:00+00:00",
        "ended_at": None,
        "ongoing": True,
        "duration_seconds": 1.5,
        "camera_time": None,
        "has_snapshot": False,
        "has_thumbnail": False,
        "has_clip": False,
        "details": {},
        "created_at": "2026-11-23T10:00:00+00:00",
        "updated_at": "2026-11-23T10:00:00+00:00",
        "face": None,
        "acknowledged_at": None,
    }


SAMPLES = {
    EventType.CAMERA_EVENT: {
        "id": str(uuid4()),
        "action": "open",
        "camera": str(uuid4()),
        "kind": "motion",
        "source_topic": "tns1:RuleEngine/CellMotionDetector/Motion",
        "alert": False,
        "started_at": "2026-11-23T10:00:00+00:00",
        "ended_at": None,
        "camera_time": None,
        "details": {"messages": []},
        "snapshot_path": None,
        "thumbnail_path": None,
    },
    EventType.ANALYTICS_EVENT: {
        "id": str(uuid4()),
        "action": "open",
        "camera": str(uuid4()),
        "view": str(uuid4()),
        "kind": "intrusion",
        "label": "person",
        "started_at": 1763892000.5,
        "alert": True,
        "snapshot_path": "analytics/2026/11/23/7c3a59c4-6f39-4e0e-9a3b-3d0c3e0f3a11.jpg",
    },
    EventType.EVENT_INTERRUPTED: {"id": str(uuid4()), "ended_at": "2026-11-23T10:00:00+00:00"},
    EventType.OBSERVATION: {
        "at": 1763892000.5,
        "pipeline": {"state": "running"},
        "gpu": {},
        "view": {"id": str(uuid4())},
    },
    EventType.LIVE_EVENT: event_read(),
    EventType.LIVE_SOURCE: {"camera_uuid": str(uuid4()), "state": "connected", "topics": [], "kinds": []},
    EventType.LIVE_SOURCE_REMOVED: {"camera_uuid": str(uuid4())},
    EventType.ALERT: event_read(),
    EventType.USAGE: {"metric": "notifications", "amount": 2, "at": "2026-11-23T10:00:00+00:00", "channel": "telegram"},
    EventType.CONNECTOR_TELEMETRY: {"connector": str(uuid4()), "at": "2026-11-23T10:00:00+00:00", "readings": {}},
    EventType.DEAD_LETTER: {
        "stream": "SSS_EVENTS",
        "consumer": "event-store",
        "subject": "sss.o.s.camera.c.event",
        "attempts": 9,
        "reason": "OperationalError",
    },
}


@pytest.mark.parametrize("type_name", sorted(SAMPLES))
def test_envelopes_of_every_type_match_their_schemas(type_name):
    envelope = Envelope.create(type_name, "/sss/test", SAMPLES[type_name], organisation=uuid4(), site=uuid4())
    body = json.loads(envelope.to_bytes())
    validator("envelope.schema.json").validate(body)
    validator(f"{type_name}.schema.json").validate(body["data"])


def test_the_envelope_schema_refuses_unknown_attributes():
    body = Envelope.create(EventType.USAGE, "/sss/test", {}).to_dict()
    body["extra"] = 1
    assert list(validator("envelope.schema.json").iter_errors(body))


def test_a_change_without_its_event_id_is_refused():
    data = dict(SAMPLES[EventType.ANALYTICS_EVENT])
    del data["id"]
    assert list(validator(f"{EventType.ANALYTICS_EVENT}.schema.json").iter_errors(data))
