"""Command line of the event bus: provision the streams and consumers, or list them.

Usage:
    python -m event_bus provision [nats://nats:4222]
    python -m event_bus streams [nats://nats:4222]
"""

import asyncio
import json
import os
import sys

from event_bus.jetstream import NatsBus
from event_bus.streams import CONSUMERS, STREAMS, provision

DEFAULT_URL = "nats://nats:4222"


async def run(command: str, url: str) -> int:
    """Run one command against a NATS server.

    Args:
        command: provision or streams.
        url: NATS server URL.

    Returns:
        The exit status.
    """
    bus = NatsBus(url, name="event-bus-cli")
    await bus.connect()
    try:
        if command == "provision":
            await provision(bus)
            print(f"Provisioned {len(STREAMS)} streams and {len(CONSUMERS)} consumers")
            return 0
        if command == "streams":
            js = bus.context()
            for spec in STREAMS:
                info = await js.stream_info(spec.name)
                print(json.dumps({"stream": spec.name, "messages": info.state.messages, "bytes": info.state.bytes}))
            return 0
    finally:
        await bus.close()
    print(__doc__, file=sys.stderr)
    return 2


def main() -> None:
    """Parse the arguments and run the command."""
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    url = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SSS_NATS_URL", DEFAULT_URL)
    sys.exit(asyncio.run(run(sys.argv[1], url)))


if __name__ == "__main__":
    main()
