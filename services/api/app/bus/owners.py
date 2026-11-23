"""Public ids of the organisation and site that own a camera or a site, for the subjects of the bus."""

import time
from uuid import UUID

from sqlalchemy import select

from app.core.database import UNSCOPED_OPTION, async_session_maker
from app.models import Camera, Organisation, Site

__all__ = ["CACHE_SECONDS", "OwnerCache", "owners"]

CACHE_SECONDS = 60.0
UNSCOPED = {UNSCOPED_OPTION: True}

Owner = tuple[str | None, str | None]


class OwnerCache:
    """Looks up and briefly remembers the organisation and site of cameras and sites.

    Attributes:
        cameras: Owner and lookup time by camera id.
        sites: Owner and lookup time by site database id.
        seconds: How long a lookup is remembered.
    """

    def __init__(self, seconds: float = CACHE_SECONDS):
        """Start empty.

        Args:
            seconds: How long a lookup is remembered.
        """
        self.cameras: dict[UUID, tuple[Owner, float]] = {}
        self.sites: dict[int, tuple[Owner, float]] = {}
        self.seconds = seconds

    def fresh(self, entry: tuple[Owner, float] | None) -> Owner | None:
        """Return a remembered owner that is still fresh.

        Args:
            entry: The remembered owner and its lookup time.

        Returns:
            The owner, or None.
        """
        if entry is None or time.monotonic() - entry[1] > self.seconds:
            return None
        return entry[0]

    async def of_camera(self, camera_uuid: UUID | str) -> Owner:
        """Return the organisation and site of a camera, including a deleted one.

        Args:
            camera_uuid: Public id of the camera.

        Returns:
            Organisation and site ids as text, None where the camera has none or is unknown.
        """
        key = UUID(str(camera_uuid))
        known = self.fresh(self.cameras.get(key))
        if known is not None:
            return known
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    select(Organisation.uuid, Site.uuid)
                    .select_from(Camera)
                    .outerjoin(Site, Site.id == Camera.site_id)
                    .outerjoin(Organisation, Organisation.id == Camera.organisation_id)
                    .where(Camera.uuid == key)
                    .execution_options(**UNSCOPED)
                )
            ).first()
        owner = (str(row[0]) if row and row[0] else None, str(row[1]) if row and row[1] else None)
        self.cameras[key] = (owner, time.monotonic())
        return owner

    async def of_site(self, session, site_id: int | None) -> Owner:
        """Return the organisation and site ids of a site.

        Args:
            session: Database session to look up in.
            site_id: Database id of the site, None for a row without one.

        Returns:
            Organisation and site ids as text, None where unknown.
        """
        if site_id is None:
            return None, None
        known = self.fresh(self.sites.get(site_id))
        if known is not None:
            return known
        row = (
            await session.execute(
                select(Organisation.uuid, Site.uuid)
                .select_from(Site)
                .join(Organisation, Organisation.id == Site.organisation_id)
                .where(Site.id == site_id)
                .execution_options(**UNSCOPED)
            )
        ).first()
        owner = (str(row[0]) if row else None, str(row[1]) if row else None)
        self.sites[site_id] = (owner, time.monotonic())
        return owner


owners = OwnerCache()
