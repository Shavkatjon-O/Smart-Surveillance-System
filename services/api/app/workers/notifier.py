"""The notifier worker: one consumer per channel sends every alert, retries what failed and reports usage."""

import logging
from collections import OrderedDict

from app.bus.connection import BusConnection, bus
from app.modules.alerting.services import PushChannel, TelegramChannel
from app.modules.billing.enums import UsageMetric
from event_bus import ConsumerSpec, Delivery, Envelope, EventType, Retry, stable_id, subjects
from event_bus.streams import NOTIFIER_PUSH, NOTIFIER_TELEGRAM

__all__ = ["REMEMBERED_ALERTS", "SOURCE", "ChannelWorker", "channel_workers"]

logger = logging.getLogger(__name__)

SOURCE = "/sss/notifier"
REMEMBERED_ALERTS = 4096


class ChannelWorker:
    """Sends the alerts of the bus on one channel.

    A failed recipient that may succeed later makes the alert come back after the consumer's backoff; the
    recipients that already received it are remembered, so the retry reaches only the others. Every attempt that
    reached someone publishes the notifications it sent as usage.

    Attributes:
        channel: The channel, Telegram or push.
        spec: Its durable consumer.
        connection: The bus connection.
        done: Recipients that received each recent alert, by alert id.
    """

    def __init__(self, channel, spec: ConsumerSpec, connection: BusConnection = bus):
        """Describe the worker.

        Args:
            channel: The channel.
            spec: Its durable consumer.
            connection: The bus connection.
        """
        self.channel = channel
        self.spec = spec
        self.connection = connection
        self.done: OrderedDict[str, set] = OrderedDict()

    def recipients(self, alert_id: str) -> set:
        """Return the remembered recipients of an alert.

        Args:
            alert_id: The alert's id.

        Returns:
            The set, which the channel updates.
        """
        done = self.done.setdefault(alert_id, set())
        self.done.move_to_end(alert_id)
        while len(self.done) > REMEMBERED_ALERTS:
            self.done.popitem(last=False)
        return done

    async def handle(self, envelope: Envelope, delivery: Delivery) -> None:
        """Send one alert.

        Args:
            envelope: The alert.
            delivery: Its delivery.

        Raises:
            Retry: Some recipients failed in a way a retry may cure.
        """
        if envelope.type != EventType.ALERT:
            return
        result = await self.channel.send(envelope.data, self.recipients(envelope.id))
        if result.sent:
            usage = Envelope.create(
                EventType.USAGE,
                SOURCE,
                {
                    "metric": UsageMetric.NOTIFICATIONS.value,
                    "amount": result.sent,
                    "at": envelope.time,
                    "channel": self.channel.name,
                },
                subject=envelope.subject,
                organisation=envelope.organisation,
                site=envelope.site,
                id=stable_id("usage", envelope.id, self.channel.name, delivery.attempt),
            )
            await self.connection.emit(subjects.usage(envelope.organisation), usage)
            logger.info("Sent alert %s on %s to %d recipients", envelope.subject, self.channel.name, result.sent)
        if result.pending:
            raise Retry(f"{result.pending} {self.channel.name} recipients failed")


def channel_workers(connection: BusConnection = bus) -> list[ChannelWorker]:
    """Build the workers of both channels.

    Args:
        connection: The bus connection.

    Returns:
        The Telegram and push workers.
    """
    return [
        ChannelWorker(TelegramChannel(), NOTIFIER_TELEGRAM, connection),
        ChannelWorker(PushChannel(), NOTIFIER_PUSH, connection),
    ]
