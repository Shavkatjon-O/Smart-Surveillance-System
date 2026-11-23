import json
from datetime import UTC, datetime

import httpx
import pytest

from app.modules.alerting.services import telegram as module
from app.modules.alerting.services.telegram import TelegramBot, alert_caption, parse_start, token_hash
from app.modules.alerting.services.telegram_client import TelegramClient, TelegramError

TOKEN = "Ab3_dE5-fG7hJ9kL1mN2pQ4r"


def fake_api(answers, seen):
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        seen.append((request.url.path, method, request.headers.get("content-type", "")))
        status, body = answers.get(method, (200, {"ok": True, "result": True}))
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_start_commands_carry_the_link_token_in_private_chats_and_groups():
    assert parse_start(f"/start {TOKEN}") == TOKEN
    assert parse_start(f"/start@sss_devkit_bot {TOKEN}") == TOKEN
    assert parse_start("/start") is None and parse_start("hello") is None and parse_start(None) is None
    assert parse_start(f"/start {TOKEN}; drop table") is None
    assert len(token_hash(TOKEN)) == 64 and token_hash(TOKEN) != TOKEN


def test_an_alert_names_what_where_and_when_with_a_link():
    text = alert_caption(
        "line_crossing",
        None,
        "Gate",
        "Home",
        datetime(2026, 11, 20, 9, 5, tzinfo=UTC),
        "Asia/Tashkent",
        "https://x/e/1",
    )
    assert text.splitlines() == ["Line crossing on Gate, Home", "2026-11-20 14:05:00", "https://x/e/1"]
    assert alert_caption("person", "person", "Yard", "Store", datetime(2026, 1, 1, tzinfo=UTC), "Bad/Zone", "l")
    assert text.isascii()


async def test_the_client_uses_the_token_path_and_multipart_for_photos():
    seen = []
    client = TelegramClient("123:abc", "http://telegram:8081", transport=fake_api({}, seen))
    await client.send_photo(-100, b"\xff\xd8jpeg", "Person on Gate")
    await client.send_message(5, "Test")
    assert seen[0][0] == "/bot123:abc/sendPhoto" and seen[0][2].startswith("multipart/form-data")
    assert seen[1][1] == "sendMessage" and seen[1][2] == "application/json"


async def test_refusals_say_whether_the_chat_is_gone():
    answers = {
        "sendMessage": (403, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}),
        "getUpdates": (409, {"ok": False, "error_code": 409, "description": "Conflict: webhook is active"}),
    }
    client = TelegramClient("t", "http://telegram:8081", transport=fake_api(answers, []))
    with pytest.raises(TelegramError) as blocked:
        await client.send_message(5, "x")
    assert blocked.value.gone
    with pytest.raises(TelegramError) as conflict:
        await client.get_updates(None, 1)
    assert conflict.value.code == 409 and not conflict.value.gone


def test_the_webhook_needs_the_configured_secret(monkeypatch):
    monkeypatch.setattr(module.settings, "telegram_webhook_secret", "s3cret-value")
    bot = TelegramBot()
    assert bot.authentic("s3cret-value") and not bot.authentic("wrong") and not bot.authentic(None)
    monkeypatch.setattr(module.settings, "telegram_webhook_secret", "")
    assert not bot.authentic("")


async def test_updates_are_routed_to_linking_stopping_and_removal(monkeypatch):
    bot = TelegramBot()
    calls = []

    async def link(token, chat):
        calls.append(("link", token, chat["id"]))
        return "linked"

    async def gone(chat_id, stopped=False):
        calls.append(("gone", chat_id, stopped))

    async def reply(chat_id, text):
        calls.append(("reply", chat_id))

    monkeypatch.setattr(bot, "link", link)
    monkeypatch.setattr(bot, "mark_gone", gone)
    monkeypatch.setattr(bot, "reply", reply)
    group = {"id": -42, "type": "group", "title": "Shop"}
    assert await bot.handle({"message": {"chat": group, "text": f"/start@bot {TOKEN}"}}) == "linked"
    assert await bot.handle({"message": {"chat": {"id": 7, "type": "private"}, "text": "/stop"}}) == "stopped"
    left = {"my_chat_member": {"chat": group, "new_chat_member": {"status": "kicked"}}}
    assert await bot.handle(left) == "removed"
    assert await bot.handle({"message": {"chat": {"id": 7}, "text": "hi"}}) == "ignored"
    assert await bot.handle({"edited_message": {}}) == "ignored"
    assert calls == [("link", TOKEN, -42), ("gone", 7, True), ("reply", 7), ("gone", -42, False)]
    assert json.dumps(calls)


def test_only_unreachable_busy_or_failing_telegram_answers_are_worth_a_retry():
    assert TelegramError(0, "Telegram does not answer").transient
    assert TelegramError(429, "Too Many Requests").transient
    assert TelegramError(502, "Bad Gateway").transient
    assert not TelegramError(400, "Bad Request: chat not found").transient
    assert not TelegramError(403, "Forbidden: bot was blocked by the user").transient
