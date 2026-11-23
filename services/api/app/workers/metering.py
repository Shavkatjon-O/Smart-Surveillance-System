"""The metering worker: counted usage from the bus into the hourly rollups, and the hourly gauge rollup."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.core.database import UNSCOPED_OPTION, async_session_maker
from app.models import Organisation, Site
from app.modules.billing.enums import UsageMetric
from app.modules.billing.services.metering import record_usage
from event_bus import Delivery, Envelope, EventType, Reject
from event_bus.streams import METERING

__all__ = ["MeteringWorker"]

UNSCOPED = {UNSCOPED_OPTION: True}


class MeteringWorker:
    """Adds each usage message to the rollup of its site and hour.

    Attributes:
        sessions: Opens database sessions.
        spec: The durable consumer.
    """

    def __init__(self, sessions=async_session_maker):
        """Describe the worker.

        Args:
            sessions: Opens database sessions.
        """
        self.sessions = sessions
        self.spec = METERING

    async def owner(self, session, organisation: str | None, site: str | None) -> tuple[int | None, int | None]:
        """Find the database ids of an organisation and site.

        Args:
            session: Database session.
            organisation: Public id of the organisation.
            site: Public id of the site.

        Returns:
            Organisation and site ids, None where unknown.
        """
        if site:
            row = (
                await session.execute(
                    select(Site.organisation_id, Site.id).where(Site.uuid == site).execution_options(**UNSCOPED)
                )
            ).first()
            if row is not None:
                return row[0], row[1]
        if organisation:
            found = await session.scalar(
                select(Organisation.id).where(Organisation.uuid == organisation).execution_options(**UNSCOPED)
            )
            return found, None
        return None, None

    async def handle(self, envelope: Envelope, delivery: Delivery) -> None:
        """Record one usage message.

        Args:
            envelope: The usage.
            delivery: Its delivery.

        Raises:
            Reject: The metric or amount is invalid.
        """
        if envelope.type != EventType.USAGE:
            return
        data = envelope.data
        try:
            metric = UsageMetric(data["metric"])
            amount = float(data["amount"])
            at = datetime.fromisoformat(str(data.get("at"))) if data.get("at") else datetime.now(UTC)
        except (KeyError, TypeError, ValueError) as exc:
            raise Reject(f"Invalid usage: {exc}")
        async with self.sessions() as session:
            organisation_id, site_id = await self.owner(session, envelope.organisation, envelope.site)
            if organisation_id is None:
                return
            await record_usage(session, organisation_id, site_id, metric, amount, at)
            await session.commit()
