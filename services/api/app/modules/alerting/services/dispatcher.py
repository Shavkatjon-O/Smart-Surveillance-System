"""Alert dispatch: every alert from the bus reaches the organisation's Telegram chats and the members' devices.

Each channel runs as its own consumer of the alert stream in the notifier worker, so a channel that fails retries
alone and the other is never sent twice.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_maker
from app.models import AuthSession, Camera, Membership, MembershipSite, PushTarget, Site
from app.modules.alerting.services.providers import providers
from app.modules.alerting.services.targets import RETRY, SENT, deliver_each
from app.modules.alerting.services.telegram import TelegramBot, telegram_bot

__all__ = ["ChannelResult", "PushChannel", "TelegramChannel", "alert_payload", "recipients"]


@dataclass(frozen=True)
class ChannelResult:
    """What one attempt to send an alert on a channel achieved.

    Attributes:
        sent: Recipients that received it on this attempt.
        pending: Recipients that failed in a way a retry may cure.
    """

    sent: int = 0
    pending: int = 0


def alert_payload(event: dict, site_uuid: UUID) -> dict[str, str]:
    """Build the data of an alert push, which carries ids only.

    Args:
        event: The event message.
        site_uuid: Site of its camera.

    Returns:
        Type, event, camera and site ids as text.
    """
    return {
        "type": "alert",
        "event": str(event["uuid"]),
        "camera": str(event["camera_uuid"]),
        "site": str(site_uuid),
    }


async def recipients(session: AsyncSession, site_id: int, organisation_id: int) -> list[PushTarget]:
    """Load the enabled push targets of live sessions of the members who may see a site.

    Args:
        session: Database session without a scope.
        site_id: Database id of the site.
        organisation_id: Database id of its organisation.

    Returns:
        The targets.
    """
    covered = select(MembershipSite.id).where(
        MembershipSite.membership_id == Membership.id, MembershipSite.site_id == site_id
    )
    return list(
        await session.scalars(
            select(PushTarget)
            .join(AuthSession, AuthSession.id == PushTarget.session_id)
            .join(
                Membership,
                and_(Membership.user_id == PushTarget.user_id, Membership.organisation_id == organisation_id),
            )
            .where(
                PushTarget.enabled,
                AuthSession.revoked_at.is_(None),
                or_(Membership.all_sites, covered.exists()),
            )
        )
    )


class TelegramChannel:
    """Sends alerts to the organisation's linked Telegram chats.

    Attributes:
        name: Channel name, recorded with the usage it causes.
        bot: The platform's bot.
    """

    name = "telegram"

    def __init__(self, bot: TelegramBot = telegram_bot):
        """Bind the channel to a bot.

        Args:
            bot: The bot.
        """
        self.bot = bot

    async def send(self, event: dict, done: set) -> ChannelResult:
        """Send one alert to the chats that have not received it.

        Args:
            event: The event in its API form.
            done: Chat ids that already received it; updated in place.

        Returns:
            What the attempt achieved.
        """
        sent, pending = await self.bot.deliver(event, done)
        return ChannelResult(sent=sent, pending=pending)


class PushChannel:
    """Pushes alerts to the device sessions of the members who may see the event's site.

    Attributes:
        name: Channel name, recorded with the usage it causes.
        available: Builds the push providers.
    """

    name = "push"

    def __init__(self, available=providers):
        """Bind the channel to the providers.

        Args:
            available: Returns the providers by name.
        """
        self.available = available

    async def send(self, event: dict, done: set) -> ChannelResult:
        """Push one alert to the targets that have not received it.

        Args:
            event: The event in its API form.
            done: Ids of the targets that already received it; updated in place.

        Returns:
            What the attempt achieved.
        """
        available: dict = self.available()
        if not any(provider.enabled for provider in available.values()):
            return ChannelResult()
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    select(Camera.site_id, Camera.organisation_id, Site.uuid)
                    .join(Site, Site.id == Camera.site_id)
                    .where(Camera.uuid == UUID(str(event["camera_uuid"])))
                )
            ).first()
            if row is None:
                return ChannelResult()
            site_id, organisation_id, site_uuid = row
            targets = [
                target for target in await recipients(session, site_id, organisation_id) if target.id not in done
            ]
            if not targets:
                return ChannelResult()
            outcomes = await deliver_each(targets, alert_payload(event, site_uuid), available)
            await session.commit()
        for target, outcome in zip(targets, outcomes, strict=True):
            if outcome == SENT:
                done.add(target.id)
        return ChannelResult(
            sent=sum(1 for outcome in outcomes if outcome == SENT),
            pending=sum(1 for outcome in outcomes if outcome == RETRY),
        )
