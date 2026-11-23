# Event bus

The Python package event_bus: the message bus every event of Smart Surveillance System travels on. It holds the CloudEvents envelope, the subject names, the JetStream streams and durable consumers with their limits, a publisher with acknowledgement, bounded retry and a local buffer, a consumer runner with acknowledgement, delayed redelivery and dead letters, and an in-memory bus for tests. The bus runs on NATS with JetStream through the official nats-py client, 2.17, under the Apache 2.0 licence. Producers and consumers see only the Bus interface in event_bus/bus.py, so a Kafka adapter can join without changing either. The package imports nothing from services or apps. The root README describes the product; docs/architecture.md describes the event pipeline.

## Layout

| Module | Role |
|---|---|
| envelope | Envelope: CloudEvents 1.0 in the structured JSON mode with the organisation and site extensions, and stable_id for message ids derived from what a message is about |
| subjects | Subject builders and patterns, and EventType with every versioned event type |
| streams | StreamSpec and ConsumerSpec, the platform's streams and durable consumers, and provision |
| bus | The Bus and Delivery interfaces and the errors |
| jetstream | NatsBus, the bus on NATS JetStream |
| memory | MemoryBus, the same semantics in memory: message ids, redelivery, delivery limits, last message per subject and going offline |
| publisher | Publisher for asyncio producers and ThreadedPublisher for producers that run threads |
| consumer | ConsumerRunner, Retry, Reject and the dead letter envelope |

## Envelope

Every message is one JSON envelope with specversion 1.0, id, source, type, subject, time, datacontenttype application/json and data, plus the extension attributes organisation and site, the public ids of the tenant. The id is a UUID that identifies the message and is its idempotency key: a producer that retries publishes the same envelope, and the publisher sends the id as Nats-Msg-Id, so the stream stores it once within its duplicate window. A message that is built again from the same facts takes a name-based id from stable_id, such as the alert of an event, so it is deduplicated even when two processes build it. The subject attribute names what the message is about, such as the surveillance event's id for an event change. The JSON Schemas of the envelope and of every type's data are in packages/contracts/events, one file per type named after it, such as sss.analytics.event.v1.schema.json; a type changes its version when its data changes incompatibly.

## Subjects

| Subject | Type | Producer | Consumer |
|---|---|---|---|
| sss.organisation.site.camera.camera.event | sss.camera.event.v1, sss.event.interrupted.v1 | The API's ONVIF subscriber | event-store |
| sss.organisation.site.analytics.view.event | sss.analytics.event.v1, sss.event.interrupted.v1 | The analytics engine, or the API's intake route for an engine that posts | event-store |
| sss.organisation.site.analytics.view.observation | sss.analytics.observation.v1 | The analytics engine, or the intake route | every API replica |
| sss.organisation.site.live | sss.live.event.v1, sss.live.source.v1, sss.live.source-removed.v1 | event-store, the face route and the ONVIF subscriber | every API replica, for its live event stream |
| sss.organisation.alert | sss.alert.v1 | event-store and the face route | notifier-telegram, notifier-push |
| sss.organisation.usage | sss.usage.v1 | notifier | metering |
| sss.connector.connector.telemetry | sss.connector.telemetry.v1 | Connectors, later | none yet |
| sss.deadletter.stream.consumer | sss.deadletter.v1 | Every consumer runner | People, with the commands under Run |

Each lower-case word stands for a public id: the organisation, the site, the camera, the view or the connector. An owner that is not assigned yet is the token none. The live subject is plain NATS fan-out and no stream captures it, because every API replica needs every message once and a replica that reconnects tells its clients to reload, which gives them the current state.

## Streams

| Stream | Subjects | Storage | Kept | Duplicate window | Why |
|---|---|---|---|---|---|
| SSS_EVENTS | camera and analytics events | file | 3 days or 512 MiB, oldest dropped first | 10 minutes | A change is one to two kilobytes and a busy site makes a few thousand a day, so the limit holds weeks of a home and days of a large site; 3 days outlasts a weekend outage of the database or the event store, and the window covers a producer that retries a message whose acknowledgement it lost |
| SSS_OBSERVATIONS | observations | memory | the last message per view, at most 10 minutes or 16 MiB | 2 minutes | Only the latest status of a view matters and the engine sends a new one every 5 s, so nothing needs a disk; the age drops views that stopped reporting |
| SSS_ALERTS | alerts | file | 1 day or 64 MiB | 1 hour | An alert is useful for minutes and the notifier's retries end after about 80 minutes; the window, longer than an event's changes take to arrive, keeps one alert per event |
| SSS_USAGE | usage | file | 7 days or 64 MiB | 1 hour | Usage is small and billing must not lose it while metering is down for a few days |
| SSS_TELEMETRY | connector telemetry | file | 1 day, 1000 per connector, or 64 MiB | 2 minutes | Reserved for connectors, which will report often and need only recent history |
| SSS_DEADLETTER | dead letters | file | 14 days or 128 MiB | 1 hour | Two weeks is long enough for someone to notice, read and replay a failure |

Every stream keeps messages by limits rather than until they are consumed, so a consumer added later, or one that was reset, can read what the stream still holds, and two consumers can read the same alerts. The server's JetStream store is capped at 64 MiB of memory and 2 GiB of disk in deploy/server/nats/nats.conf, above the sum of the streams' limits.

## Consumers

Every consumer is a durable pull consumer with explicit acknowledgement, shared by all replicas of its worker: the server hands each message to one of them.

| Consumer | Stream | Acknowledgement wait | Retries | Does |
|---|---|---|---|---|
| event-store | SSS_EVENTS | 30 s | 8, from 1 s to 5 minutes | Stores each change in Postgres idempotently by the event's id, then sends the stored event to the live subject and raises the event's alert once |
| notifier-telegram | SSS_ALERTS | 120 s | 10, from 5 s to 15 minutes | Sends the alert to the organisation's Telegram chats |
| notifier-push | SSS_ALERTS | 120 s | 10, from 5 s to 15 minutes | Pushes the alert to the members' devices |
| metering | SSS_USAGE | 30 s | 8, from 1 s to 5 minutes | Adds counted usage to the hourly rollups |

## Delivery

- Producers publish with acknowledgement and up to 3 attempts. When the bus stays away, the publisher keeps up to 2000 messages in memory in order, dropping the oldest when full, and sends them as soon as the bus answers; a new message waits behind older buffered ones, so order is kept. Observations are dropped rather than buffered, because the next one replaces them.
- Consumers receive every message at least once. A handler that returns acknowledges its message; one that raises has it delivered again after the next delay of the consumer's backoff; after the last delay, or at once for Reject, the runner publishes a dead letter with the reason and the original envelope to sss.deadletter.stream.consumer and ends the message. The server stops delivering two attempts later on its own, for a message that crashes its worker before the runner can act.
- Handlers are idempotent, because a message can arrive twice: the event store upserts by the event's id and only ever turns the alert on or sets an end; the alert's id derives from the event's id; usage ids derive from the alert and the channel; a notifier channel remembers which chats and devices already received an alert while it retries.

## Run

The package is a uv workspace member installed with the API image, and the analytics image installs it with pip. Provision the streams and consumers, or count the messages of each stream, from any container on the bus network:

```
python -m event_bus provision nats://nats:4222
python -m event_bus streams nats://nats:4222
```

The API and the workers provision at every start, so the first command is only needed on a bus nothing else has started on. Read the dead letters with the nats command line tool, for example from the server target's folder:

```
docker run --rm --network smart-surveillance-system_bus natsio/nats-box:0.20.2 nats --server nats://nats:4222 stream view SSS_DEADLETTER
```

## Test

Run from the repository root; the JetStream tests also run when SSS_TEST_NATS_URL names a NATS server with JetStream, and skip otherwise:

```
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uv-cache -v $PWD:/repo -w /repo/packages/event-bus ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c "uv sync --frozen --all-packages && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen pytest -q"
```

.github/workflows/event-bus.yml runs the same with a NATS service container, so the JetStream tests run in CI.

## Configuration

The package reads no environment variables except SSS_NATS_URL in its command line. Services pass the server address to NatsBus: the API and its workers read SSS_NATS_URL, and so does the analytics engine.
