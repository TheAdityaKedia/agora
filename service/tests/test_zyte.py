import base64

import pytest
import requests

from scrapers import zyte
from scrapers.browser import RateLimited


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Zyte retries back off with sleeps; skip the wall-clock wait in tests."""
    monkeypatch.setattr(zyte.time, "sleep", lambda s: None)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(zyte.ENV_KEY, "test-key")


def test_is_configured_follows_env(monkeypatch):
    monkeypatch.delenv(zyte.ENV_KEY, raising=False)
    assert not zyte.is_configured()
    monkeypatch.setenv(zyte.ENV_KEY, "abc")
    assert zyte.is_configured()


def test_empty_env_key_counts_as_unconfigured(monkeypatch):
    monkeypatch.setenv(zyte.ENV_KEY, "")
    assert not zyte.is_configured()


def test_fetch_without_key_raises(monkeypatch):
    monkeypatch.delenv(zyte.ENV_KEY, raising=False)
    with pytest.raises(zyte.ZyteNotConfigured):
        zyte.fetch_html("https://example.com")


def test_render_requests_browser_html(key, monkeypatch):
    sent = {}

    def fake_post(url, auth=None, json=None, timeout=None):
        sent.update(url=url, auth=auth, body=json)
        return FakeResponse(200, {"browserHtml": "<html>ok</html>"})

    monkeypatch.setattr(zyte.requests, "post", fake_post)
    assert zyte.fetch_html("https://example.com/events") == "<html>ok</html>"
    assert sent["url"] == zyte.API_URL
    assert sent["auth"] == ("test-key", "")
    assert sent["body"] == {"url": "https://example.com/events", "browserHtml": True}


def test_no_render_requests_raw_body_and_decodes_base64(key, monkeypatch):
    encoded = base64.b64encode("<html>raw</html>".encode()).decode()

    def fake_post(url, auth=None, json=None, timeout=None):
        assert json == {"url": "https://example.com", "httpResponseBody": True}
        return FakeResponse(200, {"httpResponseBody": encoded})

    monkeypatch.setattr(zyte.requests, "post", fake_post)
    assert zyte.fetch_html("https://example.com", render=False) == "<html>raw</html>"


def test_geolocation_passed_through(key, monkeypatch):
    sent = {}

    def fake_post(url, auth=None, json=None, timeout=None):
        sent.update(json)
        return FakeResponse(200, {"browserHtml": "x"})

    monkeypatch.setattr(zyte.requests, "post", fake_post)
    zyte.fetch_html("https://example.com", geolocation="US")
    assert sent["geolocation"] == "US"


def test_ban_is_retried_then_raises_rate_limited(key, monkeypatch):
    calls = []

    def fake_post(url, auth=None, json=None, timeout=None):
        calls.append(1)
        return FakeResponse(520, text="Website Ban")

    monkeypatch.setattr(zyte.requests, "post", fake_post)
    with pytest.raises(RateLimited) as exc:
        zyte.fetch_html("https://example.com", attempts=3)
    assert len(calls) == 3
    assert exc.value.status == 520


def test_ban_then_success_returns_html(key, monkeypatch):
    responses = [
        FakeResponse(520, text="Website Ban"),
        FakeResponse(200, {"browserHtml": "<html>late</html>"}),
    ]

    def fake_post(url, auth=None, json=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(zyte.requests, "post", fake_post)
    assert zyte.fetch_html("https://example.com") == "<html>late</html>"


def test_non_retryable_status_raises_immediately(key, monkeypatch):
    calls = []

    def fake_post(url, auth=None, json=None, timeout=None):
        calls.append(1)
        return FakeResponse(401, text="Unauthorized")

    monkeypatch.setattr(zyte.requests, "post", fake_post)
    with pytest.raises(RateLimited) as exc:
        zyte.fetch_html("https://example.com")
    assert len(calls) == 1  # auth errors don't get retried
    assert exc.value.status == 401


def test_network_error_retries_then_raises(key, monkeypatch):
    calls = []

    def fake_post(url, auth=None, json=None, timeout=None):
        calls.append(1)
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(zyte.requests, "post", fake_post)
    with pytest.raises(RateLimited):
        zyte.fetch_html("https://example.com", attempts=2)
    assert len(calls) == 2


def test_missing_body_returns_empty_string(key, monkeypatch):
    monkeypatch.setattr(zyte.requests, "post",
                        lambda *a, **k: FakeResponse(200, {"statusCode": 200}))
    assert zyte.fetch_html("https://example.com") == ""
