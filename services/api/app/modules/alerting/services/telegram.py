"""Telegram alerts: linking chats through single-use bot deep links, receiving updates and sending alerts."""

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.exceptions import NotFound, Unavailable
from app.models import Camera, Event, Organisation, Site, TelegramChat, TelegramLink
from app.modules.alerting.schemas.telegram import TelegramChatList, TelegramChatRead, TelegramLinkRead
from app.modules.alerting.services.telegram_client import TelegramClient, TelegramError
from app.modules.billing.enums import Feature, UsageMetric
from app.modules.billing.services.entitlements import entitlements_for
from app.modules.billing.services.metering import record_usage
from app.modules.operations.enums import AuditAction
from app.modules.operations.services.audit import record_audit
from app.modules.tenancy.services.access import SiteAccess
from app.modules.tenancy.services.scope import UNSCOPED_OPTION

__all__ = ["TelegramBot", "TelegramService", "alert_caption", "parse_start", "telegram_bot", "token_hash"]

logger = logging.getLogger(__name__)

UNSCOPED = {UNSCOPED_OPTION: True}
POLL_SECONDS = 25
RETRY_SECONDS = 10
SNAPSHOT_WAIT_SECONDS = 5
START = re.compile(r"^/start(?:@\w+)?\s+([A-Za-z0-9_-]{16,64})\s*$")
STOP = re.compile(r"^/stop(?:@\w+)?\s*$")
GONE_STATUSES = {"kicked", "left"}


def token_hash(token: str) -> str:
    """Hash a link token for storage.

    Args:
        token: The token.

    Returns:
        Its SHA-256 in hex.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def parse_start(text: str | None) -> str | None:
    """Read the token of a /start command, as private chats and groups send it.

    Args:
        text: Message text.

    Returns:
        The token, or None for any other text.
    """
    found = START.match(text or "")
    return found.group(1) if found else None


def chat_title(chat: dict) -> str | None:
    """Name a chat for the member's list.

    Args:
        chat: Telegram chat object.

    Returns:
        The group title or the person's name.
    """
    if chat.get("title"):
        return str(chat["title"])[:255]
    name = " ".join(part for part in (chat.get("first_name"), chat.get("last_name")) if part)
    return name[:255] or chat.get("username")


def alert_caption(kind: str, label: str | None, camera: str, site: str, at: datetime, zone: str, link: str) -> str:
    """Write the text of an alert.

    Args:
        kind: Event kind.
        label: What was detected, if known.
        camera: Camera name.
        site: Site name.
        at: When it started.
        zone: Time zone of the site.
        link: Address of the event in the app.

    Returns:
        Plain text.
    """
    try:
        local = at.astimezone(ZoneInfo(zone))
    except Exception:
        local = at
    what = (label or kind).replace("_", " ")
    return f"{what.capitalize()} on {camera}, {site}\n{local:%Y-%m-%d %H:%M:%S}\n{link}"


class TelegramBot:
    """The platform's Telegram bot: receives updates by webhook or polling and sends alerts.

    Attributes:
        client: Bot API client, None while Telegram alerts are off.
        username: The bot's username, for the deep links.
        task: The polling loop, if polling.
    """

    def __init__(self):
        """Create the bot from the settings, not yet started."""
        self.client: TelegramClient | None = None
        self.username = settings.telegram_bot_username
        self.task: asyncio.Task | None = None
        if settings.telegram_bot_token:
            self.client = TelegramClient(settings.telegram_bot_token, settings.telegram_api_url)

    @property
    def enabled(self) -> bool:
        """bool: Whether a bot token is configured."""
        return self.client is not None

    async def start(self) -> None:
        """Read the bot's name and start receiving updates, by polling only when the setting asks for it."""
        if self.client is None:
            return
        try:
            if not self.username:
                self.username = str((await self.client.get_me()).get("username") or "")
            if settings.telegram_polling:
                await self.client.delete_webhook()
                self.task = asyncio.create_task(self.poll(), name="telegram-poll")
            elif settings.telegram_webhook_secret:
                url = settings.telegram_webhook_url or f"{settings.public_url.rstrip('/')}/api/v1/telegram/webhook"
                await self.client.set_webhook(url, settings.telegram_webhook_secret)
            else:
                logger.warning("SSS_TELEGRAM_WEBHOOK_SECRET is empty, so Telegram updates are not received")
        except TelegramError as exc:
            logger.warning("Starting the Telegram bot failed: %s", exc)

    async def stop(self) -> None:
        """Stop polling."""
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def poll(self) -> None:
        """Poll for updates until cancelled, surviving failures."""
        offset = None
        while True:
            try:
                updates = await self.client.get_updates(offset, POLL_SECONDS)
            except TelegramError as exc:
                logger.warning("Polling Telegram failed: %s", exc)
                await asyncio.sleep(RETRY_SECONDS)
                continue
            for item in updates:
                offset = int(item["update_id"]) + 1
                try:
                    await self.handle(item)
                except Exception:
                    logger.exception("Handling Telegram update %s failed", item.get("update_id"))

    def authentic(self, secret: str | None) -> bool:
        """Check the secret a webhook call carries.

        Args:
            secret: Value of the X-Telegram-Bot-Api-Secret-Token header.

        Returns:
            True when it matches the configured secret.
        """
        expected = settings.telegram_webhook_secret
        return bool(expected) and hmac.compare_digest((secret or "").encode(), expected.encode())

    async def handle(self, item: dict) -> str:
        """Act on one update: link a chat, stop alerts, or note that the bot was removed.

        Args:
            item: The update.

        Returns:
            What happened: linked, unknown_link, stopped, removed or ignored.
        """
        member = item.get("my_chat_member")
        if member:
            status = ((member.get("new_chat_member") or {}).get("status")) or ""
            if status in GONE_STATUSES:
                await self.mark_gone(int(member["chat"]["id"]))
                return "removed"
            return "ignored"
        message = item.get("message") or {}
        chat = message.get("chat") or {}
        if "id" not in chat:
            return "ignored"
        text = message.get("text")
        token = parse_start(text)
        if token is not None:
            return await self.link(token, chat)
        if STOP.match(text or ""):
            await self.mark_gone(int(chat["id"]), stopped=True)
            await self.reply(int(chat["id"]), "Alerts stopped. Link this chat again from the app to resume them.")
            return "stopped"
        return "ignored"

    async def link(self, token: str, chat: dict) -> str:
        """Connect a chat to the organisation of a link.

        Args:
            token: The link token.
            chat: Telegram chat object.

        Returns:
            linked or unknown_link.
        """
        chat_id = int(chat["id"])
        async with async_session_maker() as session:
            link = await session.scalar(
                select(TelegramLink)
                .where(
                    TelegramLink.token_hash == token_hash(token),
                    TelegramLink.used_at.is_(None),
                    TelegramLink.expires_at > datetime.now(UTC),
                )
                .with_for_update()
            )
            if link is None:
                await session.rollback()
                await self.reply(chat_id, "This link is used or expired. Create a new one in the app.")
                return "unknown_link"
            link.used_at = datetime.now(UTC)
            row = await session.scalar(
                select(TelegramChat).where(
                    TelegramChat.organisation_id == link.organisation_id, TelegramChat.chat_id == chat_id
                )
            )
            if row is None:
                row = TelegramChat(organisation_id=link.organisation_id, chat_id=chat_id)
                session.add(row)
            row.chat_type = str(chat.get("type") or "private")
            row.title = chat_title(chat)
            row.linked_by_id = link.created_by_id
            row.enabled = True
            row.blocked_at = None
            row.deleted_at = None
            await session.flush()
            await record_audit(
                session,
                AuditAction.ALERT_CHANNEL_LINK,
                actor_id=link.created_by_id,
                organisation_id=link.organisation_id,
                target_kind="telegram_chat",
                target_uuid=row.uuid,
                details={"chat_type": row.chat_type},
            )
            name = await session.scalar(select(Organisation.name).where(Organisation.id == link.organisation_id))
            await session.commit()
        await self.reply(chat_id, f"This chat now receives the alerts of {name}. Send /stop to end them.")
        return "linked"

    async def mark_gone(self, chat_id: int, stopped: bool = False) -> None:
        """Stop sending to a chat in every organisation.

        Args:
            chat_id: Telegram chat id.
            stopped: Whether the chat asked to stop rather than removed the bot.
        """
        values = {"enabled": False} if stopped else {"blocked_at": datetime.now(UTC)}
        async with async_session_maker() as session:
            await session.execute(update(TelegramChat).where(TelegramChat.chat_id == chat_id).values(**values))
            await session.commit()

    async def reply(self, chat_id: int, text: str) -> None:
        """Answer a chat, logging a failure.

        Args:
            chat_id: Telegram chat id.
            text: Plain text.
        """
        if self.client is None:
            return
        try:
            await self.client.send_message(chat_id, text)
        except TelegramError as exc:
            logger.warning("Answering Telegram chat failed: %s", exc)

    async def snapshot(self, session: AsyncSession, event_uuid: UUID) -> bytes | None:
        """Read an event's snapshot, waiting briefly for it to be written.

        Args:
            session: Database session without a scope.
            event_uuid: The event.

        Returns:
            The JPEG, or None when the event has none.
        """
        for _ in range(SNAPSHOT_WAIT_SECONDS):
            path = await session.scalar(select(Event.snapshot_path).where(Event.uuid == event_uuid))
            if path:
                try:
                    return (Path(settings.events_dir) / path).read_bytes()
                except OSError:
                    return None
            await asyncio.sleep(1)
        return None

    async def deliver(self, event: dict, done: set) -> tuple[int, int]:
        """Send an alert to the enabled chats of the event's organisation, when its plan includes Telegram alerts.

        Chats already in done are skipped, and every chat that received the alert joins it, so a retry reaches only
        the chats that missed it.

        Args:
            event: The event in its API form.
            done: Telegram chat ids that already received this alert; updated in place.

        Returns:
            The chats that received it now and the chats that failed in a way a retry may cure.
        """
        if self.client is None:
            return 0, 0
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    select(Camera.name, Camera.site_id, Camera.organisation_id, Site.name, Site.timezone)
                    .join(Site, Site.id == Camera.site_id)
                    .where(Camera.uuid == UUID(str(event["camera_uuid"])))
                    .execution_options(**UNSCOPED)
                )
            ).first()
            if row is None:
                return 0, 0
            camera, site_id, organisation_id, site, zone = row
            granted = await entitlements_for(session, organisation_id)
            if not granted.allows(Feature.TELEGRAM_ALERTS) or granted.suspended:
                return 0, 0
            chats = list(
                await session.scalars(
                    select(TelegramChat).where(
                        TelegramChat.organisation_id == organisation_id,
                        TelegramChat.enabled.is_(True),
                        TelegramChat.blocked_at.is_(None),
                        TelegramChat.deleted_at.is_(None),
                    )
                )
            )
            chats = [chat for chat in chats if chat.chat_id not in done]
            if not chats:
                return 0, 0
            started = event.get("started_at")
            at = datetime.fromisoformat(str(started)) if started else datetime.now(UTC)
            caption = alert_caption(
                str(event.get("kind") or "event"),
                event.get("label"),
                camera,
                site,
                at,
                zone,
                f"{settings.public_url.rstrip('/')}/events/{event['uuid']}",
            )
            picture = await self.snapshot(session, UUID(str(event["uuid"])))
            sent = 0
            pending = 0
            for chat in chats:
                try:
                    if picture:
                        await self.client.send_photo(chat.chat_id, picture, caption)
                    else:
                        await self.client.send_message(chat.chat_id, caption)
                    sent += 1
                    done.add(chat.chat_id)
                except TelegramError as exc:
                    logger.warning("Sending an alert to a Telegram chat failed: %s", exc)
                    if exc.gone:
                        chat.blocked_at = datetime.now(UTC)
                    elif exc.transient:
                        pending += 1
            await session.commit()
            return sent, pending


telegram_bot = TelegramBot()


class TelegramService:
    """The Telegram chats of the organisation of the request's site.

    Attributes:
        session: Database session limited to the request's site and organisation.
        access: Access of the signed-in member.
        bot: The platform's bot.
    """

    def __init__(self, session: AsyncSession, access: SiteAccess, bot: TelegramBot | None = None):
        """Bind the service to a request.

        Args:
            session: Database session limited to the request's site and organisation.
            access: Access of the signed-in member.
            bot: The bot, the platform's when omitted.
        """
        self.session = session
        self.access = access
        self.bot = bot or telegram_bot

    def require_bot(self) -> TelegramClient:
        """Return the bot client.

        Returns:
            The client.

        Raises:
            Unavailable: Telegram alerts are not configured.
        """
        if self.bot.client is None or not self.bot.username:
            raise Unavailable("telegram_off", "Telegram alerts are not set up on this server")
        return self.bot.client

    async def create_link(self) -> TelegramLinkRead:
        """Create the single-use deep links that connect a private chat or a group.

        Returns:
            The links and when they expire.
        """
        self.require_bot()
        token = secrets.token_urlsafe(24)
        expires = datetime.now(UTC) + timedelta(minutes=settings.telegram_link_minutes)
        self.session.add(
            TelegramLink(
                organisation_id=self.access.organisation_id,
                token_hash=token_hash(token),
                created_by_id=self.access.user.id,
                expires_at=expires,
            )
        )
        await self.session.commit()
        base = f"https://t.me/{self.bot.username}"
        return TelegramLinkRead(
            url=f"{base}?start={token}",
            group_url=f"{base}?startgroup={token}",
            bot=self.bot.username,
            expires_at=expires,
        )

    async def chats(self) -> TelegramChatList:
        """List the organisation's chats.

        Returns:
            The chats, newest first.
        """
        rows = await self.session.scalars(
            select(TelegramChat).where(TelegramChat.deleted_at.is_(None)).order_by(TelegramChat.created_at.desc())
        )
        return TelegramChatList(
            items=[
                TelegramChatRead(
                    uuid=row.uuid,
                    chat_type=row.chat_type,
                    title=row.title,
                    enabled=row.enabled,
                    blocked=row.blocked_at is not None,
                    created_at=row.created_at,
                )
                for row in rows
            ],
            available=self.bot.enabled,
        )

    async def chat(self, uuid: UUID) -> TelegramChat:
        """Load a chat of the organisation.

        Args:
            uuid: Public id.

        Returns:
            The chat.

        Raises:
            NotFound: The organisation has no such chat.
        """
        row = await self.session.scalar(
            select(TelegramChat).where(TelegramChat.uuid == uuid, TelegramChat.deleted_at.is_(None))
        )
        if row is None:
            raise NotFound("telegram_chat_not_found", f"Telegram chat {uuid} does not exist")
        return row

    async def remove(self, uuid: UUID) -> None:
        """Stop sending alerts to a chat and forget it.

        Args:
            uuid: Public id of the chat.
        """
        row = await self.chat(uuid)
        row.enabled = False
        row.deleted_at = datetime.now(UTC)
        await self.session.commit()
        if self.bot.client is not None:
            await self.bot.reply(row.chat_id, "This chat no longer receives alerts.")

    async def test(self, uuid: UUID) -> None:
        """Send a test message to a chat.

        Args:
            uuid: Public id of the chat.

        Raises:
            Unavailable: Telegram refused the message.
        """
        client = self.require_bot()
        row = await self.chat(uuid)
        try:
            await client.send_message(row.chat_id, f"Test alert from {self.access.site_name}.")
        except TelegramError as exc:
            if exc.gone:
                row.blocked_at = datetime.now(UTC)
                await self.session.commit()
            raise Unavailable("telegram_refused", exc.description)
        await record_usage(self.session, self.access.organisation_id, self.access.site_id, UsageMetric.NOTIFICATIONS, 1)
        await self.session.commit()
