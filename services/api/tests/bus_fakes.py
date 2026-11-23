from contextlib import asynccontextmanager
from datetime import UTC, datetime
from itertools import count

from app.bus.connection import BusConnection
from event_bus import MemoryBus, provision

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


class StoreSession:
    """A session that answers scalar calls from a queue and keeps what is added."""

    def __init__(self, scalar=()):
        self.scalar_results = list(scalar)
        self.statements = []
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.ids = count(100)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, item):
        item.created_at = item.created_at or NOW
        item.updated_at = item.updated_at or NOW
        if item.id is None:
            item.id = next(self.ids)
        if getattr(item, "site_id", None) is None:
            item.site_id = 3

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.scalar_results.pop(0) if self.scalar_results else None


def sessions_of(session):
    @asynccontextmanager
    async def open_session():
        yield session

    return open_session


async def memory_connection():
    bus = MemoryBus()
    await bus.connect()
    await provision(bus)
    connection = BusConnection("tests")
    connection.use(bus)
    return connection, bus


class Owners:
    """Answers owner lookups with fixed ids."""

    def __init__(self, organisation="org-1", site="site-1"):
        self.owner = (organisation, site)

    async def of_site(self, session, site_id):
        return self.owner if site_id is not None else (None, None)

    async def of_camera(self, camera_uuid):
        return self.owner
