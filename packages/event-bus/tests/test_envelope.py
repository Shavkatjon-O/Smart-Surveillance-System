import json
from uuid import UUID

import pytest

from event_bus import Envelope, EnvelopeError, EventType, stable_id


def test_an_envelope_survives_its_json_form():
    envelope = Envelope.create(
        EventType.ANALYTICS_EVENT, "/sss/analytics", {"id": "x"}, subject="event", organisation="o", site="s"
    )
    again = Envelope.from_bytes(envelope.to_bytes())
    assert again == envelope
    assert UUID(envelope.id)


def test_optional_attributes_are_left_out_when_unset():
    body = Envelope.create(EventType.USAGE, "/sss/notifier", {}).to_dict()
    assert set(body) == {"specversion", "id", "source", "type", "time", "datacontenttype", "data"}
    assert body["specversion"] == "1.0"
    assert body["datacontenttype"] == "application/json"


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"specversion": "1.0", "source": "/x", "type": "t", "data": {}},
        {"specversion": "0.3", "id": "7c3a59c4-6f39-4e0e-9a3b-3d0c3e0f3a11", "source": "/x", "type": "t"},
        {"specversion": "1.0", "id": "not-a-uuid", "source": "/x", "type": "t"},
        {"specversion": "1.0", "id": "7c3a59c4-6f39-4e0e-9a3b-3d0c3e0f3a11", "source": "/x", "type": "t", "data": []},
    ],
)
def test_invalid_envelopes_are_refused(body):
    with pytest.raises(EnvelopeError):
        Envelope.from_dict(body)


def test_a_payload_that_is_not_json_is_refused():
    with pytest.raises(EnvelopeError):
        Envelope.from_bytes(b"\xff not json")


def test_stable_ids_repeat_for_the_same_parts_only():
    assert stable_id("alert", "a") == stable_id("alert", "a")
    assert stable_id("alert", "a") != stable_id("alert", "b")
    assert UUID(stable_id("alert", "a")).version == 5


def test_the_json_form_is_compact():
    envelope = Envelope.create(EventType.USAGE, "/sss/notifier", {"amount": 1})
    assert b" " not in envelope.to_bytes().replace(b"/sss/notifier", b"")
    assert json.loads(envelope.to_bytes())["data"] == {"amount": 1}
