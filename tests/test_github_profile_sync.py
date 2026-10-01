"""Unit tests for committing a learned bank profile back to GitHub. Never
makes a real network call -- every requests.get/requests.put is mocked."""
import pytest

from core.bank_profiles import BankProfile
from core.github_profile_sync import commit_profile_to_github


def _profile():
    return BankProfile(
        bank_slug="wells_fargo",
        display_name="Wells Fargo",
        deposit_headers=["deposits and other credits"],
        withdrawal_headers=["withdrawals and other debits"],
    )


def test_returns_false_without_a_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert commit_profile_to_github(_profile()) is False


def test_creates_a_new_file_when_none_exists(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    calls = {"get": None, "put": None}

    class FakeResponse:
        def __init__(self, status_code, json_body=None):
            self.status_code = status_code
            self._json = json_body or {}

        def json(self):
            return self._json

    def fake_get(url, headers=None, params=None, timeout=None):
        calls["get"] = {"url": url, "headers": headers, "params": params}
        return FakeResponse(404)  # file does not exist yet

    def fake_put(url, headers=None, json=None, timeout=None):
        calls["put"] = {"url": url, "headers": headers, "json": json}
        return FakeResponse(201)

    import core.github_profile_sync as sync_module
    monkeypatch.setattr(sync_module.requests, "get", fake_get)
    monkeypatch.setattr(sync_module.requests, "put", fake_put)

    result = commit_profile_to_github(_profile())

    assert result is True
    assert calls["put"]["url"].endswith("/contents/bank_profiles/wells_fargo.json")
    assert "sha" not in calls["put"]["json"]  # a create, not an update
    assert calls["put"]["headers"]["Authorization"] == "Bearer fake-token"
    assert "wells_fargo" in calls["put"]["json"]["message"]


def test_updates_an_existing_file_with_its_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    class FakeResponse:
        def __init__(self, status_code, json_body=None):
            self.status_code = status_code
            self._json = json_body or {}

        def json(self):
            return self._json

    put_calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        return FakeResponse(200, {"sha": "abc123"})

    def fake_put(url, headers=None, json=None, timeout=None):
        put_calls.append(json)
        return FakeResponse(200)

    import core.github_profile_sync as sync_module
    monkeypatch.setattr(sync_module.requests, "get", fake_get)
    monkeypatch.setattr(sync_module.requests, "put", fake_put)

    result = commit_profile_to_github(_profile())

    assert result is True
    assert put_calls[0]["sha"] == "abc123"


def test_returns_false_when_put_fails(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    class FakeResponse:
        def __init__(self, status_code, json_body=None):
            self.status_code = status_code
            self._json = json_body or {}

        def json(self):
            return self._json

    import core.github_profile_sync as sync_module
    monkeypatch.setattr(sync_module.requests, "get", lambda *a, **k: FakeResponse(404))
    monkeypatch.setattr(sync_module.requests, "put", lambda *a, **k: FakeResponse(403))

    assert commit_profile_to_github(_profile()) is False


def test_returns_false_on_network_error(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    import core.github_profile_sync as sync_module

    def raise_connection_error(*args, **kwargs):
        raise sync_module.requests.RequestException("network down")

    monkeypatch.setattr(sync_module.requests, "get", raise_connection_error)

    assert commit_profile_to_github(_profile()) is False
