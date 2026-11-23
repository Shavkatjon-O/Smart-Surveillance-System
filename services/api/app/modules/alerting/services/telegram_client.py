"""A small client of the Telegram Bot API: the methods the alert channel uses."""

import httpx

__all__ = ["TelegramClient", "TelegramError"]

TIMEOUT_SECONDS = 20.0


class TelegramError(Exception):
    """Telegram refused a call or could not be reached.

    Attributes:
        code: HTTP status or error code Telegram answered, 0 when it could not be reached.
        description: Telegram's description of the error.
    """

    def __init__(self, code: int, description: str):
        """Create the error.

        Args:
            code: Status or error code.
            description: Description of the error.
        """
        super().__init__(f"Telegram answered {code}: {description}")
        self.code = code
        self.description = description

    @property
    def transient(self) -> bool:
        """bool: Whether the call may succeed later: Telegram was unreachable, busy or failing."""
        return self.code == 0 or self.code == 429 or self.code >= 500

    @property
    def gone(self) -> bool:
        """bool: Whether the chat blocked the bot or no longer exists, so nothing more should be sent to it."""
        return self.code in (400, 403) and any(
            text in self.description.lower() for text in ("blocked", "kicked", "chat not found", "deactivated")
        )


class TelegramClient:
    """Calls one bot's methods.

    Attributes:
        base_url: Base address of the method calls, with the token.
        transport: HTTP transport, replaced in tests.
    """

    def __init__(self, token: str, api_url: str, transport: httpx.AsyncBaseTransport | None = None):
        """Bind the client to a bot.

        Args:
            token: The bot token.
            api_url: Base address of the Bot API.
            transport: HTTP transport, the default when omitted.
        """
        self.base_url = f"{api_url.rstrip('/')}/bot{token}"
        self.transport = transport

    async def call(self, method: str, data: dict | None = None, files: dict | None = None, timeout: float = 0):
        """Call one method.

        Args:
            method: Method name, such as sendMessage.
            data: Parameters.
            files: Files to upload, as multipart parts.
            timeout: Extra seconds to wait, for long polling.

        Returns:
            The result Telegram answered.

        Raises:
            TelegramError: Telegram refused the call or could not be reached.
        """
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS + timeout, transport=self.transport) as http:
                if files:
                    response = await http.post(f"{self.base_url}/{method}", data=data or {}, files=files)
                else:
                    response = await http.post(f"{self.base_url}/{method}", json=data or {})
        except httpx.HTTPError as exc:
            raise TelegramError(0, f"Telegram does not answer ({type(exc).__name__})")
        try:
            body = response.json()
        except ValueError:
            raise TelegramError(response.status_code, "Telegram answered without JSON")
        if not body.get("ok"):
            raise TelegramError(int(body.get("error_code") or response.status_code), str(body.get("description")))
        return body.get("result")

    async def get_me(self) -> dict:
        """Read the bot's own user.

        Returns:
            The bot user.
        """
        return await self.call("getMe")

    async def send_message(self, chat_id: int, text: str) -> dict:
        """Send a text message.

        Args:
            chat_id: Chat to send to.
            text: Plain text.

        Returns:
            The sent message.
        """
        return await self.call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    async def send_photo(self, chat_id: int, photo: bytes, caption: str) -> dict:
        """Send a picture with a caption.

        Args:
            chat_id: Chat to send to.
            photo: JPEG bytes.
            caption: Plain text caption.

        Returns:
            The sent message.
        """
        return await self.call(
            "sendPhoto",
            {"chat_id": str(chat_id), "caption": caption},
            files={"photo": ("snapshot.jpg", photo, "image/jpeg")},
        )

    async def set_webhook(self, url: str, secret: str) -> bool:
        """Have Telegram deliver updates to a webhook.

        Args:
            url: The webhook address.
            secret: Secret Telegram sends back in a header with every update.

        Returns:
            True when set.
        """
        return await self.call(
            "setWebhook",
            {"url": url, "secret_token": secret, "allowed_updates": ["message", "my_chat_member"]},
        )

    async def delete_webhook(self) -> bool:
        """Stop webhook delivery so updates can be polled.

        Returns:
            True when removed.
        """
        return await self.call("deleteWebhook")

    async def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        """Poll for updates.

        Args:
            offset: First update to return, which confirms the earlier ones.
            timeout: Seconds Telegram may hold the request open.

        Returns:
            The updates.
        """
        data = {"timeout": timeout, "allowed_updates": ["message", "my_chat_member"]}
        if offset is not None:
            data["offset"] = offset
        return await self.call("getUpdates", data, timeout=timeout)
