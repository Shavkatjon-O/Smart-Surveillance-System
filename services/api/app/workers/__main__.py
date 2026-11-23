"""Run one worker: python -m app.workers event-store, notifier or metering, or health to check one's heartbeat."""

import asyncio
import logging
import sys

from app.core.logging import setup_logging
from app.workers.runtime import Worker, stale

WORKERS = ("event-store", "notifier", "metering")

logger = logging.getLogger(__name__)


def build(name: str) -> Worker:
    """Build a worker by name.

    Args:
        name: event-store, notifier or metering.

    Returns:
        The worker.

    Raises:
        SystemExit: The name is unknown.
    """
    if name == "event-store":
        from app.workers.event_store import EventStoreWorker

        store = EventStoreWorker()
        return Worker(name, [(store.spec, store.handle)])
    if name == "notifier":
        from app.workers.notifier import channel_workers

        return Worker(name, [(worker.spec, worker.handle) for worker in channel_workers()])
    if name == "metering":
        from app.modules.billing.services.metering import usage_meter
        from app.workers.metering import MeteringWorker

        meter = MeteringWorker()
        return Worker(name, [(meter.spec, meter.handle)], tasks=[usage_meter.run])
    raise SystemExit(f"Unknown worker {name!r}; choose one of {', '.join(WORKERS)} or health")


def main() -> None:
    """Parse the arguments and run the worker, or check its heartbeat."""
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: python -m app.workers {{{'|'.join(WORKERS)}|health}}")
    if sys.argv[1] == "health":
        sys.exit(1 if stale() else 0)
    setup_logging()
    asyncio.run(build(sys.argv[1]).run())


if __name__ == "__main__":
    main()
