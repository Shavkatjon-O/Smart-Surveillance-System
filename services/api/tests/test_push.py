import json
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models import PushTarget
from app.modules.alerting.enums import PushProvider
from app.modules.alerting.services.dispatcher import alert_payload
from app.modules.alerting.services.providers import Delivery, FcmProvider, NtfyProvider, Provider
from app.modules.alerting.services.targets import DISABLE_AFTER_FAILURES, deliver

BASE = "https://ntfy.example.org"
TOPIC = f"{BASE}/abcdefghijklmnop"


@pytest.mark.parametrize(
    "token,expected",
    [
        (TOPIC, True),
        (f"{BASE}/abcdefghijklmno", False),
        (f"{BASE}/", False),
        (f"{BASE}/abcdefghijklmnop/extra", False),
        ("https://evil.example.org/abcdefghijklmnop", False),
        (f"{BASE}.evil.org/abcdefghijklmnop", False),
        ("abcdefghijklmnop", False),
    ],
)
def test_ntfy_accepts_only_long_topics_under_its_base(token, expected):
    assert NtfyProvider(BASE).accepts(token) is expected


def test_ntfy_without_a_server_is_disabled_and_accepts_nothing():
    provider = NtfyProvider("")
    assert not provider.enabled
    assert not provider.accepts(TOPIC)


async def test_ntfy_posts_the_ids_as_json_to_the_topic():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    provider = NtfyProvider(BASE, "secret", httpx.MockTransport(handler))
    outcome = await provider.send(TOPIC, {"type": "alert", "event": "e1"})
    assert outcome == Delivery(ok=True)
    request = seen[0]
    assert str(request.url) == TOPIC
    assert json.loads(request.content) == {"type": "alert", "event": "e1"}
    assert request.headers["Authorization"] == "Bearer secret"


async def test_ntfy_not_found_is_gone():
    provider = NtfyProvider(BASE, transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    outcome = await provider.send(TOPIC, {})
    assert not outcome.ok
    assert outcome.gone


async def test_ntfy_server_error_is_not_gone():
    provider = NtfyProvider(BASE, transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    outcome = await provider.send(TOPIC, {})
    assert not outcome.ok
    assert not outcome.gone


@pytest.fixture
def key_file(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    path = tmp_path / "account.json"
    path.write_text(
        json.dumps(
            {"client_email": "push@example.iam", "private_key": pem, "token_uri": "https://oauth.example.org/token"}
        )
    )
    return str(path)


def fcm_transport(send_status=200, send_text="{}", requests=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.host == "oauth.example.org":
            return httpx.Response(200, json={"access_token": "oauth-token", "expires_in": 3600})
        return httpx.Response(send_status, text=send_text)

    return httpx.MockTransport(handler)


def test_fcm_is_enabled_only_with_a_project_and_a_readable_key(key_file):
    assert FcmProvider("proj", key_file).enabled
    assert not FcmProvider("", key_file).enabled
    assert not FcmProvider("proj", "/no/such/file.json").enabled


async def test_fcm_exchanges_the_assertion_and_sends_a_data_message(key_file):
    requests = []
    provider = FcmProvider("proj", key_file, fcm_transport(requests=requests))
    outcome = await provider.send("device-token", {"type": "alert"})
    assert outcome == Delivery(ok=True)
    token_request, send_request = requests
    assert b"grant_type=urn" in token_request.content
    assert str(send_request.url) == "https://fcm.googleapis.com/v1/projects/proj/messages:send"
    assert send_request.headers["Authorization"] == "Bearer oauth-token"
    body = json.loads(send_request.content)["message"]
    assert body["token"] == "device-token"
    assert body["data"] == {"type": "alert"}


async def test_fcm_reuses_the_cached_oauth_token(key_file):
    requests = []
    provider = FcmProvider("proj", key_file, fcm_transport(requests=requests))
    await provider.send("a", {})
    await provider.send("b", {})
    assert [request.url.host for request in requests].count("oauth.example.org") == 1


async def test_fcm_unregistered_answer_is_gone(key_file):
    provider = FcmProvider("proj", key_file, fcm_transport(400, '{"error": {"status": "UNREGISTERED"}}'))
    outcome = await provider.send("device-token", {})
    assert not outcome.ok
    assert outcome.gone


async def test_fcm_not_found_status_is_gone(key_file):
    outcome = await FcmProvider("proj", key_file, fcm_transport(404)).send("device-token", {})
    assert outcome.gone


async def test_fcm_other_failure_is_not_gone(key_file):
    outcome = await FcmProvider("proj", key_file, fcm_transport(500)).send("device-token", {})
    assert not outcome.ok
    assert not outcome.gone


class FakeProvider(Provider):
    name = PushProvider.NTFY

    def __init__(self, outcome):
        super().__init__()
        self.outcome = outcome

    @property
    def enabled(self):
        return True

    async def send(self, token, data):
        return self.outcome


def target(**fields):
    base = {"provider": "ntfy", "token": TOPIC, "enabled": True, "failures": 0}
    base.update(fields)
    return PushTarget(**base)


async def test_deliver_records_success():
    item = target(failures=3, last_error="old")
    sent = await deliver([item], {}, {PushProvider.NTFY: FakeProvider(Delivery(ok=True))})
    assert sent == 1
    assert item.failures == 0
    assert item.last_error is None
    assert isinstance(item.last_success_at, datetime)


async def test_deliver_disables_a_gone_target():
    item = target()
    sent = await deliver([item], {}, {PushProvider.NTFY: FakeProvider(Delivery(ok=False, gone=True, error="gone"))})
    assert sent == 0
    assert item.failures == 1
    assert item.last_error == "gone"
    assert item.enabled is False
    assert item.last_success_at is None


async def test_deliver_keeps_a_target_after_one_plain_failure():
    item = target()
    await deliver([item], {}, {PushProvider.NTFY: FakeProvider(Delivery(ok=False, error="boom"))})
    assert item.enabled is True
    assert item.failures == 1


async def test_deliver_disables_a_target_after_repeated_failures():
    item = target(failures=DISABLE_AFTER_FAILURES - 1)
    await deliver([item], {}, {PushProvider.NTFY: FakeProvider(Delivery(ok=False, error="boom"))})
    assert item.enabled is False


async def test_deliver_notes_a_provider_that_is_not_set_up():
    item = target()
    assert await deliver([item], {}, {}) == 0
    assert item.last_error == "The provider is not set up"


def test_alert_payload_carries_only_ids():
    event = {"uuid": uuid4(), "camera_uuid": uuid4(), "label": "person", "snapshot": "x.jpg"}
    site = uuid4()
    assert alert_payload(event, site) == {
        "type": "alert",
        "event": str(event["uuid"]),
        "camera": str(event["camera_uuid"]),
        "site": str(site),
    }


async def test_deliver_each_says_which_targets_a_retry_may_reach():
    from app.modules.alerting.services.targets import FAILED, RETRY, SENT, deliver_each

    gone, plain, fine = target(), target(), target()
    providers = {PushProvider.NTFY: FakeProvider(Delivery(ok=False, gone=True, error="gone"))}
    assert await deliver_each([gone], {}, providers) == [FAILED]
    providers = {PushProvider.NTFY: FakeProvider(Delivery(ok=False, error="busy"))}
    assert await deliver_each([plain], {}, providers) == [RETRY]
    providers = {PushProvider.NTFY: FakeProvider(Delivery(ok=True))}
    assert await deliver_each([fine], {}, providers) == [SENT]
    assert await deliver_each([target()], {}, {}) == [FAILED]
