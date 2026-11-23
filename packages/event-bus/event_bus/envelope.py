"""CloudEvents 1.0 envelope of every message on the bus, in the structured JSON content mode."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

__all__ = ["CONTENT_TYPE", "DATA_CONTENT_TYPE", "NAMESPACE", "SPEC_VERSION", "Envelope", "EnvelopeError", "stable_id"]

SPEC_VERSION = "1.0"
DATA_CONTENT_TYPE = "application/json"
CONTENT_TYPE = "application/cloudevents+json"
NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/Shavkatjon-O/Smart-Surveillance-System/events")

REQUIRED = ("specversion", "id", "source", "type")
ATTRIBUTES = ("id", "source", "type", "subject", "time", "datacontenttype", "organisation", "site")


class EnvelopeError(ValueError):
    """A message is not a valid envelope."""


def stable_id(*parts: object) -> str:
    """Derive the same message id from the same parts, so a message that is built again is deduplicated.

    Args:
        *parts: What identifies the message, such as an event id and a channel.

    Returns:
        A name-based UUID as text.
    """
    return str(uuid5(NAMESPACE, ":".join(str(part) for part in parts)))


def now_text() -> str:
    """Return the current time in RFC 3339 form.

    Returns:
        The time in UTC with microseconds.
    """
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Envelope:
    """One CloudEvents 1.0 event: context attributes, the organisation and site extensions, and JSON data.

    The id is a UUID that identifies the message and is its idempotency key: a producer that retries sends the same
    envelope, and the bus publishes the id as Nats-Msg-Id so the stream stores it once.

    Attributes:
        id: Message id, a UUID as text.
        source: URI reference of the producer, such as /sss/analytics.
        type: Versioned event type, such as sss.analytics.event.v1.
        data: The payload, a JSON object.
        subject: What the event is about within the producer, such as the surveillance event's id.
        time: When it happened, RFC 3339.
        organisation: Extension attribute: public id of the organisation, None for platform messages.
        site: Extension attribute: public id of the site, None for organisation-wide messages.
        datacontenttype: Media type of data, always application/json.
        specversion: CloudEvents version, always 1.0.
    """

    id: str
    source: str
    type: str
    data: dict = field(default_factory=dict)
    subject: str | None = None
    time: str = field(default_factory=now_text)
    organisation: str | None = None
    site: str | None = None
    datacontenttype: str = DATA_CONTENT_TYPE
    specversion: str = SPEC_VERSION

    @classmethod
    def create(
        cls,
        type: str,
        source: str,
        data: dict,
        subject: str | None = None,
        organisation: object | None = None,
        site: object | None = None,
        id: str | None = None,
        time: str | None = None,
    ) -> Envelope:
        """Build an envelope with a new id unless one is given.

        Args:
            type: Versioned event type.
            source: URI reference of the producer.
            data: The payload.
            subject: What the event is about, if anything.
            organisation: Public id of the organisation, if any.
            site: Public id of the site, if any.
            id: Message id; a random UUID when omitted.
            time: When it happened; now when omitted.

        Returns:
            The envelope.
        """
        return cls(
            id=id or str(uuid4()),
            source=source,
            type=type,
            data=data,
            subject=str(subject) if subject is not None else None,
            time=time or now_text(),
            organisation=str(organisation) if organisation is not None else None,
            site=str(site) if site is not None else None,
        )

    def to_dict(self) -> dict:
        """Return the envelope as a JSON object, leaving out optional attributes that are not set.

        Returns:
            The CloudEvents attributes and data.
        """
        body = {"specversion": self.specversion}
        for name in ATTRIBUTES:
            value = getattr(self, name)
            if value is not None:
                body[name] = value
        body["data"] = self.data
        return body

    def to_bytes(self) -> bytes:
        """Encode the envelope as compact JSON.

        Returns:
            UTF-8 JSON.
        """
        return json.dumps(self.to_dict(), separators=(",", ":"), default=str).encode()

    @classmethod
    def from_dict(cls, body: dict) -> Envelope:
        """Read an envelope from its JSON object.

        Args:
            body: The decoded JSON.

        Returns:
            The envelope.

        Raises:
            EnvelopeError: A required attribute is missing or invalid.
        """
        if not isinstance(body, dict):
            raise EnvelopeError("An envelope is a JSON object")
        missing = [name for name in REQUIRED if not body.get(name)]
        if missing:
            raise EnvelopeError(f"The envelope has no {', '.join(missing)}")
        if body["specversion"] != SPEC_VERSION:
            raise EnvelopeError(f"Unsupported CloudEvents version {body['specversion']}")
        try:
            UUID(str(body["id"]))
        except ValueError:
            raise EnvelopeError("The envelope id is not a UUID") from None
        data = body.get("data", {})
        if not isinstance(data, dict):
            raise EnvelopeError("The envelope data is not a JSON object")
        content = body.get("datacontenttype", DATA_CONTENT_TYPE)
        if content != DATA_CONTENT_TYPE:
            raise EnvelopeError(f"Unsupported data content type {content}")
        return cls(
            id=str(body["id"]),
            source=str(body["source"]),
            type=str(body["type"]),
            data=data,
            subject=body.get("subject"),
            time=str(body.get("time") or now_text()),
            organisation=body.get("organisation"),
            site=body.get("site"),
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> Envelope:
        """Decode an envelope from a message payload.

        Args:
            payload: UTF-8 JSON.

        Returns:
            The envelope.

        Raises:
            EnvelopeError: The payload is not JSON or not a valid envelope.
        """
        try:
            body = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EnvelopeError(f"The payload is not JSON: {exc}") from None
        return cls.from_dict(body)
