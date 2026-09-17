"""Unit tests for the bank-profile-learning path. Never calls the real
Anthropic API -- see tests/test_llm_extractor.py for the same mocking
approach."""
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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = learn_bank_profile("raw text", _fake_verified_data(), "statement.pdf")
    assert result is None


def test_learn_bank_profile_uses_mocked_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")

    class FakeToolUseBlock:
        type = "tool_use"
        input = {
            "bank_slug": "wells_fargo",
            "display_name": "Wells Fargo",
            "deposit_headers": ["deposits and other credits"],
            "withdrawal_headers": ["withdrawals and other debits"],
            "checks_headers": ["checks paid"],
            "daily_balance_skip_headers": ["daily ledger balance"],
        }

    class FakeResponse:
        content = [FakeToolUseBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            assert kwargs["tools"][0]["name"] == "record_bank_profile"
            return FakeResponse()

    class FakeAnthropicClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    import sys
    fake_anthropic_module = type("FakeAnthropicModule", (), {"Anthropic": FakeAnthropicClient})
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic_module)

    profile = learn_bank_profile("raw statement text", _fake_verified_data(), "statement.pdf")
    assert profile is not None
    assert profile.bank_slug == "wells_fargo"


def test_learn_bank_profile_returns_none_when_client_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")

    class FakeMessages:
        def create(self, **kwargs):
            raise RuntimeError("network error")

    class FakeAnthropicClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    import sys
    fake_anthropic_module = type("FakeAnthropicModule", (), {"Anthropic": FakeAnthropicClient})
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic_module)

    assert learn_bank_profile("raw text", _fake_verified_data(), "statement.pdf") is None
