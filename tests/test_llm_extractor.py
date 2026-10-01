"""Unit tests for the AI fallback extraction path. Never calls the real
OpenAI API -- extract_transactions_llm's own API-calling code is
exercised via a mocked client (monkeypatched `openai` module), and
build_financial_document_data (the part that actually matters for
correctness -- mapping a structured payload into TransactionItem/
FinancialDocumentData) is tested directly against a fake payload."""
import json

import pytest

from core.llm_extractor import build_financial_document_data, extract_transactions_llm
from core.tax_categorizer import TaxCategorizer


def test_build_financial_document_data_maps_payload_to_transactions():
    payload = {
        "transactions": [
            {"date": "01/30", "payee": "Deposit 1188271441", "amount": 6293.94, "is_deposit": True},
            {"date": "01/03", "payee": "Card Purchase Inside Real Estate", "amount": 250.00, "is_deposit": False},
        ],
        "declared": {
            "beginning_balance": 43288.06,
            "ending_balance": 45446.47,
            "total_deposits": 6293.94,
            "total_withdrawals": 250.00,
        },
    }
    data = build_financial_document_data(payload, "Chase 01 31 2023.pdf", TaxCategorizer())

    assert len(data.transactions) == 2
    dep = next(t for t in data.transactions if t.is_deposit)
    wd = next(t for t in data.transactions if not t.is_deposit)
    assert dep.amount == pytest.approx(6293.94)
    assert wd.amount == pytest.approx(-250.00)  # withdrawals are stored negative, same as every rule-based strategy
    assert data.total_deposits == pytest.approx(6293.94)
    assert data.total_withdrawals == pytest.approx(250.00)
    assert data.bank_name == "Unrecognized format (AI fallback)"


def test_build_financial_document_data_handles_null_declared_figures():
    payload = {
        "transactions": [],
        "declared": {
            "beginning_balance": None, "ending_balance": None,
            "total_deposits": None, "total_withdrawals": None,
        },
    }
    data = build_financial_document_data(payload, "statement.pdf", TaxCategorizer())
    assert data.total_deposits == 0.0
    assert data.total_withdrawals == 0.0


def test_extract_transactions_llm_raises_clean_error_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        extract_transactions_llm(b"%PDF-fake", "statement.pdf", 2025, TaxCategorizer())


def test_extract_transactions_llm_uses_mocked_client_and_maps_result(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")

    fake_payload = {
        "transactions": [
            {"date": "01/30", "payee": "Deposit", "amount": 100.0, "is_deposit": True},
        ],
        "declared": {
            "beginning_balance": 0.0, "ending_balance": 100.0,
            "total_deposits": 100.0, "total_withdrawals": 0.0,
        },
    }

    class FakeResponse:
        output_text = json.dumps(fake_payload)

    class FakeResponses:
        def create(self, **kwargs):
            # Confirms the call shape without depending on real network access.
            assert kwargs["model"] == "gpt-6-luna"
            assert kwargs["text"]["format"]["name"] == "record_statement_extraction"
            assert kwargs["text"]["format"]["strict"] is True
            content = kwargs["input"][0]["content"]
            assert content[0]["type"] == "input_file"
            assert content[0]["file_data"].startswith("data:application/pdf;base64,")
            return FakeResponse()

    class FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.responses = FakeResponses()

    import sys
    fake_openai_module = type("FakeOpenAIModule", (), {"OpenAI": FakeOpenAIClient})
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    data = extract_transactions_llm(b"%PDF-fake", "statement.pdf", 2025, TaxCategorizer())
    assert len(data.transactions) == 1
    assert data.transactions[0].amount == pytest.approx(100.0)
    assert data.total_deposits == pytest.approx(100.0)


def test_extract_transactions_llm_raises_when_output_is_empty(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")

    class FakeResponse:
        output_text = ""

    class FakeResponses:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.responses = FakeResponses()

    import sys
    fake_openai_module = type("FakeOpenAIModule", (), {"OpenAI": FakeOpenAIClient})
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    with pytest.raises(RuntimeError, match="did not return a structured extraction"):
        extract_transactions_llm(b"%PDF-fake", "statement.pdf", 2025, TaxCategorizer())
