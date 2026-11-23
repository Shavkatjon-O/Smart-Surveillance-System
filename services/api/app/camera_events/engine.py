"""Engine that follows the events of every camera, publishes their changes on the bus and takes snapshots.

The engine never writes an event itself: every change goes onto the bus as a camera event change carrying the whole
event, and the event store worker stores it. Source states go to the live fan-out of every API replica.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update

from app.bus.connection import BusConnection, bus
from app.bus.messages import live_envelope
from app.bus.owners import owners
from app.camera_events.broker import EventBroker
from app.camera_events.snapshots import SnapshotStore
from app.camera_events.subscriber import CameraSubscriber, SourceStatus
from app.camera_events.topics import classify, condition_name, is_condition, state_of
from app.camera_events.tracker import Closing, EventTracker, OpenEvent
from app.core.config import settings
from app.core.database import async_session_maker
from app.health.probe import tcp_probe
from app.health.targets import camera_address
from app.models import Camera, Event, Organisation, Site
from app.modules.analytics.retention import purge_expired_counts, purge_expired_journeys
from app.modules.billing.enums import Limit
from app.modules.billing.services.entitlements import entitlements_for
from app.modules.events.enums import EventKind, EventSource, SourceState
from app.modules.events.schemas import CameraEventChange, EventInterrupted, EventSourceRead
from app.modules.investigation.services.retention import purge_expired_samples
from app.modules.tenancy.services.sites import effective_retention
from app.onvif.endpoints import device_endpoint, endpoint_key
from app.streaming.names import MAIN, SUB, stream_name
from camera_adapters.onvif.notifications import Notification, parse_time
from event_bus import Envelope, EventType, stable_id, subjects

logger = logging.getLogger(__name__)

TICK_SECONDS = 1.0
RECONCILE_SECONDS = 15.0
PRUNE_SECONDS = 3600.0
PRUNE_BATCH = 500

STORAGE_REPEAT = timedelta(hours=24)

SNAPSHOT_KINDS = {EventKind.MOTION, EventKind.PERSON, EventKind.VEHICLE, EventKind.TAMPER}
QUIET_KINDS = {EventKind.MOTION.value}

SOURCE = "/sss/api/camera-events"
REMEMBERED_EVENTS = 4096


def source_read(status: SourceStatus) -> EventSourceRead:
    """Convert a source status to its API form.

    Args:
        status: Source status of one camera.

    Returns:
        The source description sent to clients.
    """
    return EventSourceRead(
        camera_uuid=status.camera_uuid,
        camera_name=status.camera_name,
        state=status.state,
        mode=status.mode,
        endpoint=status.endpoint,
        topics=list(status.topics),
        kinds=list(status.kinds),
        conditions=dict(status.conditions),
        connected_at=status.connected_at,
        last_message_at=status.last_message_at,
        error=status.error,
    )


def alive_check(host: str, port: int):
    """Build a check of whether a camera answers on a TCP port.

    Args:
        host: Host to probe.
        port: Port to probe.

    Returns:
        A coroutine function that returns True when the port accepts a connection.
    """

    async def alive() -> bool:
        """Probe the port and report whether it answered.

        Returns:
            True when the port accepted a connection.
        """
        return (await tcp_probe(host, port)).ok

    return alive


class CameraEventsEngine:
    """Run one event subscriber per enabled camera and publish their messages as event changes on the bus.

    A background task reconciles subscribers with the camera list, closes finished events and prunes events, their
    face samples and the analytics appearances and journeys past the event retention, and line counts past theirs.
    Every change is published on the camera's event subject, and every source state on the site's live subject.

    Attributes:
        store: Snapshot storage.
        broker: Broker of this replica's live event streams, which the live relay fills from the bus.
        connection: The bus connection.
        retention: How long events, their face samples, appearances and journeys are kept.
        counts_retention: How long line counts are kept.
        tracker: Open events.
        subscribers: Subscriber of each camera with the key of the endpoint it was created for.
        statuses: Latest source status of each camera.
        names: Name of each camera.
        streams: go2rtc stream used for the snapshots of each camera.
        owners: Organisation and site ids of each camera, as text.
        changes: The latest change published for each recent event, so every change can carry the whole event.
        lock: Serialises the prune's database writes.
        task: Background loop, if started.
        background: Snapshot tasks that are still running.
        reconciled_at: Monotonic time of the last reconciliation, if any.
        pruned_at: Monotonic time of the last prune, if any.
    """

    def __init__(
        self,
        store: SnapshotStore,
        broker: EventBroker,
        retention_days: int,
        counts_retention_days: int,
        connection: BusConnection = bus,
    ):
        """Create an engine that is not started yet.

        Args:
            store: Snapshot storage.
            broker: Broker of this replica's live event streams.
            retention_days: Number of days events, their face samples, appearances and journeys are kept.
            counts_retention_days: Number of days line counts are kept.
            connection: The bus connection.
        """
        self.store = store
        self.broker = broker
        self.connection = connection
        self.retention = timedelta(days=retention_days)
        self.counts_retention = timedelta(days=counts_retention_days)

        self.tracker = EventTracker()
        self.subscribers: dict[UUID, tuple[tuple, CameraSubscriber]] = {}
        self.statuses: dict[UUID, SourceStatus] = {}
        self.names: dict[UUID, str] = {}
        self.streams: dict[UUID, str] = {}
        self.owners: dict[UUID, tuple[str | None, str | None]] = {}
        self.changes: OrderedDict[UUID, dict] = OrderedDict()

        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.background: set[asyncio.Task] = set()
        self.reconciled_at: float | None = None
        self.pruned_at: float | None = None

    async def start(self) -> None:
        """Start the background loop unless it is already running."""
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="camera-events")

    async def stop(self) -> None:
        """Stop the loop, the subscribers and the running snapshot tasks."""
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await asyncio.gather(*(subscriber.stop() for _, subscriber in self.subscribers.values()))
        self.subscribers.clear()
        for task in list(self.background):
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        self.background.clear()

    async def run(self) -> None:
        """Recover interrupted events, then reconcile, close and prune on their schedules until cancelled.

        Failures of a step are logged and do not stop the loop.
        """
        try:
            await self.recover()
        except Exception:
            logger.exception("Closing the events left open failed")
        while True:
            now = time.monotonic()
            if self.reconciled_at is None or now - self.reconciled_at >= RECONCILE_SECONDS:
                self.reconciled_at = now
                try:
                    await self.reconcile()
                except Exception:
                    logger.exception("Reconciling the event subscribers failed")
            try:
                await self.close_due()
            except Exception:
                logger.exception("Closing finished events failed")
            if self.pruned_at is None or now - self.pruned_at >= PRUNE_SECONDS:
                self.pruned_at = now
                try:
                    await self.prune()
                except Exception:
                    logger.exception("Pruning old events failed")
                try:
                    await self.prune_faces()
                except Exception:
                    logger.exception("Pruning old face samples failed")
                try:
                    await self.prune_analytics()
                except Exception:
                    logger.exception("Pruning old appearances, journeys and counts failed")
            await asyncio.sleep(TICK_SECONDS)

    def sources(self) -> list[EventSourceRead]:
        """List the source status of every camera.

        Returns:
            The sources ordered by camera position and name.
        """
        ordered = sorted(self.statuses.values(), key=lambda item: (item.position, item.camera_name))
        return [source_read(status) for status in ordered]

    def later(self, coroutine) -> None:
        """Run a coroutine in a background task that stop cancels.

        Args:
            coroutine: The coroutine.
        """
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def send_live(self, camera_uuid: UUID, kind: str, payload: dict) -> None:
        """Send a message about a camera to the live fan-out of its site.

        Args:
            camera_uuid: Public id of the camera.
            kind: Live stream message type.
            payload: The message data.
        """
        organisation, site = self.owners.get(camera_uuid) or await owners.of_camera(camera_uuid)
        envelope = live_envelope(kind, payload, organisation, site, SOURCE)
        await self.connection.broadcast(subjects.live(organisation, site), envelope)

    def set_status(self, status: SourceStatus) -> None:
        """Store a source status and send it to the live fan-out when it changed.

        Args:
            status: New source status.
        """
        previous = self.statuses.get(status.camera_uuid)
        self.statuses[status.camera_uuid] = status
        if previous != status:
            self.later(self.send_live(status.camera_uuid, "source", source_read(status).model_dump(mode="json")))

    def subscriber_status(self, status: SourceStatus) -> None:
        """Store a status reported by a subscriber, keeping the engine's camera name and position.

        Args:
            status: Status reported by the subscriber.
        """
        current = self.statuses.get(status.camera_uuid)
        if current is not None:
            status = replace(status, camera_name=current.camera_name, position=current.position)
        self.set_status(status)

    async def recover(self) -> None:
        """Close events that a previous run left open, through the bus.

        They end at the time of the restart limited to the open cap and are marked as interrupted. Each close goes
        on the event's own subject, behind any change of the event still on its way, and the event store applies it
        only while the event is still open.
        """
        async with async_session_maker() as session:
            query = select(Event).where(Event.ended_at.is_(None), Event.deleted_at.is_(None))
            rows = list(await session.scalars(query))
            now = datetime.now(UTC)
            for row in rows:
                organisation, site = await owners.of_site(session, row.site_id)
                if row.source == EventSource.ANALYTICS.value and row.view_uuid is not None:
                    subject = subjects.analytics_event(organisation, site, row.view_uuid)
                else:
                    subject = subjects.camera_event(organisation, site, row.camera_uuid)
                data = EventInterrupted(id=row.uuid, ended_at=min(now, row.started_at + self.tracker.cap))
                envelope = Envelope.create(
                    EventType.EVENT_INTERRUPTED,
                    SOURCE,
                    data.model_dump(mode="json"),
                    subject=row.uuid,
                    organisation=organisation,
                    site=site,
                )
                await self.connection.emit(subject, envelope)
        if rows:
            logger.info("Closing %d events left open by the previous run", len(rows))

    async def load(self) -> list[Camera]:
        """Load the cameras that are not soft-deleted, remembering their organisation and site ids.

        Returns:
            The cameras.
        """
        async with async_session_maker() as session:
            rows = (
                await session.execute(
                    select(Camera, Organisation.uuid, Site.uuid)
                    .outerjoin(Site, Site.id == Camera.site_id)
                    .outerjoin(Organisation, Organisation.id == Camera.organisation_id)
                    .where(Camera.deleted_at.is_(None))
                )
            ).all()
        for camera, organisation, site in rows:
            self.owners[camera.uuid] = (str(organisation) if organisation else None, str(site) if site else None)
        return [camera for camera, _, _ in rows]

    async def reconcile(self) -> None:
        """Bring subscribers and cached names and streams in line with the cameras and forget removed cameras."""
        cameras = await self.load()
        seen = set()
        for camera in cameras:
            seen.add(camera.uuid)
            self.names[camera.uuid] = camera.name
            self.streams[camera.uuid] = stream_name(camera.uuid, SUB if camera.sub_path else MAIN)
            await self.reconcile_camera(camera)
        for camera_uuid in [uuid for uuid in self.statuses if uuid not in seen]:
            await self.forget(camera_uuid)

    async def reconcile_camera(self, camera: Camera) -> None:
        """Bring the subscriber of one camera in line with its settings.

        A subscriber whose endpoint is unchanged is kept. Otherwise it is replaced, its open events are closed, and a
        disabled camera or one without an ONVIF port gets a status that says so.

        Args:
            camera: Camera to reconcile.
        """
        current = self.statuses.get(camera.uuid)
        endpoint = device_endpoint(camera) if camera.enabled and camera.connector_id is None else None
        entry = self.subscribers.get(camera.uuid)
        if endpoint is not None and entry is not None and entry[0] == endpoint_key(endpoint):
            if current is not None and (current.camera_name != camera.name or current.position != camera.position):
                self.set_status(replace(current, camera_name=camera.name, position=camera.position))
            entry[1].status = replace(entry[1].status, camera_name=camera.name, position=camera.position)
            return
        if entry is not None:
            del self.subscribers[camera.uuid]
            await entry[1].stop()
            await self.close(self.tracker.drop(camera.uuid, datetime.now(UTC)))
        base = SourceStatus(camera_uuid=camera.uuid, camera_name=camera.name, position=camera.position)
        if not camera.enabled:
            self.set_status(replace(base, state=SourceState.DISABLED, error="The camera is disabled"))
            return
        if camera.connector_id is not None:
            self.set_status(
                replace(base, state=SourceState.UNSUPPORTED, error="Camera events of connector cameras come later")
            )
            return
        if endpoint is None:
            self.set_status(replace(base, state=SourceState.UNSUPPORTED, error="The camera has no ONVIF port"))
            return
        self.set_status(base)
        host, port = camera_address(camera.host, camera.device_host, camera.rtsp_port)
        subscriber = CameraSubscriber(base, endpoint, self.handle, self.subscriber_status, alive_check(host, port))
        self.subscribers[camera.uuid] = (endpoint_key(endpoint), subscriber)
        subscriber.start()

    async def forget(self, camera_uuid: UUID) -> None:
        """Stop following a camera that no longer exists.

        Args:
            camera_uuid: Public id of the camera.
        """
        entry = self.subscribers.pop(camera_uuid, None)
        if entry is not None:
            await entry[1].stop()
        await self.close(self.tracker.drop(camera_uuid, datetime.now(UTC)))
        self.statuses.pop(camera_uuid, None)
        self.names.pop(camera_uuid, None)
        self.streams.pop(camera_uuid, None)
        await self.send_live(camera_uuid, "source_removed", {"camera_uuid": str(camera_uuid)})
        self.owners.pop(camera_uuid, None)

    async def handle(
        self, camera_uuid: UUID, messages: list[Notification], camera_now: datetime | None, polled: bool
    ) -> None:
        """Turn the notifications of a camera into events.

        Notifications that arrive when a subscription starts only keep events already open, because their real start
        time is unknown. Event times use this computer's clock.

        Args:
            camera_uuid: Public id of the camera.
            messages: Notifications delivered by the camera.
            camera_now: Camera time at delivery, if reported.
            polled: Whether the messages came from polling rather than from a held subscription.
        """
        now = datetime.now(UTC)
        offset = (camera_now - now).total_seconds() if camera_now is not None else None
        for message in messages:
            if is_condition(message.topic):
                self.condition(camera_uuid, message)
            signal = classify(message)
            if signal is None:
                continue
            if signal.reboot:
                await self.reboot(camera_uuid, message, offset, now)
                continue
            initialized = message.operation == "Initialized" and not polled
            if signal.active is None:
                if message.operation is None:
                    await self.instant(camera_uuid, signal.kind, message, offset, now)
                continue
            if signal.kind == EventKind.STORAGE:
                if signal.active:
                    await self.storage(camera_uuid, message, offset, now, initialized)
                continue
            if not signal.active:
                self.tracker.stop(camera_uuid, message.topic, now)
                continue
            if initialized:
                self.tracker.keep(camera_uuid, signal.kind.value, message.topic)
                continue
            opened = self.tracker.start(camera_uuid, signal.kind.value, message.topic, now)
            if opened is not None:
                await self.create_open(opened, message, offset)

    def condition(self, camera_uuid: UUID, message: Notification) -> None:
        """Update the status with the state of a camera condition.

        Args:
            camera_uuid: Public id of the camera.
            message: Notification reporting the condition.
        """
        status = self.statuses.get(camera_uuid)
        value = state_of(message.data)
        if status is None or value is None:
            return
        conditions = dict(status.conditions)
        conditions[condition_name(message.topic)] = value
        entry = self.subscribers.get(camera_uuid)
        if entry is not None:
            entry[1].status = replace(entry[1].status, conditions=conditions)
        self.set_status(replace(status, conditions=conditions))

    def details(self, message: Notification, offset: float | None, **extra) -> dict:
        """Build the details stored with an event.

        Args:
            message: Notification that caused the event.
            offset: Seconds the camera clock is ahead of this computer's, or None when unknown.
            **extra: Further details to add.

        Returns:
            The message summary, the rounded clock offset when known, and the extra details.
        """
        result: dict = {"messages": [message.details()]}
        if offset is not None:
            result["camera_offset_seconds"] = round(offset, 1)
        result.update(extra)
        return result

    async def publish(self, change: dict) -> None:
        """Publish an event change carrying the whole event, and remember it for the event's later changes.

        Args:
            change: The change, in the fields of CameraEventChange.
        """
        data = CameraEventChange.model_validate(change).model_dump(mode="json")
        event_uuid = UUID(str(change["id"]))
        self.changes[event_uuid] = change
        self.changes.move_to_end(event_uuid)
        while len(self.changes) > REMEMBERED_EVENTS:
            self.changes.popitem(last=False)
        camera_uuid = UUID(str(change["camera"]))
        organisation, site = self.owners.get(camera_uuid) or await owners.of_camera(camera_uuid)
        envelope = Envelope.create(
            EventType.CAMERA_EVENT, SOURCE, data, subject=event_uuid, organisation=organisation, site=site
        )
        await self.connection.emit(subjects.camera_event(organisation, site, camera_uuid), envelope)

    def change(
        self,
        event_uuid: UUID,
        action: str,
        camera_uuid: UUID,
        kind: str,
        topic: str,
        started_at: datetime,
        ended_at: datetime | None,
        message: Notification,
        details: dict,
        camera_time: datetime | None = None,
    ) -> dict:
        """Describe a new event as its first change.

        Args:
            event_uuid: Public id of the event.
            action: open or instant.
            camera_uuid: Public id of the camera.
            kind: Kind of the event.
            topic: ONVIF topic that caused it.
            started_at: Its start.
            ended_at: Its end, None while open.
            message: Notification that caused it.
            details: Details to store with it.
            camera_time: The camera's own time of the event; the message's time when omitted.

        Returns:
            The change.
        """
        return {
            "id": event_uuid,
            "action": action,
            "camera": camera_uuid,
            "kind": kind,
            "source_topic": topic,
            "alert": kind not in QUIET_KINDS,
            "started_at": started_at,
            "ended_at": ended_at,
            "camera_time": camera_time or message.utc_time,
            "details": details,
            "snapshot_path": None,
            "thumbnail_path": None,
        }

    async def create_open(self, opened: OpenEvent, message: Notification, offset: float | None) -> None:
        """Publish a newly opened event and schedule its snapshot.

        Args:
            opened: Event the tracker opened.
            message: Notification that opened it.
            offset: Seconds the camera clock is ahead of this computer's, or None when unknown.
        """
        await self.publish(
            self.change(
                opened.uuid,
                "open",
                opened.camera_uuid,
                opened.kind,
                opened.topic,
                opened.started_at,
                None,
                message,
                self.details(message, offset),
            )
        )
        logger.info("Camera %s %s started", self.names.get(opened.camera_uuid), opened.kind)
        self.snapshot_later(opened.uuid, opened.camera_uuid, opened.started_at)

    async def instant(
        self,
        camera_uuid: UUID,
        kind: EventKind,
        message: Notification,
        offset: float | None,
        now: datetime,
        event_uuid: UUID | None = None,
        started_at: datetime | None = None,
        details: dict | None = None,
        camera_time: datetime | None = None,
    ) -> None:
        """Publish an event that starts and ends at the same moment.

        A snapshot is scheduled for the kinds that have one.

        Args:
            camera_uuid: Public id of the camera.
            kind: Kind of the event.
            message: Notification that caused it.
            offset: Seconds the camera clock is ahead of this computer's, or None when unknown.
            now: Current time.
            event_uuid: Public id of the event; a new one when omitted.
            started_at: Its time; now when omitted.
            details: Details to store; the message summary when omitted.
            camera_time: The camera's own time of the event; the message's time when omitted.
        """
        event_uuid = event_uuid or uuid4()
        at = started_at or now
        await self.publish(
            self.change(
                event_uuid,
                "instant",
                camera_uuid,
                kind.value,
                message.topic,
                at,
                at,
                message,
                details if details is not None else self.details(message, offset),
                camera_time,
            )
        )
        if kind in SNAPSHOT_KINDS:
            self.snapshot_later(event_uuid, camera_uuid, now)

    async def storage(
        self, camera_uuid: UUID, message: Notification, offset: float | None, now: datetime, initialized: bool
    ) -> None:
        """Store a storage failure event.

        A failure reported only because a subscription started is skipped when one was stored recently.

        Args:
            camera_uuid: Public id of the camera.
            message: Notification that reported the failure.
            offset: Seconds the camera clock is ahead of this computer's, or None when unknown.
            now: Current time.
            initialized: Whether the message only reports the state at subscription start.
        """
        if initialized:
            async with async_session_maker() as session:
                recent = await session.scalar(
                    select(Event.id)
                    .where(
                        Event.camera_uuid == camera_uuid,
                        Event.kind == EventKind.STORAGE.value,
                        Event.started_at >= now - STORAGE_REPEAT,
                        Event.deleted_at.is_(None),
                    )
                    .limit(1)
                )
            if recent is not None:
                return
        await self.instant(camera_uuid, EventKind.STORAGE, message, offset, now)

    async def reboot(self, camera_uuid: UUID, message: Notification, offset: float | None, now: datetime) -> None:
        """Store a reboot event from the camera's reported last reboot time.

        The reboot is skipped when its time is unknown, older than retention, or already stored.

        Args:
            camera_uuid: Public id of the camera.
            message: Notification that reported the reboot.
            offset: Seconds the camera clock is ahead of this computer's, or None when unknown.
            now: Current time.
        """
        reported = parse_time(message.data.get("Status"))
        if reported is None:
            return
        started = reported - timedelta(seconds=offset or 0)
        if started > now:
            started = now
        if now - started > self.retention:
            return
        async with async_session_maker() as session:
            known = await session.scalar(
                select(Event.id)
                .where(
                    Event.camera_uuid == camera_uuid,
                    Event.kind == EventKind.REBOOT.value,
                    Event.camera_time == reported,
                )
                .limit(1)
            )
        if known is not None:
            return
        await self.instant(
            camera_uuid,
            EventKind.REBOOT,
            message,
            offset,
            now,
            event_uuid=UUID(stable_id("reboot", camera_uuid, reported.isoformat())),
            started_at=started,
            details=self.details(message, offset, reported_at=reported.isoformat()),
            camera_time=reported,
        )

    async def close_due(self) -> None:
        """Close the open events that have stopped or reached the cap."""
        await self.close(self.tracker.due(datetime.now(UTC)))

    async def close(self, closings: list[Closing]) -> None:
        """Publish the end of events.

        Args:
            closings: Events to close with their end times.
        """
        for closing in closings:
            event = closing.event
            previous = self.changes.get(event.uuid)
            if previous is None:
                logger.warning("Closing camera event %s without its first change", event.uuid)
                previous = {
                    "id": event.uuid,
                    "camera": event.camera_uuid,
                    "kind": event.kind,
                    "source_topic": event.topic,
                    "alert": event.kind not in QUIET_KINDS,
                    "started_at": event.started_at,
                    "details": {},
                }
            extra = {"topics": sorted(event.seen | {event.topic})}
            if closing.capped:
                extra["capped"] = True
            details = {**(previous.get("details") or {}), **extra}
            await self.publish({**previous, "action": "close", "ended_at": closing.ended_at, "details": details})

    def snapshot_later(self, event_uuid: UUID, camera_uuid: UUID, at: datetime) -> None:
        """Take a snapshot of an event in a background task.

        Args:
            event_uuid: Public id of the event.
            camera_uuid: Public id of the camera.
            at: Time of the event.
        """
        self.later(self.snapshot(event_uuid, camera_uuid, at))

    async def snapshot(self, event_uuid: UUID, camera_uuid: UUID, at: datetime) -> None:
        """Capture a snapshot of an event and publish it as a change of the event.

        Args:
            event_uuid: Public id of the event.
            camera_uuid: Public id of the camera.
            at: Time of the event.
        """
        stream = self.streams.get(camera_uuid)
        if not stream:
            return
        snapshot_path, thumbnail_path = await self.store.capture(stream, event_uuid, at)
        if not snapshot_path and not thumbnail_path:
            return
        previous = self.changes.get(event_uuid)
        if previous is None:
            logger.warning("Dropping the snapshot of camera event %s, whose changes are forgotten", event_uuid)
            await self.store.remove([path for path in (snapshot_path, thumbnail_path) if path])
            return
        await self.publish(
            {**previous, "action": "snapshot", "snapshot_path": snapshot_path, "thumbnail_path": thumbnail_path}
        )

    async def cutoffs(self, session, now: datetime) -> tuple[dict[int, datetime], dict[int, datetime]]:
        """Work out how far back each site keeps its events and its clips.

        A site keeps events for the shortest of its own setting, its plan's event retention and the server's
        retention, and clips for the shorter of that and its plan's clip retention.

        Args:
            session: Database session without a scope.
            now: Current time.

        Returns:
            Event cutoff and clip cutoff by site id.
        """
        sites = (
            await session.execute(
                select(Site.id, Site.organisation_id, Site.event_retention_days).where(Site.deleted_at.is_(None))
            )
        ).all()
        plans = {}
        events, clips = {}, {}
        for site_id, organisation_id, own in sites:
            if organisation_id not in plans:
                plans[organisation_id] = await entitlements_for(session, organisation_id)
            granted = plans[organisation_id]
            days = effective_retention(own, granted.limit(Limit.EVENT_RETENTION_DAYS))
            clip_days = min(days, granted.limit(Limit.CLIP_RETENTION_DAYS) or days)
            events[site_id] = now - timedelta(days=days)
            clips[site_id] = now - timedelta(days=clip_days)
        return events, clips

    async def prune(self) -> None:
        """Delete events past their site's retention in batches, with their files, and clips past theirs."""
        now = datetime.now(UTC)
        default = now - self.retention
        async with async_session_maker() as session:
            events, clips = await self.cutoffs(session, now)
        expired = or_(
            Event.site_id.is_(None) & (Event.started_at < default),
            *[(Event.site_id == site_id) & (Event.started_at < cutoff) for site_id, cutoff in events.items()],
        )
        total = 0
        while True:
            async with self.lock:
                async with async_session_maker() as session:
                    rows = (
                        await session.execute(
                            select(Event.id, Event.snapshot_path, Event.thumbnail_path, Event.clip_path)
                            .where(expired)
                            .limit(PRUNE_BATCH)
                        )
                    ).all()
                    if not rows:
                        break
                    await session.execute(delete(Event).where(Event.id.in_([row.id for row in rows])))
                    await session.commit()
            files = [path for row in rows for path in (row.snapshot_path, row.thumbnail_path, row.clip_path) if path]
            await self.store.remove(files)
            total += len(rows)
            if len(rows) < PRUNE_BATCH:
                break
        if total:
            logger.info("Pruned %d events past their retention", total)
        await self.prune_clips(clips, events)

    async def prune_clips(self, clips: dict[int, datetime], events: dict[int, datetime]) -> None:
        """Delete the clips of events that outlive their site's clip retention, keeping the events.

        Args:
            clips: Clip cutoff by site id.
            events: Event cutoff by site id; sites whose clips go with their events are skipped.
        """
        shorter = {site_id: cutoff for site_id, cutoff in clips.items() if cutoff > events.get(site_id, cutoff)}
        if not shorter:
            return
        expired = or_(
            *[(Event.site_id == site_id) & (Event.started_at < cutoff) for site_id, cutoff in shorter.items()]
        )
        async with self.lock:
            async with async_session_maker() as session:
                rows = (
                    await session.execute(
                        select(Event.id, Event.clip_path)
                        .where(Event.clip_path.is_not(None), expired)
                        .limit(PRUNE_BATCH)
                    )
                ).all()
                if not rows:
                    return
                await session.execute(
                    update(Event).where(Event.id.in_([row.id for row in rows])).values(clip_path=None)
                )
                await session.commit()
        await self.store.remove([row.clip_path for row in rows])
        logger.info("Removed %d clips past their retention", len(rows))

    async def prune_faces(self) -> None:
        """Delete face samples seen by the cameras before the retention, together with their crops."""
        async with async_session_maker() as session:
            await purge_expired_samples(session, datetime.now(UTC), self.retention.days)

    async def prune_analytics(self) -> None:
        """Delete appearances and journeys older than the event retention and line counts older than theirs."""
        now = datetime.now(UTC)
        async with async_session_maker() as session:
            await purge_expired_journeys(session, now, self.retention.days)
            await purge_expired_counts(session, now, self.counts_retention.days)


camera_events = CameraEventsEngine(
    SnapshotStore(settings.events_dir, settings.go2rtc_url),
    EventBroker(),
    settings.events_retention_days,
    settings.counts_retention_days,
)
