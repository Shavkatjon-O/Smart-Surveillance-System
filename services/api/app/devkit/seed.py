"""Demo data of the development kit, run inside the API container with python -m app.devkit.seed.

The seed creates the Demo organisation with a home and a store site and one member of each role, adds the
simulated cameras the way a member adds them, through the API (the home cameras through the connector, the store
camera directly), gives each camera an analytics view with a zone and a line, and writes a week of events with
snapshots from the cameras and minute line counts. Running it again creates nothing that already exists.
"""

import argparse
import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.security import hash_password
from app.models import (
    AnalyticsCount,
    AnalyticsLine,
    AnalyticsView,
    AnalyticsZone,
    Camera,
    CameraLink,
    Event,
    Membership,
    MembershipSite,
    NetworkDevice,
    Organisation,
    Site,
    User,
)
from app.streaming.names import MAIN, stream_name

__all__ = ["DemoSettings", "main", "seed"]

logger = logging.getLogger("devkit.seed")

ORGANISATION = "Demo"
ROLES = ("owner", "admin", "operator", "viewer")
SITES = (("Home", "home"), ("Store", "store"))
SITE_ROLES = {"operator": "Home", "viewer": "Store"}
SEED_MARK = "devkit"
BOOT = "devkit-seed"
EVENT_DAYS = 7
EVENTS_PER_CAMERA = 12
EVENT_KINDS = (("person", "person"), ("line_crossing", "person"), ("intrusion", "person"), ("vehicle", "car"))
ZONE = [[0.1, 0.45], [0.65, 0.45], [0.65, 0.95], [0.1, 0.95]]
LINE = [[0.15, 0.7], [0.85, 0.7]]
WAIT_SECONDS = 90
POLL_SECONDS = 3
THUMBNAIL_WIDTH = 320


def parse_names(value: str) -> dict[str, str]:
    """Read a list of address=name pairs.

    Args:
        value: Comma-separated pairs, such as 172.31.250.11=Gate.

    Returns:
        Name by address.
    """
    pairs = (item.split("=", 1) for item in value.split(",") if "=" in item)
    return {host.strip(): name.strip() for host, name in pairs if host.strip() and name.strip()}


@dataclass(frozen=True)
class DemoSettings:
    """Settings of the seed, from the development kit's environment.

    Attributes:
        password: Password of every demo member, from SSS_DEMO_PASSWORD.
        email_domain: Domain of the demo members' addresses.
        camera_password: Login password of the simulated cameras.
        api_url: Address of the API from inside its container.
        connector_url: Address of the simulated site's connector agent.
        home_names: Names of the home cameras by address.
        store_names: Names of the store cameras by address, which the API reaches directly.
        store_switch: Address on the store LAN that stands in for the store's switch.
    """

    password: str = field(default_factory=lambda: os.environ.get("SSS_DEMO_PASSWORD", ""))
    email_domain: str = field(default_factory=lambda: os.environ.get("SSS_DEMO_EMAIL_DOMAIN", "devkit.localhost"))
    camera_password: str = field(default_factory=lambda: os.environ.get("SSS_DEV_CAMERA_PASSWORD", ""))

    api_url: str = field(default_factory=lambda: os.environ.get("DEVKIT_API_URL", "http://127.0.0.1:8000"))
    connector_url: str = field(default_factory=lambda: os.environ.get("DEVKIT_CONNECTOR_URL", "http://connector:8780"))

    home_names: dict[str, str] = field(
        default_factory=lambda: parse_names(
            os.environ.get("DEVKIT_HOME_NAMES", "172.31.250.11=Gate,172.31.250.12=Yard")
        )
    )
    store_names: dict[str, str] = field(
        default_factory=lambda: parse_names(os.environ.get("DEVKIT_STORE_NAMES", "172.31.252.11=Counter"))
    )
    store_switch: str = field(default_factory=lambda: os.environ.get("DEVKIT_STORE_SWITCH", "172.31.252.2"))

    def email(self, role: str) -> str:
        """Return the address of a demo member.

        Args:
            role: The member's role.

        Returns:
            The address.
        """
        return f"{role}@{self.email_domain}"


@dataclass
class SeedReport:
    """What a run created.

    Attributes:
        users: Members created.
        sites: Sites created.
        cameras: Cameras added through the API.
        views: Analytics views created.
        devices: Network devices created.
        events: Events written.
        counts: Count rows written.
    """

    users: int = 0
    sites: int = 0
    cameras: int = 0
    views: int = 0
    devices: int = 0
    events: int = 0
    counts: int = 0


async def ensure_users(session: AsyncSession, demo: DemoSettings, report: SeedReport) -> dict[str, User]:
    """Create one verified member per role unless they exist.

    Args:
        session: Database session without a scope.
        demo: Seed settings.
        report: Report to count in.

    Returns:
        The users by role.
    """
    users = {}
    for role in ROLES:
        email = demo.email(role)
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                username=f"demo-{role}",
                email=email,
                name=f"Demo {role}",
                password_hash=hash_password(demo.password),
                email_verified_at=datetime.now(UTC),
            )
            session.add(user)
            report.users += 1
        users[role] = user
    await session.flush()
    return users


async def ensure_organisation(
    session: AsyncSession, users: dict[str, User], report: SeedReport
) -> tuple[Organisation, dict[str, Site]]:
    """Create the Demo organisation, its sites and the memberships unless they exist.

    The owner and the admin reach every site, the operator only the home and the viewer only the store.

    Args:
        session: Database session without a scope.
        users: The demo members by role.
        report: Report to count in.

    Returns:
        The organisation and its sites by name.
    """
    owner = users["owner"]
    organisation = await session.scalar(
        select(Organisation)
        .join(Membership, Membership.organisation_id == Organisation.id)
        .where(Organisation.name == ORGANISATION, Membership.user_id == owner.id, Membership.role == "owner")
    )
    if organisation is None:
        organisation = Organisation(name=ORGANISATION)
        session.add(organisation)
        await session.flush()
    sites = {}
    for position, (name, kind) in enumerate(SITES):
        site = await session.scalar(
            select(Site).where(Site.organisation_id == organisation.id, Site.name == name, Site.deleted_at.is_(None))
        )
        if site is None:
            site = Site(
                organisation_id=organisation.id, name=name, kind=kind, timezone=settings.timezone, position=position
            )
            session.add(site)
            report.sites += 1
        sites[name] = site
    await session.flush()
    for role, user in users.items():
        membership = await session.scalar(
            select(Membership).where(Membership.organisation_id == organisation.id, Membership.user_id == user.id)
        )
        if membership is not None:
            continue
        only = SITE_ROLES.get(role)
        membership = Membership(user_id=user.id, organisation_id=organisation.id, role=role, all_sites=only is None)
        session.add(membership)
        await session.flush()
        if only is not None:
            session.add(MembershipSite(membership_id=membership.id, site_id=sites[only].id))
    await session.flush()
    return organisation, sites


class ApiClient:
    """Calls the API as the demo admin.

    Attributes:
        http: HTTP client with the admin's access token.
    """

    def __init__(self, http: httpx.AsyncClient):
        """Wrap a signed-in client.

        Args:
            http: HTTP client with the admin's access token.
        """
        self.http = http

    @classmethod
    async def sign_in(cls, demo: DemoSettings) -> "ApiClient":
        """Sign in as the demo admin.

        Args:
            demo: Seed settings.

        Returns:
            The client.
        """
        http = httpx.AsyncClient(base_url=demo.api_url, timeout=60)
        response = await http.post(
            "/api/v1/auth/token",
            json={"email": demo.email("admin"), "password": demo.password, "device_name": "Development kit seed"},
        )
        response.raise_for_status()
        http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        return cls(http)

    async def call(self, method: str, path: str, site: Site, body: dict | None = None) -> dict:
        """Send one request for a site.

        Args:
            method: HTTP method.
            path: Path under /api/v1.
            site: Site the request is about.
            body: JSON body, if any.

        Returns:
            The JSON answer.

        Raises:
            RuntimeError: The API refused the request.
        """
        response = await self.http.request(method, f"/api/v1{path}", json=body, headers={"X-Site": str(site.uuid)})
        if not response.is_success:
            raise RuntimeError(f"{method} {path} answered {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.http.aclose()


async def site_hosts(api: ApiClient, site: Site) -> set[str]:
    """List the addresses of a site's cameras.

    Args:
        api: Signed-in client.
        site: The site.

    Returns:
        The camera addresses.
    """
    listing = await api.call("GET", "/cameras?page_size=100", site)
    return {item.get("device_host") or item["host"] for item in listing.get("items", [])}


async def add_store_site_cameras(api: ApiClient, demo: DemoSettings, site: Site, report: SeedReport) -> None:
    """Add the store cameras the API reaches directly, by address.

    Args:
        api: Signed-in client.
        demo: Seed settings.
        site: The store site.
        report: Report to count in.
    """
    existing = await site_hosts(api, site)
    for host, name in demo.store_names.items():
        if host in existing:
            continue
        await api.call(
            "POST",
            "/pairing/devices",
            site,
            {
                "method": "manual_host",
                "host": host,
                "port": 80,
                "username": "admin",
                "password": demo.camera_password,
                "name": name,
            },
        )
        report.cameras += 1


async def home_connector(api: ApiClient, demo: DemoSettings, site: Site) -> dict | None:
    """Return the home site's online connector, pairing the waiting one first when the site has none.

    Args:
        api: Signed-in client.
        demo: Seed settings.
        site: The home site.

    Returns:
        The connector, or None when no connector is online or waiting.
    """
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
    claimed = False
    while asyncio.get_running_loop().time() < deadline:
        connectors = (await api.call("GET", "/connectors", site)).get("items", [])
        online = [item for item in connectors if item.get("online")]
        if online:
            return online[0]
        if not claimed and not connectors:
            async with httpx.AsyncClient(timeout=10) as http:
                try:
                    pending = (await http.get(f"{demo.connector_url}/pairing")).json()
                except httpx.HTTPError:
                    pending = {}
            if pending.get("user_code"):
                await api.call("POST", "/connectors/claim", site, {"user_code": pending["user_code"], "name": "Home"})
                claimed = True
        await asyncio.sleep(POLL_SECONDS)
    return None


async def add_home_site_cameras(api: ApiClient, demo: DemoSettings, site: Site, report: SeedReport) -> None:
    """Find the home cameras through the connector and add the ones the site lacks.

    Args:
        api: Signed-in client.
        demo: Seed settings.
        site: The home site.
        report: Report to count in.
    """
    existing = await site_hosts(api, site)
    if set(demo.home_names) <= existing:
        return
    connector = await home_connector(api, demo, site)
    if connector is None:
        logger.warning("No connector is online or waiting for the home site; its cameras are skipped")
        return
    uuid = connector["uuid"]
    await api.call("POST", f"/connectors/{uuid}/discovery", site)
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
    found: dict[str, dict] = {}
    while asyncio.get_running_loop().time() < deadline:
        listing = await api.call("GET", f"/connectors/{uuid}/discovered", site)
        found = {item["host"]: item for item in listing.get("items", []) if item["host"] in demo.home_names}
        if set(found) >= set(demo.home_names) - existing:
            break
        await asyncio.sleep(POLL_SECONDS)
    for host, candidate in sorted(found.items()):
        if host in existing or candidate.get("camera"):
            continue
        await api.call(
            "POST",
            "/pairing/devices",
            site,
            {
                "method": "connector_discovery",
                "connector": uuid,
                "discovered": candidate["uuid"],
                "host": host,
                "port": candidate.get("onvif_port") or 80,
                "username": "admin",
                "password": demo.camera_password,
                "name": demo.home_names[host],
            },
        )
        report.cameras += 1


async def ensure_view(session: AsyncSession, camera: Camera, report: SeedReport) -> tuple[AnalyticsView, AnalyticsLine]:
    """Give a camera an analytics view with an entrance zone and a door line unless it has a view.

    The view is enabled, so the analytics service runs it when the kit starts the analytics profile; the zone alerts
    on people at any time and the line counts people both ways. A view the seed created before is enabled again.

    Args:
        session: Database session without a scope.
        camera: The camera.
        report: Report to count in.

    Returns:
        The view and its line.
    """
    view = await session.scalar(
        select(AnalyticsView).where(AnalyticsView.camera_uuid == camera.uuid, AnalyticsView.lens == 0)
    )
    if view is None:
        view = AnalyticsView(
            site_id=camera.site_id,
            organisation_id=camera.organisation_id,
            camera_uuid=camera.uuid,
            lens=0,
            name=camera.name,
            profile="perimeter",
            enabled=True,
        )
        session.add(view)
        await session.flush()
        session.add(
            AnalyticsZone(
                site_id=camera.site_id,
                organisation_id=camera.organisation_id,
                view_id=view.id,
                name="Entrance",
                polygon=ZONE,
                labels=["person"],
                loiter_seconds=30.0,
                alert={"mode": "always", "schedule": [], "labels": ["person"]},
            )
        )
        session.add(
            AnalyticsLine(
                site_id=camera.site_id,
                organisation_id=camera.organisation_id,
                view_id=view.id,
                name="Door",
                points=LINE,
                labels=["person"],
                direction="both",
                alert={"mode": "off", "schedule": [], "labels": []},
            )
        )
        await session.flush()
        report.views += 1
    elif not view.enabled:
        seeded = await session.scalar(
            select(AnalyticsZone.id).where(AnalyticsZone.view_id == view.id, AnalyticsZone.name == "Entrance").limit(1)
        )
        if seeded is not None:
            view.enabled = True
    line = await session.scalar(select(AnalyticsLine).where(AnalyticsLine.view_id == view.id).limit(1))
    return view, line


async def ensure_store_switch(session: AsyncSession, site: Site, demo: DemoSettings, report: SeedReport) -> None:
    """Give the store a network device the API can probe, with its camera linked to it, unless it has one.

    The store LAN's go2rtc address stands in for the switch, so the store's health page has a real device and a
    camera behind it.

    Args:
        session: Database session without a scope.
        site: The store site.
        demo: Seed settings.
        report: Report to count in.
    """
    device = await session.scalar(
        select(NetworkDevice).where(NetworkDevice.site_id == site.id, NetworkDevice.name == "Store switch")
    )
    if device is None:
        device = NetworkDevice(
            site_id=site.id,
            organisation_id=site.organisation_id,
            name="Store switch",
            kind="switch",
            host=demo.store_switch,
            probe_port=1984,
            enabled=True,
        )
        session.add(device)
        await session.flush()
        report.devices += 1
    cameras = await session.scalars(
        select(Camera).where(Camera.site_id == site.id, Camera.deleted_at.is_(None), Camera.connector_id.is_(None))
    )
    for camera in cameras:
        linked = await session.scalar(select(CameraLink.id).where(CameraLink.camera_uuid == camera.uuid))
        if linked is None:
            session.add(
                CameraLink(
                    site_id=site.id,
                    organisation_id=site.organisation_id,
                    camera_uuid=camera.uuid,
                    parent_uuid=device.uuid,
                    link="cable",
                )
            )
    await session.flush()


async def fetch_frame(stream: str, width: int | None = None) -> bytes | None:
    """Fetch one picture of a stream from go2rtc, waiting for the stream to start.

    Args:
        stream: go2rtc stream name.
        width: Width to scale to, the stream's own when None.

    Returns:
        The JPEG, or None when the stream gives no picture in time.
    """
    params = [("src", stream)] + ([("width", str(width))] if width else [])
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
    async with httpx.AsyncClient(timeout=20) as http:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await http.get(f"{settings.go2rtc_url.rstrip('/')}/api/frame.jpeg", params=params)
                if response.status_code == 200 and response.content[:2] == b"\xff\xd8":
                    return response.content
            except httpx.HTTPError:
                pass
            await asyncio.sleep(POLL_SECONDS)
    return None


def write_picture(relative: Path, content: bytes) -> str:
    """Write a picture into the event store.

    Args:
        relative: Path relative to the event store.
        content: JPEG bytes.

    Returns:
        The relative path in POSIX form.
    """
    target = Path(settings.events_dir) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return relative.as_posix()


async def ensure_events(
    session: AsyncSession, camera: Camera, view: AnalyticsView, rng: random.Random, report: SeedReport
) -> None:
    """Write a week of analytics events with snapshots for a camera unless the seed wrote them before.

    Args:
        session: Database session without a scope.
        camera: The camera.
        view: Its analytics view.
        rng: Random source, seeded for repeatable data.
        report: Report to count in.
    """
    seeded = await session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.camera_uuid == camera.uuid, Event.details["seeded"].astext == SEED_MARK)
    )
    if seeded:
        return
    stream = stream_name(camera.uuid, MAIN)
    full = await fetch_frame(stream)
    thumbnail = await fetch_frame(stream, THUMBNAIL_WIDTH)
    if full is None:
        logger.warning("Camera %s gave no picture; its events are written without snapshots", camera.name)
    now = datetime.now(UTC)
    for index in range(EVENTS_PER_CAMERA):
        kind, label = EVENT_KINDS[index % len(EVENT_KINDS)]
        at = now - timedelta(minutes=rng.randint(5, EVENT_DAYS * 24 * 60))
        uuid = uuid4()
        folder = Path(f"{at:%Y}") / f"{at:%m}" / f"{at:%d}"
        session.add(
            Event(
                uuid=uuid,
                site_id=camera.site_id,
                organisation_id=camera.organisation_id,
                camera_uuid=camera.uuid,
                kind=kind,
                source="analytics",
                source_topic=f"analytics/{kind}",
                label=label,
                alert=kind == "intrusion",
                view_uuid=view.uuid,
                started_at=at,
                ended_at=at + timedelta(seconds=rng.randint(2, 40)),
                snapshot_path=write_picture(folder / f"{uuid}.jpg", full) if full else None,
                thumbnail_path=write_picture(folder / f"{uuid}.thumb.jpg", thumbnail) if thumbnail else None,
                details={"seeded": SEED_MARK, "zone": "Entrance" if kind == "intrusion" else None},
            )
        )
        report.events += 1
    await session.flush()


async def ensure_counts(
    session: AsyncSession, view: AnalyticsView, line: AnalyticsLine, rng: random.Random, report: SeedReport
) -> None:
    """Write a week of minute line counts, busier in the day, unless the line has counts.

    Args:
        session: Database session without a scope.
        view: The view.
        line: Its line.
        rng: Random source, seeded for repeatable data.
        report: Report to count in.
    """
    existing = await session.scalar(
        select(func.count()).select_from(AnalyticsCount).where(AnalyticsCount.line_uuid == line.uuid)
    )
    if existing:
        return
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    for step in range(EVENT_DAYS * 24):
        start = hour - timedelta(hours=step)
        busy = 8 <= start.hour <= 20
        for direction in ("in", "out"):
            count = rng.randint(2, 14) if busy else rng.randint(0, 2)
            if not count:
                continue
            session.add(
                AnalyticsCount(
                    site_id=view.site_id,
                    organisation_id=view.organisation_id,
                    boot=BOOT,
                    view_uuid=view.uuid,
                    line_uuid=line.uuid,
                    start=start + timedelta(minutes=rng.randint(0, 59)),
                    direction=direction,
                    label="person",
                    count=count,
                )
            )
            report.counts += 1
    await session.flush()


async def seed(demo: DemoSettings) -> SeedReport:
    """Create the demo data.

    Args:
        demo: Seed settings.

    Returns:
        What the run created.

    Raises:
        SystemExit: SSS_DEMO_PASSWORD is empty.
    """
    if not demo.password:
        raise SystemExit("Set SSS_DEMO_PASSWORD in deploy/devkit/.env; scripts/devkit.sh init generates one")
    report = SeedReport()
    async with async_session_maker() as session:
        users = await ensure_users(session, demo, report)
        _, sites = await ensure_organisation(session, users, report)
        await session.commit()
    api = await ApiClient.sign_in(demo)
    try:
        await add_store_site_cameras(api, demo, sites["Store"], report)
        await add_home_site_cameras(api, demo, sites["Home"], report)
    finally:
        await api.close()
    rng = random.Random(20261120)
    async with async_session_maker() as session:
        site_ids = [site.id for site in sites.values()]
        cameras = list(
            await session.scalars(
                select(Camera).where(Camera.site_id.in_(site_ids), Camera.deleted_at.is_(None)).order_by(Camera.id)
            )
        )
        await ensure_store_switch(session, sites["Store"], demo, report)
        for camera in cameras:
            view, line = await ensure_view(session, camera, report)
            await ensure_events(session, camera, view, rng, report)
            await ensure_counts(session, view, line, rng, report)
        await session.commit()
    return report


def main() -> None:
    """Run the seed and print what it created."""
    parser = argparse.ArgumentParser(description="Create the development kit's demo data")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    report = asyncio.run(seed(DemoSettings()))
    print(
        f"Demo seeded: {report.users} members, {report.sites} sites, {report.cameras} cameras, "
        f"{report.views} views, {report.devices} network devices, {report.events} events, "
        f"{report.counts} count rows created"
    )


if __name__ == "__main__":
    main()
