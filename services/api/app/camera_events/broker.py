"""Fan-out of live camera event messages to connected listeners."""

import asyncio
from contextlib import contextmanager

QUEUE_SIZE = 200


class EventBroker:
    """Hold one bounded queue per listener and copy each published message into all of them.

    Attributes:
        queues: Queues of the listeners that are currently subscribed.
    """

    def __init__(self):
        """Create a broker with no listeners."""
        self.queues: set[asyncio.Queue] = set()

    @contextmanager
    def subscribe(self):
        """Register a listener queue for the duration of a with block.

        The queue is removed again when the block exits.

        Yields:
            The queue that receives every message published while the block is open.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.queues.add(queue)
        try:
            yield queue
        finally:
            self.queues.discard(queue)

    def publish(self, kind: str, payload: dict) -> None:
        """Queue a message for every listener.

        A listener whose queue is full is emptied and receives one overflow message instead of the new message, so
        it can tell it missed messages; the web app reconnects when it gets one.

        Args:
            kind: Message type, stored under the type key of the message.
            payload: Message data, stored under the data key of the message.
        """
        message = {"type": kind, "data": payload}
        for queue in list(self.queues):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "overflow", "data": {}})
                continue
            queue.put_nowait(message)

    def reset(self) -> None:
        """Empty every listener's queue and send it one overflow message, so the web app reconnects and reloads."""
        for queue in list(self.queues):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({"type": "overflow", "data": {}})
