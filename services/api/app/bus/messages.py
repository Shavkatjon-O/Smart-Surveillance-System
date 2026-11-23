"""Envelopes the API sends on the bus: live stream messages, alerts and the announcement of stored events."""

import logging

from app.bus.connection import bus
from app.bus.owners import owners
from event_bus import Envelope, EventType, stable_id, subjects

__all__ = [
    "LIVE_KINDS",
    "LIVE_TYPES",
    "SOURCE",
    "alert_envelope",
    "announce",
    "announce_event",
    "live_envelope",
]

logger = logging.getLogger(__name__)

SOURCE = "/sss/api"

LIVE_TYPES = {
    "event": EventType.LIVE_EVENT,
    "source": EventType.LIVE_SOURCE,
    "source_removed": EventType.LIVE_SOURCE_REMOVED,
}
LIVE_KINDS = {value: key for key, value in LIVE_TYPES.items()}


def live_envelope(kind: str, data: dict, organisation: str | None, site: str | None, source: str = SOURCE) -> Envelope:
    """Wrap a live stream message.

    Args:
        kind: Message type of the live stream: event, source or source_removed.
        data: The message data, such as an event in its API form.
        organisation: Public id of the organisation.
        site: Public id of the site.
        source: The producer.

    Returns:
        The envelope.
    """
    subject = data.get("uuid") or data.get("camera_uuid")
    return Envelope.create(LIVE_TYPES[kind], source, data, subject=subject, organisation=organisation, site=site)


def alert_envelope(event: dict, organisation: str | None, site: str | None, source: str = SOURCE) -> Envelope:
    """Wrap the alert of an event, with an id derived from the event so the alert stream keeps one per event.

    Args:
        event: The event in its API form.
        organisation: Public id of the organisation.
        site: Public id of the site.
        source: The producer.

    Returns:
        The envelope.
    """
    return Envelope.create(
        EventType.ALERT,
        source,
        event,
        subject=event["uuid"],
        organisation=organisation,
        site=site,
        id=stable_id("alert", event["uuid"]),
    )


async def announce(kind: str, data: dict, organisation: str | None, site: str | None, source: str = SOURCE) -> None:
    """Send a live stream message to every API replica and raise the alert of an alerting event.

    The live message is fan-out only; the alert goes through the publisher's buffer so a short outage of the bus
    does not lose it, and its id keeps it to one alert per event.

    Args:
        kind: Message type of the live stream.
        data: The message data.
        organisation: Public id of the organisation.
        site: Public id of the site.
        source: The producer.
    """
    await bus.broadcast(subjects.live(organisation, site), live_envelope(kind, data, organisation, site, source))
    if kind == "event" and data.get("alert") and data.get("uuid"):
        await bus.emit(subjects.alert(organisation), alert_envelope(data, organisation, site, source))


async def announce_event(kind: str, data: dict) -> None:
    """Announce a message about a camera, looking up the camera's organisation and site.

    Args:
        kind: Message type of the live stream.
        data: The message data, with camera_uuid.
    """
    try:
        organisation, site = await owners.of_camera(data["camera_uuid"])
    except Exception:
        logger.exception("Looking up the owner of camera %s failed", data.get("camera_uuid"))
        organisation, site = None, None
    await announce(kind, data, organisation, site)
