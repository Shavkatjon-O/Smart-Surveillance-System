from pathlib import Path

import pytest

from event_bus import STREAMS, EventType, subjects
from event_bus.memory import overlap

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "events"


def test_site_subjects_carry_organisation_and_site():
    assert subjects.camera_event("o", "s", "c") == "sss.o.s.camera.c.event"
    assert subjects.analytics_event("o", "s", "v") == "sss.o.s.analytics.v.event"
    assert subjects.analytics_observation("o", "s", "v") == "sss.o.s.analytics.v.observation"
    assert subjects.live("o", "s") == "sss.o.s.live"
    assert subjects.alert("o") == "sss.o.alert"
    assert subjects.usage("o") == "sss.o.usage"
    assert subjects.connector_telemetry("c") == "sss.connector.c.telemetry"
    assert subjects.dead_letter("SSS_EVENTS", "event-store") == "sss.deadletter.SSS_EVENTS.event-store"


@pytest.mark.parametrize(("value", "expected"), [(None, "none"), ("", "none"), ("a.b", "a-b"), ("a*>b c", "a-b-c")])
def test_tokens_never_split_or_widen_a_subject(value, expected):
    assert subjects.token(value) == expected


@pytest.mark.parametrize(
    ("pattern", "subject", "expected"),
    [
        ("sss.*.alert", "sss.o.alert", True),
        ("sss.*.alert", "sss.o.s.alert", False),
        ("sss.*.*.camera.*.event", "sss.o.s.camera.c.event", True),
        ("sss.deadletter.>", "sss.deadletter.a.b", True),
        ("sss.deadletter.>", "sss.deadletter", False),
        ("sss.*.*.live", "sss.o.s.live", True),
    ],
)
def test_wildcards_match_as_nats_does(pattern, subject, expected):
    assert subjects.matches(pattern, subject) is expected


def test_no_two_streams_capture_the_same_subject():
    for index, first in enumerate(STREAMS):
        for second in STREAMS[index + 1 :]:
            for left in first.subjects:
                for right in second.subjects:
                    assert not overlap(left, right), (first.name, second.name)


def test_no_stream_captures_the_live_fan_out():
    live = subjects.live("o", "s")
    assert not any(subjects.matches(pattern, live) for stream in STREAMS for pattern in stream.subjects)


def test_every_event_type_has_a_schema():
    types = [value for name, value in vars(EventType).items() if name.isupper()]
    assert types
    for type_name in types:
        assert (SCHEMAS / f"{type_name}.schema.json").is_file(), type_name
