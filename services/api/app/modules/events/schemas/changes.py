"""Schemas of the event changes that travel on the bus to the event store."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.events.enums import EventKind

__all__ = ["MEDIA_PATH", "AnalyticsEventChange", "CameraEventChange", "EventInterrupted"]

MEDIA_PATH = r"^analytics/\d{4}/\d{2}/\d{2}/[0-9a-f-]{36}(\.thumb)?\.(jpg|mp4)$"


class AnalyticsEventChange(BaseModel):
    """A change of an analytics event, the data of sss.analytics.event.v1.

    Every change carries the whole event, so a lost change is repaired by the next one.

    Attributes:
        id: Event id, shared by every change of the same event.
        action: open, update, close, instant or media.
        camera: Camera id.
        view: View id.
        view_name: View name.
        profile: Profile of the view.
        kind: Event kind.
        label: Object label or tamper condition.
        started_at: Start, seconds since the epoch.
        ended_at: End, None while open.
        zone: Zone id, if any.
        zone_name: Zone name, if any.
        line: Line id, if any.
        line_name: Line name, if any.
        direction: in or out for crossings.
        objects: Object ids involved.
        boxes: Boxes of the objects when they joined.
        count: Largest number of objects at once.
        alert: Whether the event raises an alert.
        plates: Number plate candidates of a vehicle event, strongest first, None when not read.
        plate_status: read, infrared, no_plate, too_small, unreadable or pending, None when not read.
        snapshot_path: Snapshot relative to the events folder.
        thumbnail_path: Thumbnail relative to the events folder.
        clip_path: Clip relative to the events folder.
    """

    id: UUID
    action: str = Field(max_length=10)
    camera: UUID
    view: UUID
    view_name: str = Field("", max_length=100)
    profile: str = Field("", max_length=20)
    kind: EventKind
    label: str = Field(max_length=20)
    started_at: float
    ended_at: float | None = None
    zone: str | None = Field(None, max_length=64)
    zone_name: str | None = Field(None, max_length=60)
    line: str | None = Field(None, max_length=64)
    line_name: str | None = Field(None, max_length=60)
    direction: str | None = Field(None, max_length=5)
    objects: list[int] = Field(default_factory=list, max_length=200)
    boxes: list[dict] = Field(default_factory=list, max_length=20)
    count: int = 0
    alert: bool = False
    plates: list[dict] | None = Field(None, max_length=5)
    plate_status: str | None = Field(None, max_length=20)
    snapshot_path: str | None = Field(None, pattern=MEDIA_PATH)
    thumbnail_path: str | None = Field(None, pattern=MEDIA_PATH)
    clip_path: str | None = Field(None, pattern=MEDIA_PATH)


class CameraEventChange(BaseModel):
    """A change of an event a camera reported over ONVIF, the data of sss.camera.event.v1.

    Every change carries the whole event as the subscriber knows it, so the event store creates the event from
    whichever change arrives first.

    Attributes:
        id: Event id, shared by every change of the same event.
        action: open, instant, close or snapshot.
        camera: Camera id.
        kind: Event kind.
        source_topic: ONVIF topic that caused the event.
        alert: Whether the event raises an alert.
        started_at: Start by the server's clock.
        ended_at: End, None while open.
        camera_time: The camera's own time of the event, if it sent one.
        details: The messages and the camera's clock offset, merged into what is stored.
        snapshot_path: Snapshot relative to the events folder.
        thumbnail_path: Thumbnail relative to the events folder.
    """

    id: UUID
    action: str = Field(max_length=10)
    camera: UUID
    kind: EventKind
    source_topic: str = Field(max_length=255)
    alert: bool = False
    started_at: datetime
    ended_at: datetime | None = None
    camera_time: datetime | None = None
    details: dict = Field(default_factory=dict)
    snapshot_path: str | None = Field(None, max_length=255)
    thumbnail_path: str | None = Field(None, max_length=255)


class EventInterrupted(BaseModel):
    """The close of an event a restart left open, the data of sss.event.interrupted.v1.

    Attributes:
        id: Event id.
        ended_at: End to record while the event is still open.
    """

    id: UUID
    ended_at: datetime
