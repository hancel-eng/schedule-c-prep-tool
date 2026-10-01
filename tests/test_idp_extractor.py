"""Unit tests for the multiclass IDP classifier/extractor. Never calls the
real OpenAI API -- classify_and_extract's own API-calling code is exercised
via a mocked client (monkeypatched `openai` module); the pure-Python
verification and mapping functions are tested directly against fake
payloads."""
import json

import pytest

from core.idp_extractor import (
    classify_and_extract,
    verify_bank_statement_balance_chain,
    bank_statement_payload_to_transactions,
)
from core.tax_categorizer import TaxCategorizer


def test_classify_and_extract_raises_clean_error_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        classify_and_extract(b"%PDF-fake", "statement.pdf")


def test_classify_and_extract_uses_mocked_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")

    fake_payload = {
        "processing_metadata": {"detected_document_type": "bank_statement",
                                 "issuing_entity": "Test Bank", "classification_confidence": 0.9},
        "structured_data": {"bank_statement": {"bank_name": "Test Bank", "client_name": None,
                                                "period": None, "opening_balance": 0.0,
                                                "total_deposits": 0.0, "total_withdrawals": 0.0,
                                                "closing_balance": 0.0, "transactions": []},
                             "check": None, "invoice_or_receipt": None},
        "quality_validation": {"mathematically_balanced": True, "requires_human_review": False, "alerts": []},
    }

    class FakeResponse:
        output_text = json.dumps(fake_payload)

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["model"] == "gpt-6-astra"
            assert kwargs["text"]["format"]["name"] == "classify_and_extract_document"
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

    result = classify_and_extract(b"%PDF-fake", "statement.pdf")
    assert result["processing_metadata"]["detected_document_type"] == "bank_statement"


def test_classify_and_extract_uses_image_media_type_for_non_pdf(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")

    fake_payload = {
        "processing_metadata": {"detected_document_type": "check", "issuing_entity": None,
                                 "classification_confidence": 0.8},
        "structured_data": {"bank_statement": None, "check": None, "invoice_or_receipt": None},
        "quality_validation": {"mathematically_balanced": False, "requires_human_review": True, "alerts": []},
    }

    class FakeResponse:
        output_text = json.dumps(fake_payload)

    class FakeResponses:
        def create(self, **kwargs):
            content = kwargs["input"][0]["content"]
            assert content[0]["file_data"].startswith("data:image/png;base64,")
            return FakeResponse()

    class FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.responses = FakeResponses()

    import sys
    fake_openai_module = type("FakeOpenAIModule", (), {"OpenAI": FakeOpenAIClient})
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    classify_and_extract(b"\x89PNG-fake", "check.png")


def test_classify_and_extract_raises_when_output_is_empty(monkeypatch):
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
        classify_and_extract(b"%PDF-fake", "statement.pdf")


# --- verify_bank_statement_balance_chain ------------------------------------

def test_balance_chain_consistent_returns_true():
    bank_statement = {
        "opening_balance": 100.0,
        "transactions": [
            {"amount": 50.0, "type": "credit", "resulting_balance": 150.0},
            {"amount": 20.0, "type": "debit", "resulting_balance": 130.0},
        ],
    }
    assert verify_bank_statement_balance_chain(bank_statement) is True


def test_balance_chain_inconsistent_returns_false():
    """This is exactly the Wells Fargo bug shape: a transaction's amount
    was actually the running balance, not the real amount -- the chain
    breaks immediately."""
    bank_statement = {
        "opening_balance": 11436.72,
        "transactions": [
            {"amount": 11352.86, "type": "debit", "resulting_balance": 11352.86},  # should be $16.05
        ],
    }
    assert verify_bank_statement_balance_chain(bank_statement) is False


def test_balance_chain_returns_none_when_no_resulting_balance_present():
    bank_statement = {
        "opening_balance": 100.0,
        "transactions": [{"amount": 50.0, "type": "credit", "resulting_balance": None}],
    }
    assert verify_bank_statement_balance_chain(bank_statement) is None


def test_balance_chain_returns_none_without_opening_balance():
    bank_statement = {
        "opening_balance": None,
        "transactions": [{"amount": 50.0, "type": "credit", "resulting_balance": 150.0}],
    }
    assert verify_bank_statement_balance_chain(bank_statement) is None


# --- bank_statement_payload_to_transactions ---------------------------------

def test_bank_statement_payload_to_transactions_maps_direction_and_categorizes():
    bank_statement = {
        "transactions": [
            {"date": "2025-01-05", "description": "Client Payment", "amount": 500.0, "type": "credit"},
            {"date": "2025-01-10", "description": "Office Supplies", "amount": 50.0, "type": "debit"},
        ],
    }
    results = bank_statement_payload_to_transactions(bank_statement, "statement.pdf", TaxCategorizer())
    assert len(results) == 2
    dep = next(t for t in results if t["is_deposit"])
    wd = next(t for t in results if not t["is_deposit"])
    assert dep["amount"] == pytest.approx(500.0)
    assert wd["amount"] == pytest.approx(-50.0)
    assert dep["category"]  # categorizer ran, produced something
    assert wd["source_file"] == "statement.pdf"
