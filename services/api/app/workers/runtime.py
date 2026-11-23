"""What every worker shares: the bus connection, the heartbeat file and running its consumers until stopped."""

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.bus.connection import BusConnection, bus
from app.core.database import async_engine, verify_db_connection
from event_bus import ConsumerRunner, ConsumerSpec, Delivery, Envelope

__all__ = ["HEARTBEAT_FILE", "HEARTBEAT_SECONDS", "Worker", "beat"]

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = Path("/tmp/worker-heartbeat")
HEARTBEAT_SECONDS = 60

Handler = Callable[[Envelope, Delivery], Awaitable[None]]


def beat(path: Path = HEARTBEAT_FILE) -> None:
    """Touch the heartbeat file the container's healthcheck reads.

    Args:
        path: The file.
    """
    try:
        path.touch()
    except OSError as exc:
        logger.debug("Touching the heartbeat failed: %s", exc)


class Worker:
    """Runs a worker's consumers and background tasks on one bus connection until a signal stops it.

    Attributes:
        name: Worker name, also its connection name on the bus.
        connection: The bus connection.
        consumers: Durable consumers with their handlers.
        tasks: Further coroutine functions to run beside the consumers.
        runners: The consumer runners, once started.
    """

    def __init__(
        self,
        name: str,
        consumers: list[tuple[ConsumerSpec, Handler]],
        tasks: list[Callable[[], Awaitable[None]]] | None = None,
        connection: BusConnection = bus,
    ):
        """Describe the worker.

        Args:
            name: Worker name.
            consumers: Durable consumers with their handlers.
            tasks: Further coroutine functions to run.
            connection: The bus connection.
        """
        self.name = name
        self.connection = connection
        self.consumers = consumers
        self.tasks = tasks or []
        self.runners: list[ConsumerRunner] = []

    async def run(self) -> None:
        """Check the database, connect to the bus, then consume until SIGTERM or SIGINT."""
        await verify_db_connection()
        self.connection.name = f"sss-{self.name}"
        await self.connection.start()
        beat()
        self.runners = [
            ConsumerRunner(self.connection.current(), spec, handler, on_tick=beat) for spec, handler in self.consumers
        ]
        running = [asyncio.create_task(runner.run(), name=runner.spec.durable) for runner in self.runners]
        running += [asyncio.create_task(task(), name=f"{self.name}-task") for task in self.tasks]
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        for number in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(number, stopping.set)
        logger.info("Worker %s started with %d consumers", self.name, len(self.runners))
        finished = asyncio.create_task(stopping.wait())
        done, _ = await asyncio.wait([finished, *running], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not finished and task.exception() is not None:
                logger.error("Task %s of worker %s failed: %s", task.get_name(), self.name, task.exception())
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        finished.cancel()
        await self.connection.stop()
        await async_engine.dispose()
        logger.info("Worker %s stopped after %s", self.name, self.summary())

    def summary(self) -> str:
        """Describe what the consumers did.

        Returns:
            Handled, retried and dead letter counts per consumer.
        """
        return ", ".join(
            f"{runner.spec.durable} handled {runner.handled} retried {runner.retried} dead {runner.dead}"
            for runner in self.runners
        )


def stale(path: Path = HEARTBEAT_FILE, seconds: float = HEARTBEAT_SECONDS) -> bool:
    """Tell whether the heartbeat is missing or older than allowed, for the healthcheck.

    Args:
        path: The file.
        seconds: Largest allowed age.

    Returns:
        True when the worker looks stuck.
    """
    try:
        return time.time() - path.stat().st_mtime > seconds
    except OSError:
        return True
