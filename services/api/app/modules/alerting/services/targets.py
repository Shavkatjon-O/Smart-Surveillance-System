"""Push targets of device sessions and delivery to them."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import UserContext
from app.core.exceptions import Conflict, NotFound, Unprocessable
from app.models import PushTarget
from app.modules.alerting.enums import PushProvider
from app.modules.alerting.schemas import PushProviders, PushTargetCreate, PushTargetList, PushTargetRead, PushTestResult
from app.modules.alerting.services.providers import Provider, providers

__all__ = ["DISABLE_AFTER_FAILURES", "FAILED", "RETRY", "SENT", "PushTargetService", "deliver", "deliver_each"]

DISABLE_AFTER_FAILURES = 10
DELIVERY_CONCURRENCY = 16


def target_read(target: PushTarget) -> PushTargetRead:
    """Build the API view of a push target.

    Args:
        target: The target.

    Returns:
        The target.
    """
    return PushTargetRead(
        uuid=target.uuid,
        provider=PushProvider(target.provider),
        token=target.token,
        enabled=target.enabled,
        last_success_at=target.last_success_at,
        last_error=target.last_error,
        created_at=target.created_at,
    )


SENT = "sent"
RETRY = "retry"
FAILED = "failed"


async def deliver_each(
    targets: list[PushTarget], data: dict[str, str], available: dict[PushProvider, Provider]
) -> list[str]:
    """Send a message to targets, record each outcome on its row and say which may succeed on a retry.

    A target the provider reports gone is disabled at once, and one that failed many times in a row is disabled
    too; the caller commits.

    Args:
        targets: The targets.
        data: Message data, ids only.
        available: Providers by name.

    Returns:
        sent, retry or failed for each target, in order: retry for a failure of a target that stays enabled.
    """
    semaphore = asyncio.Semaphore(DELIVERY_CONCURRENCY)

    async def one(target: PushTarget) -> str:
        """Send to one target once a slot is free.

        Args:
            target: The target.

        Returns:
            sent, retry or failed.
        """
        provider = available.get(PushProvider(target.provider))
        if provider is None or not provider.enabled:
            target.last_error = "The provider is not set up"
            return FAILED
        async with semaphore:
            outcome = await provider.send(target.token, data)
        if outcome.ok:
            target.failures = 0
            target.last_error = None
            target.last_success_at = datetime.now(UTC)
            return SENT
        target.failures += 1
        target.last_error = (outcome.error or "Delivery failed")[:200]
        if outcome.gone or target.failures >= DISABLE_AFTER_FAILURES:
            target.enabled = False
            return FAILED
        return RETRY

    return list(await asyncio.gather(*(one(target) for target in targets)))


async def deliver(targets: list[PushTarget], data: dict[str, str], available: dict[PushProvider, Provider]) -> int:
    """Send a message to targets and record each outcome on its row.

    Args:
        targets: The targets.
        data: Message data, ids only.
        available: Providers by name.

    Returns:
        The number of targets the providers accepted.
    """
    return sum(1 for outcome in await deliver_each(targets, data, available) if outcome == SENT)


class PushTargetService:
    """Registers and tests the push targets of the current device session.

    Attributes:
        session: Database session.
        actor: Signed-in user of a device session.
        available: Providers by name.
    """

    def __init__(
        self, session: AsyncSession, actor: UserContext, available: dict[PushProvider, Provider] | None = None
    ):
        """Bind the service to the current session.

        Args:
            session: Database session.
            actor: Signed-in user.
            available: Providers by name, the configured ones when omitted.
        """
        self.session = session
        self.actor = actor
        self.available = available if available is not None else providers()

    def device_session(self) -> int:
        """Return the device session of the request.

        Returns:
            Its database id.

        Raises:
            Conflict: The request was made with a web session, which has no push targets.
        """
        if self.actor.session_id is None:
            raise Conflict("not_a_device_session", "Push targets belong to an app session; sign in from the app")
        return self.actor.session_id

    def info(self) -> PushProviders:
        """Describe the providers that are set up.

        Returns:
            Which providers deliver.
        """
        ntfy = self.available[PushProvider.NTFY]
        return PushProviders(
            fcm=self.available[PushProvider.FCM].enabled,
            ntfy=ntfy.enabled,
            ntfy_url=getattr(ntfy, "base_url", None) or None,
        )

    async def targets(self) -> list[PushTarget]:
        """Load the targets of the current session.

        Returns:
            The targets, oldest first.
        """
        return list(
            await self.session.scalars(
                select(PushTarget)
                .where(PushTarget.session_id == self.device_session())
                .order_by(PushTarget.created_at, PushTarget.id)
            )
        )

    async def list(self) -> PushTargetList:
        """List the targets of the current session.

        Returns:
            The targets.
        """
        return PushTargetList(items=[target_read(target) for target in await self.targets()])

    async def register(self, payload: PushTargetCreate) -> PushTargetRead:
        """Register a target for the current session, or turn an existing one back on.

        Args:
            payload: Provider and token.

        Returns:
            The target.

        Raises:
            Unprocessable: The provider is not set up or refuses the token.
        """
        session_id = self.device_session()
        provider = self.available[payload.provider]
        if not provider.enabled:
            raise Unprocessable("provider_disabled", f"{payload.provider.value} is not set up on this server")
        if not provider.accepts(payload.token):
            raise Unprocessable("invalid_push_token", "The token or topic address is not valid for this provider")
        target = await self.session.scalar(
            select(PushTarget).where(
                PushTarget.session_id == session_id,
                PushTarget.provider == payload.provider.value,
                PushTarget.token == payload.token,
            )
        )
        if target is None:
            target = PushTarget(
                session_id=session_id, user_id=self.actor.id, provider=payload.provider.value, token=payload.token
            )
            self.session.add(target)
        target.enabled = True
        target.failures = 0
        await self.session.commit()
        await self.session.refresh(target)
        return target_read(target)

    async def delete(self, uuid: UUID) -> None:
        """Remove a target of the current session.

        Args:
            uuid: Public id of the target.

        Raises:
            NotFound: The current session has no such target.
        """
        target = await self.session.scalar(
            select(PushTarget).where(PushTarget.uuid == uuid, PushTarget.session_id == self.device_session())
        )
        if target is None:
            raise NotFound("push_target_not_found", f"Push target {uuid} does not exist")
        await self.session.delete(target)
        await self.session.commit()

    async def test(self) -> PushTestResult:
        """Send a test message to every enabled target of the current session.

        Returns:
            How many were accepted and how many failed.
        """
        targets = [target for target in await self.targets() if target.enabled]
        sent = await deliver(targets, {"type": "test"}, self.available)
        await self.session.commit()
        return PushTestResult(sent=sent, failed=len(targets) - sent)
