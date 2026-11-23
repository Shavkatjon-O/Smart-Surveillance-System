"""Subject names and versioned event types of the bus.

Every subject starts with sss. A subject of a site carries the public ids of its organisation and site, so a
consumer or a later leaf node can filter by tenant with wildcards alone:

    sss.<organisation>.<site>.camera.<camera>.event
    sss.<organisation>.<site>.analytics.<view>.event
    sss.<organisation>.<site>.analytics.<view>.observation
    sss.<organisation>.<site>.live
    sss.<organisation>.alert
    sss.<organisation>.usage
    sss.connector.<connector>.telemetry
    sss.deadletter.<stream>.<consumer>
"""

from __future__ import annotations

import re

__all__ = [
    "ALERTS",
    "ANALYTICS_EVENTS",
    "CAMERA_EVENTS",
    "DEAD_LETTERS",
    "LIVE",
    "OBSERVATIONS",
    "TELEMETRY",
    "UNASSIGNED",
    "USAGE",
    "EventType",
    "alert",
    "analytics_event",
    "analytics_observation",
    "camera_event",
    "connector_telemetry",
    "dead_letter",
    "live",
    "matches",
    "token",
    "usage",
]

UNASSIGNED = "none"
INVALID = re.compile(r"[\s.*>]+")

CAMERA_EVENTS = "sss.*.*.camera.*.event"
ANALYTICS_EVENTS = "sss.*.*.analytics.*.event"
OBSERVATIONS = "sss.*.*.analytics.*.observation"
LIVE = "sss.*.*.live"
ALERTS = "sss.*.alert"
USAGE = "sss.*.usage"
TELEMETRY = "sss.connector.*.telemetry"
DEAD_LETTERS = "sss.deadletter.*.*"


class EventType:
    """The versioned event types; a type changes its version whenever its data changes incompatibly.

    Attributes:
        CAMERA_EVENT: A change of an event a camera reported over ONVIF.
        ANALYTICS_EVENT: A change of an event the analytics engine detected.
        EVENT_INTERRUPTED: The close of an event that a restart left open.
        OBSERVATION: The latest status of one analytics view.
        LIVE_EVENT: A stored event as the live stream shows it.
        LIVE_SOURCE: The state of a camera's event subscription.
        LIVE_SOURCE_REMOVED: A camera whose event subscription ended because it was removed.
        ALERT: An event that alerts, raised once.
        USAGE: Counted usage, such as notifications sent.
        CONNECTOR_TELEMETRY: Telemetry of a connector, for later.
        DEAD_LETTER: A message a consumer gave up on, with the reason.
    """

    CAMERA_EVENT = "sss.camera.event.v1"
    ANALYTICS_EVENT = "sss.analytics.event.v1"
    EVENT_INTERRUPTED = "sss.event.interrupted.v1"
    OBSERVATION = "sss.analytics.observation.v1"
    LIVE_EVENT = "sss.live.event.v1"
    LIVE_SOURCE = "sss.live.source.v1"
    LIVE_SOURCE_REMOVED = "sss.live.source-removed.v1"
    ALERT = "sss.alert.v1"
    USAGE = "sss.usage.v1"
    CONNECTOR_TELEMETRY = "sss.connector.telemetry.v1"
    DEAD_LETTER = "sss.deadletter.v1"


def token(value: object | None) -> str:
    """Turn a value into one subject token.

    Args:
        value: An id or a name; None or an empty value stands for an owner that is not assigned yet.

    Returns:
        The value as text without dots, wildcards or white space.
    """
    text = INVALID.sub("-", str(value).strip()) if value is not None else ""
    return text or UNASSIGNED


def camera_event(organisation: object | None, site: object | None, camera: object) -> str:
    """Name the subject of a camera's event changes.

    Args:
        organisation: Public id of the organisation.
        site: Public id of the site.
        camera: Public id of the camera.

    Returns:
        The subject.
    """
    return f"sss.{token(organisation)}.{token(site)}.camera.{token(camera)}.event"


def analytics_event(organisation: object | None, site: object | None, view: object) -> str:
    """Name the subject of an analytics view's event changes.

    Args:
        organisation: Public id of the organisation.
        site: Public id of the site.
        view: Public id of the view.

    Returns:
        The subject.
    """
    return f"sss.{token(organisation)}.{token(site)}.analytics.{token(view)}.event"


def analytics_observation(organisation: object | None, site: object | None, view: object) -> str:
    """Name the subject of an analytics view's observations.

    Args:
        organisation: Public id of the organisation.
        site: Public id of the site.
        view: Public id of the view.

    Returns:
        The subject.
    """
    return f"sss.{token(organisation)}.{token(site)}.analytics.{token(view)}.observation"


def live(organisation: object | None, site: object | None) -> str:
    """Name the subject a site's live stream messages fan out on.

    Args:
        organisation: Public id of the organisation.
        site: Public id of the site.

    Returns:
        The subject.
    """
    return f"sss.{token(organisation)}.{token(site)}.live"


def alert(organisation: object | None) -> str:
    """Name the subject of an organisation's alerts.

    Args:
        organisation: Public id of the organisation.

    Returns:
        The subject.
    """
    return f"sss.{token(organisation)}.alert"


def usage(organisation: object | None) -> str:
    """Name the subject of an organisation's counted usage.

    Args:
        organisation: Public id of the organisation.

    Returns:
        The subject.
    """
    return f"sss.{token(organisation)}.usage"


def connector_telemetry(connector: object) -> str:
    """Name the subject of a connector's telemetry.

    Args:
        connector: Public id of the connector.

    Returns:
        The subject.
    """
    return f"sss.connector.{token(connector)}.telemetry"


def dead_letter(stream: str, consumer: str) -> str:
    """Name the subject a consumer's dead letters go to.

    Args:
        stream: Stream the message came from.
        consumer: Durable consumer that gave up on it.

    Returns:
        The subject.
    """
    return f"sss.deadletter.{token(stream)}.{token(consumer)}"


def matches(pattern: str, subject: str) -> bool:
    """Tell whether a subject matches a pattern with the * and > wildcards.

    Args:
        pattern: Pattern, such as sss.*.alert or sss.deadletter.>.
        subject: A subject without wildcards.

    Returns:
        True when it matches.
    """
    wanted = pattern.split(".")
    given = subject.split(".")
    for index, part in enumerate(wanted):
        if part == ">":
            return len(given) > index
        if index >= len(given):
            return False
        if part != "*" and part != given[index]:
            return False
    return len(given) == len(wanted)
