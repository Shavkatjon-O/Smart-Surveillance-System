"""Service for the face results of the analytics service, and the faces shown with face events."""

import inspect
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Camera, Event, FaceSample, Person, UnknownCluster
from app.modules.events.enums import EventSource
from app.modules.events.schemas import event_read
from app.modules.investigation.enums import FACE_KINDS, FACE_KNOWN, FACE_UNKNOWN, FaceStatus, SampleSource
from app.modules.investigation.schemas import FaceRead, FaceResult, FaceResultAck

from .people import missing_person
from .shared import cluster_label, lock_faces, recompute_centroid

__all__ = ["FaceResultService", "face_event_details", "face_summaries", "moment", "parse_uuid"]


def moment(seconds: float) -> datetime:
    """Turn Unix seconds into an aware time.

    Args:
        seconds: Seconds since the epoch.

    Returns:
        The time in UTC.
    """
    return datetime.fromtimestamp(seconds, UTC)


def parse_uuid(value) -> UUID | None:
    """Read a UUID stored as text in a JSON object.

    Args:
        value: Stored value, if any.

    Returns:
        The UUID, or None when the value is missing or not a UUID.
    """
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def face_event_details(payload: FaceResult, person: Person | None, cluster: UnknownCluster | None) -> dict:
    """Build the details of a face event from a face result.

    Names are left out on purpose: they are resolved from the people tables whenever events are read.

    Args:
        payload: Face result from the analytics service.
        person: Person the face belongs to, if known.
        cluster: Unknown cluster the face belongs to, if unknown.

    Returns:
        The details stored with the event.
    """
    candidate = payload.candidate
    return {
        "person_uuid": str(person.uuid) if person is not None else None,
        "cluster_uuid": str(cluster.uuid) if cluster is not None else None,
        "confidence": payload.confidence,
        "candidate": {"person": str(candidate.person), "score": candidate.score} if candidate is not None else None,
        "view": payload.view,
        "view_name": payload.view_name,
        "track": payload.track,
        "related_event": payload.related_event,
        "faces": payload.faces,
        "quality": payload.quality,
        "box": payload.box,
        "alert": payload.alert,
    }


FACE_LABEL = "person"


class FaceResultService:
    """Store each face result as one face event with one face sample, keeping the unknown clusters up to date.

    Attributes:
        session: Database session the service works in.
        publish: Announces a stored event on the live event stream and raises its alert, if set.
    """

    def __init__(self, session: AsyncSession, publish: Callable[[str, dict], Awaitable[None] | None] | None = None):
        """Bind the service to a session.

        Args:
            session: Database session the service works in.
            publish: Announces a stored event with its message type and data, if set; it may be a coroutine
                function.
        """
        self.session = session
        self.publish = publish

    async def store(self, payload: FaceResult) -> FaceResultAck:
        """Create or update the face event and the face sample of a face result.

        The event and the sample are found by the face event id, so a repeated open or an update changes what
        was stored before. A cluster that newly receives the event counts it as seen, every touched cluster gets
        its centroid recomputed, and a cluster the event left without samples is soft-deleted.

        Args:
            payload: Face result from the analytics service.

        Returns:
            The event and sample ids and the cluster number of an unknown face.
        """
        seen_at = moment(payload.seen_at)
        started_at = min(moment(payload.started_at), seen_at)
        await lock_faces(self.session)
        person = None
        cluster = None
        if payload.status == FaceStatus.KNOWN:
            person = await self.person(payload.person)
            if not await self.same_organisation(payload.camera, person.organisation_id):
                person = None
        if person is None:
            cluster = await self.cluster(payload, seen_at)
        event = await self.session.scalar(select(Event).where(Event.uuid == payload.id))
        sample = await self.session.scalar(
            select(FaceSample)
            .where(
                FaceSample.event_uuid == payload.id,
                FaceSample.source == SampleSource.EVENT.value,
                FaceSample.deleted_at.is_(None),
            )
            .order_by(FaceSample.id.desc())
            .limit(1)
        )
        previous_cluster_id = sample.cluster_id if sample is not None else None
        joins_cluster = cluster is not None and (sample is None or sample.cluster_id != cluster.id)
        event = await self.store_event(payload, event, started_at, seen_at, person, cluster)
        sample = self.store_sample(payload, sample, seen_at, person, cluster)
        if cluster is not None and cluster.organisation_id is None:
            await self.session.flush()
            cluster.organisation_id = sample.organisation_id
        if cluster is not None:
            if joins_cluster:
                cluster.seen_count = (cluster.seen_count or 0) + 1
            cluster.first_seen_at = min(cluster.first_seen_at, seen_at)
            cluster.last_seen_at = max(cluster.last_seen_at, seen_at)
            await recompute_centroid(self.session, cluster)
        if previous_cluster_id is not None and (cluster is None or previous_cluster_id != cluster.id):
            await self.leave_cluster(previous_cluster_id)
        await self.session.commit()
        await self.announce(event)
        return FaceResultAck(
            event=event.uuid,
            sample=sample.uuid,
            cluster_number=cluster.number if cluster is not None else None,
        )

    async def person(self, uuid: UUID) -> Person:
        """Load the live person a known face belongs to.

        Args:
            uuid: Public id of the person.

        Returns:
            The person.

        Raises:
            NotFound: When the person does not exist or was deleted.
        """
        person = await self.session.scalar(select(Person).where(Person.uuid == uuid, Person.deleted_at.is_(None)))
        if person is None:
            raise missing_person(uuid)
        return person

    async def same_organisation(self, camera_uuid: UUID, organisation_id: int | None) -> bool:
        """Tell whether a gallery entry belongs to the organisation of the camera that saw the face.

        Args:
            camera_uuid: Camera that saw the face.
            organisation_id: Organisation of the matched person or cluster, None for one from before
                organisations, which belongs to every camera.

        Returns:
            True when both are of the same organisation or the entry has none.
        """
        if organisation_id is None:
            return True
        camera_organisation = await self.session.scalar(
            select(Camera.organisation_id).where(Camera.uuid == camera_uuid)
        )
        return camera_organisation is None or camera_organisation == organisation_id

    async def cluster(self, payload: FaceResult, seen_at: datetime) -> UnknownCluster:
        """Find or create the unknown cluster of a face, always within the camera's organisation.

        A cluster id the backend does not know is created with that id and the next number, as is a new cluster
        without an id. A cluster that was deleted or named is brought back, since the analytics service still
        matched a face to it. A face matched to a person or cluster of another organisation starts a cluster of
        its own, because a gallery never crosses organisations.

        Args:
            payload: Face result of an unknown face, or of a known face whose person is of another organisation.
            seen_at: When the face was seen.

        Returns:
            The live cluster; a new one receives the camera's organisation with its first sample.
        """
        wanted = payload.cluster if payload.status != FaceStatus.KNOWN else None
        if wanted is not None:
            existing = await self.session.scalar(select(UnknownCluster).where(UnknownCluster.uuid == wanted))
            if existing is not None and await self.same_organisation(payload.camera, existing.organisation_id):
                existing.deleted_at = None
                return existing
            if existing is not None:
                wanted = None
        highest = await self.session.scalar(select(func.coalesce(func.max(UnknownCluster.number), 0)))
        cluster = UnknownCluster(
            uuid=wanted or uuid4(),
            number=(highest or 0) + 1,
            seen_count=0,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
        self.session.add(cluster)
        await self.session.flush()
        return cluster

    async def store_event(
        self,
        payload: FaceResult,
        event: Event | None,
        started_at: datetime,
        seen_at: datetime,
        person: Person | None,
        cluster: UnknownCluster | None,
    ) -> Event:
        """Create or update the event row of a face result.

        This is the only place that writes the event row, so it can later go through the analytics event path.

        Args:
            payload: Face result from the analytics service.
            event: Event stored for the face event id before, if any.
            started_at: When the track started.
            seen_at: When the face was seen, which also ends the event.
            person: Person the face belongs to, if known.
            cluster: Unknown cluster the face belongs to, if unknown.

        Returns:
            The event row, added to the session when it is new.
        """
        kind = FACE_KNOWN if person is not None else FACE_UNKNOWN
        details = face_event_details(payload, person, cluster)
        if event is None:
            event = Event(uuid=payload.id, source=EventSource.ANALYTICS.value, label=FACE_LABEL, alert=False)
            self.session.add(event)
        event.camera_uuid = payload.camera
        event.kind = kind
        event.source_topic = f"analytics/{kind}"
        event.view_uuid = parse_uuid(payload.view)
        event.alert = bool(event.alert) or payload.alert
        event.started_at = started_at
        event.ended_at = seen_at
        event.snapshot_path = payload.crop
        event.thumbnail_path = payload.crop
        event.details = details
        return event

    async def announce(self, event: Event) -> None:
        """Announce a stored face event on the event stream with the face it shows.

        Args:
            event: The committed event row.
        """
        if self.publish is None:
            return
        await self.session.refresh(event)
        camera_name = await self.session.scalar(select(Camera.name).where(Camera.uuid == event.camera_uuid))
        faces = await face_summaries(self.session, [event])
        read = event_read(event, camera_name, datetime.now(UTC), faces.get(event.uuid))
        sent = self.publish("event", read.model_dump(mode="json"))
        if inspect.isawaitable(sent):
            await sent

    def store_sample(
        self,
        payload: FaceResult,
        sample: FaceSample | None,
        seen_at: datetime,
        person: Person | None,
        cluster: UnknownCluster | None,
    ) -> FaceSample:
        """Create or update the face sample of a face result.

        Args:
            payload: Face result from the analytics service.
            sample: Sample stored for the face event before, if any.
            seen_at: When the face was seen.
            person: Person the face belongs to, if known.
            cluster: Unknown cluster the face belongs to, if unknown.

        Returns:
            The sample row, added to the session when it is new.
        """
        if sample is None:
            sample = FaceSample(uuid=uuid4(), source=SampleSource.EVENT.value, event_uuid=payload.id)
            self.session.add(sample)
        sample.person_id = person.id if person is not None else None
        sample.cluster_id = cluster.id if cluster is not None else None
        sample.embedding = payload.embedding
        sample.crop_path = payload.crop
        sample.quality = payload.quality
        sample.confidence = payload.confidence
        sample.camera_uuid = payload.camera
        sample.view = payload.view
        sample.view_name = payload.view_name
        sample.track_id = payload.track
        sample.seen_at = seen_at
        sample.details = {"faces": payload.faces, "box": payload.box}
        return sample

    async def leave_cluster(self, cluster_id: int) -> None:
        """Update a cluster that a face event moved away from.

        Args:
            cluster_id: Database id of the cluster.
        """
        cluster = await self.session.scalar(select(UnknownCluster).where(UnknownCluster.id == cluster_id))
        if cluster is None:
            return
        cluster.seen_count = max(0, (cluster.seen_count or 0) - 1)
        if await recompute_centroid(self.session, cluster) == 0 and cluster.deleted_at is None:
            cluster.deleted_at = func.now()


async def face_summaries(session: AsyncSession, events: Iterable[Event]) -> dict[UUID, FaceRead]:
    """Resolve who the face events among some events show, from the people tables.

    Args:
        session: Database session.
        events: Event rows of any kind.

    Returns:
        The face of each face event by event id.
    """
    faces = [event for event in events if event.kind in FACE_KINDS]
    if not faces:
        return {}
    person_ids = {parse_uuid((event.details or {}).get("person_uuid")) for event in faces} - {None}
    cluster_ids = {parse_uuid((event.details or {}).get("cluster_uuid")) for event in faces} - {None}
    people = {}
    if person_ids:
        rows = await session.execute(
            select(Person.uuid, Person.name, Person.color).where(
                Person.uuid.in_(person_ids), Person.deleted_at.is_(None)
            )
        )
        people = {uuid: (name, color) for uuid, name, color in rows.all()}
    clusters = {}
    if cluster_ids:
        rows = await session.execute(
            select(UnknownCluster.uuid, UnknownCluster.number).where(UnknownCluster.uuid.in_(cluster_ids))
        )
        clusters = {uuid: number for uuid, number in rows.all()}
    rows = await session.execute(
        select(FaceSample.event_uuid, FaceSample.uuid, FaceSample.crop_path).where(
            FaceSample.event_uuid.in_([event.uuid for event in faces]), FaceSample.deleted_at.is_(None)
        )
    )
    samples = {event_uuid: (uuid, crop_path) for event_uuid, uuid, crop_path in rows.all()}
    result = {}
    for event in faces:
        details = event.details or {}
        person_uuid = parse_uuid(details.get("person_uuid"))
        cluster_uuid = parse_uuid(details.get("cluster_uuid"))
        name, color = people.get(person_uuid, (None, None))
        number = clusters.get(cluster_uuid)
        sample_uuid, crop_path = samples.get(event.uuid, (None, None))
        confidence = details.get("confidence")
        result[event.uuid] = FaceRead(
            status=FaceStatus.KNOWN if event.kind == FACE_KNOWN else FaceStatus.UNKNOWN,
            person_uuid=person_uuid,
            person_name=name,
            person_color=color,
            cluster_uuid=cluster_uuid,
            cluster_label=cluster_label(number) if number is not None else None,
            confidence=confidence if isinstance(confidence, (int, float)) else None,
            sample_uuid=sample_uuid,
            has_crop=bool(crop_path or event.snapshot_path),
        )
    return result
