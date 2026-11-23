"""Workers: the consumers of the event bus, each run as its own container from the API image.

python -m app.workers event-store, notifier or metering runs one; every replica of a worker shares its durable
consumers, so a worker scales by running more containers of it.
"""
