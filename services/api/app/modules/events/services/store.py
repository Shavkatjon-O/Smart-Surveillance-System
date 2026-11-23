"""The event store: every event change from the bus becomes a stored event, idempotently by the event's id."""

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Camera, Event
from app.modules.events.enums import EventSource
from app.modules.events.schemas import (
    AnalyticsEventChange,
    CameraEventChange,
    EventInterrupted,
    EventRead,
    event_read,
)
from event_bus import Envelope, EventType

__all__ = ["DETAIL_FIELDS", "LATER_FIELDS", "EventStoreService", "InvalidChange", "StoredChange", "moment"]

DETAIL_FIELDS = ("view_name", "profile", "zone", "zone_name", "line", "line_name", "direction", "objects", "count")
LATER_FIELDS = ("plates", "plate_status")
MEDIA_FIELDS = ("snapshot_path", "thumbnail_path", "clip_path")


class InvalidChange(ValueError):
    """A change on the bus can never be stored, such as one of an unknown type or with invalid data."""


@dataclass(frozen=True)
class StoredChange:
    """What storing one change produced.

    Attributes:
        event: The stored event in its API form.
        raised: True when this change turned the event's alert on.
        site_id: Database id of the event's site, None for an event without one.
    """

    event: EventRead
    raised: bool
    site_id: int | None


def moment(value: float | None) -> datetime | None:
    """Convert epoch seconds to an aware time.

    Args:
        value: Seconds since the epoch, or None.

    Returns:
        The time in UTC, or None.
    """
    return datetime.fromtimestamp(value, UTC) if value is not None else None


class EventStoreService:
    """Applies event changes to the events table.

    Every change carries its whole event, so applying a change twice, or a later one before an earlier one, leaves
    the same event: the alert flag only ever turns on, an end once set is only replaced by another end, media paths
    are only ever added, and details are merged.

    Attributes:
        session: Database session without a site scope; the service commits.
    """

    def __init__(self, session: AsyncSession):
        """Bind the service to a session.

        Args:
            session: Database session without a site scope.
        """
        self.session = session

    async def apply(self, envelope: Envelope) -> StoredChange | None:
        """Store one change from the bus.

        Args:
            envelope: The change.

        Returns:
            The stored event, or None when the change had nothing left to do.

        Raises:
            InvalidChange: The change has an unknown type or invalid data.
        """
        try:
            if envelope.type == EventType.ANALYTICS_EVENT:
                return await self.analytics(AnalyticsEventChange.model_validate(envelope.data))
            if envelope.type == EventType.CAMERA_EVENT:
                return await self.camera(CameraEventChange.model_validate(envelope.data))
            if envelope.type == EventType.EVENT_INTERRUPTED:
                return await self.interrupted(EventInterrupted.model_validate(envelope.data))
        except ValidationError as exc:
            raise InvalidChange(f"Invalid {envelope.type}: {exc.error_count()} errors, first {exc.errors()[0]}")
        raise InvalidChange(f"The event store does not take {envelope.type}")

    async def load(self, event_id) -> Event | None:
        """Load an event by its public id, locking it for this transaction.

        Args:
            event_id: The event id.

        Returns:
            The row, or None.
        """
        return await self.session.scalar(select(Event).where(Event.uuid == event_id).with_for_update())

    async def finish(self, row: Event, alerted: bool) -> StoredChange:
        """Commit a change and describe the stored event.

        Args:
            row: The event row.
            alerted: Whether the event alerted before this change.

        Returns:
            The stored event.
        """
        await self.session.commit()
        await self.session.refresh(row)
        camera_name = await self.session.scalar(select(Camera.name).where(Camera.uuid == row.camera_uuid))
        read = event_read(row, camera_name, datetime.now(UTC))
        return StoredChange(event=read, raised=read.alert and not alerted, site_id=row.site_id)

    async def analytics(self, data: AnalyticsEventChange) -> StoredChange:
        """Create or update an event from an analytics change.

        Args:
            data: The change.

        Returns:
            The stored event.
        """
        row = await self.load(data.id)
        alerted = bool(row.alert) if row is not None else False
        details = {name: getattr(data, name) for name in DETAIL_FIELDS}
        details.update({name: getattr(data, name) for name in LATER_FIELDS if getattr(data, name) is not None})
        details["boxes"] = data.boxes
        if row is None:
            row = Event(
                uuid=data.id,
                camera_uuid=data.camera,
                kind=data.kind.value,
                source=EventSource.ANALYTICS.value,
                source_topic=f"analytics/{data.kind.value}",
                label=data.label,
                alert=data.alert,
                view_uuid=data.view,
                started_at=moment(data.started_at),
                details=details,
            )
            self.session.add(row)
        else:
            row.alert = row.alert or data.alert
            row.details = {**(row.details or {}), **details}
        if data.ended_at is not None:
            row.ended_at = moment(data.ended_at)
        for name in MEDIA_FIELDS:
            value = getattr(data, name)
            if value:
                setattr(row, name, value)
        return await self.finish(row, alerted)

    async def camera(self, data: CameraEventChange) -> StoredChange:
        """Create or update an event from a camera change.

        Args:
            data: The change.

        Returns:
            The stored event.
        """
        row = await self.load(data.id)
        alerted = bool(row.alert) if row is not None else False
        if row is None:
            row = Event(
                uuid=data.id,
                camera_uuid=data.camera,
                kind=data.kind.value,
                source=EventSource.CAMERA.value,
                source_topic=data.source_topic,
                alert=data.alert,
                started_at=data.started_at,
                ended_at=data.ended_at,
                camera_time=data.camera_time,
                details=data.details,
            )
            self.session.add(row)
        else:
            row.alert = row.alert or data.alert
            row.details = {**(row.details or {}), **data.details}
            if data.ended_at is not None:
                row.ended_at = data.ended_at
            if data.camera_time is not None:
                row.camera_time = data.camera_time
        for name in ("snapshot_path", "thumbnail_path"):
            value = getattr(data, name)
            if value:
                setattr(row, name, value)
        return await self.finish(row, alerted)

    async def interrupted(self, data: EventInterrupted) -> StoredChange | None:
        """Close an event a restart left open, unless it was closed meanwhile.

        Args:
            data: The close.

        Returns:
            The stored event, or None when it is unknown or already closed.
        """
        row = await self.load(data.id)
        if row is None or row.ended_at is not None or row.deleted_at is not None:
            await self.session.rollback()
            return None
        row.ended_at = data.ended_at
        row.details = {**(row.details or {}), "interrupted": True}
        return await self.finish(row, bool(row.alert))
