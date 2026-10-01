"""Unit tests for the bank-profile-learning path. Never calls the real
OpenAI API -- see tests/test_llm_extractor.py for the same mocking
approach."""
import json

import pytest

from core.bank_learner import build_bank_profile, learn_bank_profile
from core.pdf_parser import FinancialDocumentData, TransactionItem


def _fake_verified_data():
    return FinancialDocumentData(transactions=[
        TransactionItem(
            date="01/30", payee="Deposit", description="Deposit", amount=100.0,
            is_deposit=True, category="Gross Receipts", confidence_state="High Confidence",
            confidence_score=0.95, source_file="statement.pdf",
        ),
    ])


def test_build_bank_profile_maps_payload():
    payload = {
        "bank_slug": "Wells Fargo",
        "display_name": "Wells Fargo",
        "deposit_headers": ["Deposits and Other Credits"],
        "withdrawal_headers": ["Withdrawals and Other Debits"],
        "checks_headers": ["Checks Paid"],
        "daily_balance_skip_headers": ["Daily Ledger Balance"],
    }
    profile = build_bank_profile(payload)
    assert profile.bank_slug == "wells_fargo"
    assert profile.deposit_headers == ["deposits and other credits"]


def test_build_bank_profile_returns_none_for_empty_slug():
    assert build_bank_profile({"bank_slug": "   "}) is None


def test_learn_bank_profile_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = learn_bank_profile("raw text", _fake_verified_data(), "statement.pdf")
    assert result is None


def test_learn_bank_profile_uses_mocked_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")

    fake_payload = {
        "bank_slug": "wells_fargo",
        "display_name": "Wells Fargo",
        "deposit_headers": ["deposits and other credits"],
        "withdrawal_headers": ["withdrawals and other debits"],
        "checks_headers": ["checks paid"],
        "daily_balance_skip_headers": ["daily ledger balance"],
    }

    class FakeResponse:
        output_text = json.dumps(fake_payload)

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["text"]["format"]["name"] == "record_bank_profile"
            return FakeResponse()

    class FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.responses = FakeResponses()

    import sys
    fake_openai_module = type("FakeOpenAIModule", (), {"OpenAI": FakeOpenAIClient})
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    profile = learn_bank_profile("raw statement text", _fake_verified_data(), "statement.pdf")
    assert profile is not None
    assert profile.bank_slug == "wells_fargo"


def test_learn_bank_profile_returns_none_when_client_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")

    class FakeResponses:
        def create(self, **kwargs):
            raise RuntimeError("network error")

    class FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.responses = FakeResponses()

    import sys
    fake_openai_module = type("FakeOpenAIModule", (), {"OpenAI": FakeOpenAIClient})
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    assert learn_bank_profile("raw text", _fake_verified_data(), "statement.pdf") is None


def test_learn_bank_profile_returns_none_on_malformed_json(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")

    class FakeResponse:
        output_text = "{not valid json"

    class FakeResponses:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.responses = FakeResponses()

    import sys
    fake_openai_module = type("FakeOpenAIModule", (), {"OpenAI": FakeOpenAIClient})
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    assert learn_bank_profile("raw text", _fake_verified_data(), "statement.pdf") is None
