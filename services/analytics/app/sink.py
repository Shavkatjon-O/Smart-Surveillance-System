"""Where the service's event changes and observations go: the event bus, or the backend's intake routes.

With SSS_NATS_URL set the service publishes every event change on its view's subject and every view's observation
on the bus, through a publisher that retries and keeps a bounded local buffer while the bus is away. Without it,
for an engine that cannot reach a bus, they are posted to the backend's internal intake routes, which publish
them on the bus for it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from event_bus import Bus, Envelope, EventType, ThreadedPublisher, subjects

from app.backend import Outbox

logger = logging.getLogger(__name__)

SOURCE = "/sss/analytics"
EVENTS_PATH = "/internal/analytics/events"
OBSERVATIONS_PATH = "/internal/analytics/observations"

Owner = tuple[str | None, str | None]


def nats_factory(url: str) -> Callable[[], Bus]:
    """Build the factory of the NATS bus.

    Args:
        url: NATS server URL.

    Returns:
        A function that builds the bus inside the publisher's thread.
    """

    def build() -> Bus:
        """Build the bus.

        Returns:
            The bus, not yet connected.
        """
        from event_bus.jetstream import NatsBus

        return NatsBus(url, name="sss-analytics", connect_wait=10.0)

    return build


class EventSink:
    """Sends event changes and observations to the bus, or to the backend when no bus is configured.

    Attributes:
        publisher: The bus publisher, None without a bus.
        outbox: The backend outbox, used without a bus.
    """

    def __init__(self, nats_url: str, outbox: Outbox | None, factory: Callable[[], Bus] | None = None):
        """Choose where to send.

        Args:
            nats_url: NATS server URL, empty to post to the backend.
            outbox: The backend outbox, None when running from a file.
            factory: Builds the bus; NATS at nats_url when omitted.
        """
        self.outbox = outbox
        self.publisher: ThreadedPublisher | None = None
        if nats_url or factory is not None:
            self.publisher = ThreadedPublisher(factory or nats_factory(nats_url))

    @property
    def active(self) -> bool:
        """bool: Whether anything receives what the sink sends."""
        return self.publisher is not None or self.outbox is not None

    def start(self) -> None:
        """Start the bus publisher's thread."""
        if self.publisher is not None:
            self.publisher.start()

    def stop(self) -> None:
        """Stop the bus publisher, sending what it still holds when it can."""
        if self.publisher is not None:
            self.publisher.stop()

    def event(self, event: dict, owner: Owner) -> None:
        """Send one event change.

        Args:
            event: The change with its action, in the fields of sss.analytics.event.v1.
            owner: Organisation and site ids of the event's view.
        """
        if self.publisher is not None:
            organisation, site = owner
            envelope = Envelope.create(
                EventType.ANALYTICS_EVENT, SOURCE, event, subject=event["id"], organisation=organisation, site=site
            )
            self.publisher.submit(subjects.analytics_event(organisation, site, event["view"]), envelope)
        elif self.outbox is not None:
            self.outbox.send(EVENTS_PATH, event)

    def observations(self, body: dict, owners: Callable[[str], Owner]) -> None:
        """Send a report of every view; the bus receives one observation per view, which the next one replaces.

        Args:
            body: The report with at, pipeline, gpu and views.
            owners: Returns the organisation and site ids of a view.
        """
        if self.publisher is not None:
            for view in body["views"]:
                organisation, site = owners(str(view["id"]))
                envelope = Envelope.create(
                    EventType.OBSERVATION,
                    SOURCE,
                    {"at": body["at"], "pipeline": body["pipeline"], "gpu": body["gpu"], "view": view},
                    subject=view["id"],
                    organisation=organisation,
                    site=site,
                )
                self.publisher.submit(
                    subjects.analytics_observation(organisation, site, view["id"]), envelope, droppable=True
                )
        elif self.outbox is not None:
            self.outbox.send(OBSERVATIONS_PATH, body, droppable=True)

    def status(self) -> dict | None:
        """Describe the bus publisher for the status endpoint.

        Returns:
            Its counters, or None without a bus.
        """
        return self.publisher.stats() if self.publisher is not None else None
