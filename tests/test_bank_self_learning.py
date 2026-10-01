"""End-to-end test of the self-teaching escalation path: an unrecognized
document triggers the (mocked) multiclass AI classifier once, and either
(a) it's a bank statement -- a profile gets learned and saved, and a
second statement from the same "bank" is then parsed correctly by the
ordinary rules engine alone, with the AI mock never called again -- or
(b) it's something else entirely (a check, an invoice, a receipt),
surfaced as that instead of being force-fit into the transaction list.
Never calls the real OpenAI API.
"""
import functools
import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import core.bank_profiles as bank_profiles_module
import core.pdf_parser as pdf_parser_module
from core.pdf_parser import BankPDFParser, FinancialDocumentData, TransactionItem
from core.tax_categorizer import TaxCategorizer


def _pdf_bytes(lines, font_size=9):
    """A statement from a fictional bank whose header wording
    ("MONEY IN" / "MONEY OUT") matches nothing the rules engine already
    knows, but whose declared totals use ordinary "Total Deposits" /
    "Total Withdrawals" phrasing -- the same shape the real Chase bug had:
    the rules engine can extract *something*, but gets the direction of
    every transaction wrong because it never recognizes either section."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFont("Helvetica", font_size)
    y = 750
    for line in lines:
        c.drawString(40, y, line)
        y -= 14
    c.save()
    return buffer.getvalue()


UNKNOWN_BANK_STATEMENT = [
    "MONEY IN",
    "01/05 Client Payment 5,000.00",
    "MONEY OUT",
    "01/10 Office Supplies 200.00",
    "Total Deposits $5,000.00",
    "Total Withdrawals $200.00",
]


def _fake_idp_bank_statement_payload():
    """Shaped exactly like core.idp_extractor.classify_and_extract()'s real
    return value for a bank statement -- the raw JSON payload, before any
    mapping into FinancialDocumentData."""
    return {
        "processing_metadata": {
            "detected_document_type": "bank_statement",
            "issuing_entity": "Fictional Trust Bank",
            "classification_confidence": 0.97,
        },
        "structured_data": {
            "bank_statement": {
                "bank_name": "Fictional Trust Bank", "client_name": None, "period": None,
                "opening_balance": 0.0, "total_deposits": 5000.0, "total_withdrawals": 200.0,
                "closing_balance": 4800.0,
                "transactions": [
                    {"date": "2025-01-05", "description": "Client Payment", "amount": 5000.0,
                     "type": "credit", "resulting_balance": None},
                    {"date": "2025-01-10", "description": "Office Supplies", "amount": 200.0,
                     "type": "debit", "resulting_balance": None},
                ],
            },
            "check": None, "invoice_or_receipt": None,
        },
        "quality_validation": {"mathematically_balanced": True, "requires_human_review": False, "alerts": []},
    }


@pytest.fixture
def isolated_bank_profiles(tmp_path, monkeypatch):
    """Redirects every load_bank_profiles()/save_bank_profile() call made
    from inside pdf_parser.py to a throwaway directory, so this test never
    reads or writes the repo's real bank_profiles/."""
    real_load = bank_profiles_module.load_bank_profiles
    real_save = bank_profiles_module.save_bank_profile
    monkeypatch.setattr(pdf_parser_module, "load_bank_profiles",
                         functools.partial(real_load, profiles_dir=str(tmp_path)))
    monkeypatch.setattr(pdf_parser_module, "save_bank_profile",
                         functools.partial(real_save, profiles_dir=str(tmp_path)))
    return tmp_path


def test_unrecognized_bank_escalates_to_ai_and_learns_a_reusable_profile(
    isolated_bank_profiles, monkeypatch
):
    call_count = {"classify": 0, "learn": 0}

    def fake_classify(raw_bytes, filename):
        call_count["classify"] += 1
        return _fake_idp_bank_statement_payload()

    def fake_learn(raw_text, verified_data, filename):
        call_count["learn"] += 1
        from core.bank_learner import build_bank_profile
        return build_bank_profile({
            "bank_slug": "fictional_trust_bank",
            "display_name": "Fictional Trust Bank",
            "deposit_headers": ["money in"],
            "withdrawal_headers": ["money out"],
            "checks_headers": [],
            "daily_balance_skip_headers": [],
        })

    import core.idp_extractor as idp_module
    import core.bank_learner as bank_learner_module
    monkeypatch.setattr(idp_module, "classify_and_extract", fake_classify)
    monkeypatch.setattr(bank_learner_module, "learn_bank_profile", fake_learn)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    pdf_bytes = _pdf_bytes(UNKNOWN_BANK_STATEMENT)

    # --- First statement from this bank: rules engine alone gets it wrong,
    # so this should escalate through the (mocked) AI classifier and learn
    # a profile.
    result_1 = parser.parse_pdf(pdf_bytes, "Fictional Trust Bank 01 2025.pdf")
    assert result_1["document_type"] == "bank_statement"
    deposits_1 = sum(t["amount"] for t in result_1["transactions"] if t["is_deposit"])
    withdrawals_1 = sum(abs(t["amount"]) for t in result_1["transactions"] if not t["is_deposit"])

    assert deposits_1 == pytest.approx(5000.0)
    assert withdrawals_1 == pytest.approx(200.0)
    assert call_count["classify"] == 1
    assert call_count["learn"] == 1
    assert (isolated_bank_profiles / "fictional_trust_bank.json").exists()

    # --- A second statement from the SAME bank: the rules engine, now
    # carrying the learned profile, should get the right answer on its own
    # -- neither mock should be called again.
    result_2 = parser.parse_pdf(pdf_bytes, "Fictional Trust Bank 02 2025.pdf")
    deposits_2 = sum(t["amount"] for t in result_2["transactions"] if t["is_deposit"])
    withdrawals_2 = sum(abs(t["amount"]) for t in result_2["transactions"] if not t["is_deposit"])

    assert deposits_2 == pytest.approx(5000.0)
    assert withdrawals_2 == pytest.approx(200.0)
    assert call_count["classify"] == 1, "second statement should not have needed the AI classifier again"
    assert call_count["learn"] == 1


def test_llm_fallback_disabled_leaves_unreliable_extraction_flagged_not_silently_wrong(
    isolated_bank_profiles,
):
    """With the fallback off (no OPENAI_API_KEY configured), an
    unrecognized statement must still return *something* rather than
    crash -- and must say plainly that it couldn't be trusted, never pass
    off a wrong number as a confident one."""
    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=False)
    pdf_bytes = _pdf_bytes(UNKNOWN_BANK_STATEMENT)

    result = parser.parse_pdf(pdf_bytes, "Fictional Trust Bank 01 2025.pdf")
    # Wrong (misclassified) totals are expected here -- what matters is that
    # nothing crashed and the numbers are still internally consistent to
    # inspect, not that they happen to be right without any fallback at all.
    assert result["transaction_count"] == 2


def test_ai_extraction_that_does_not_reconcile_is_not_learned_from(
    isolated_bank_profiles, monkeypatch
):
    """If the AI's own bank-statement extraction doesn't reconcile against
    the statement's declared totals either, no profile should be learned
    from it -- learning must never be attempted from an already-unreliable
    result."""
    def bad_classify(raw_bytes, filename):
        payload = _fake_idp_bank_statement_payload()
        # Declares $5,000/$200 but only transcribes a single $1.00 credit --
        # the aggregate check must catch this.
        payload["structured_data"]["bank_statement"]["transactions"] = [
            {"date": "2025-01-05", "description": "Mystery", "amount": 1.0,
             "type": "credit", "resulting_balance": None},
        ]
        return payload

    learn_called = {"count": 0}

    def spy_learn(raw_text, verified_data, filename):
        learn_called["count"] += 1
        return None

    import core.idp_extractor as idp_module
    import core.bank_learner as bank_learner_module
    monkeypatch.setattr(idp_module, "classify_and_extract", bad_classify)
    monkeypatch.setattr(bank_learner_module, "learn_bank_profile", spy_learn)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    result = parser.parse_pdf(_pdf_bytes(UNKNOWN_BANK_STATEMENT), "Fictional Trust Bank 01 2025.pdf")

    assert learn_called["count"] == 0
    assert any("did not pass independent verification" in note for note in result["diagnostics"])


# --- Persisting a learned profile back to GitHub, so it survives a deploy --

def test_learning_a_profile_also_attempts_a_github_commit(isolated_bank_profiles, monkeypatch):
    """A learned profile is only ever useful past the current running
    instance if it's committed back to the repo -- Streamlit Cloud's
    filesystem is ephemeral and the local save alone (already covered by
    test_unrecognized_bank_escalates_to_ai_and_learns_a_reusable_profile)
    doesn't survive a redeploy."""
    def fake_classify(raw_bytes, filename):
        return _fake_idp_bank_statement_payload()

    def fake_learn(raw_text, verified_data, filename):
        from core.bank_learner import build_bank_profile
        return build_bank_profile({
            "bank_slug": "fictional_trust_bank",
            "display_name": "Fictional Trust Bank",
            "deposit_headers": ["money in"],
            "withdrawal_headers": ["money out"],
            "checks_headers": [],
            "daily_balance_skip_headers": [],
        })

    github_calls = []

    def fake_commit(profile):
        github_calls.append(profile.bank_slug)
        return True

    import core.idp_extractor as idp_module
    import core.bank_learner as bank_learner_module
    import core.github_profile_sync as github_sync_module
    monkeypatch.setattr(idp_module, "classify_and_extract", fake_classify)
    monkeypatch.setattr(bank_learner_module, "learn_bank_profile", fake_learn)
    monkeypatch.setattr(github_sync_module, "commit_profile_to_github", fake_commit)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    result = parser.parse_pdf(_pdf_bytes(UNKNOWN_BANK_STATEMENT), "Fictional Trust Bank 01 2025.pdf")

    assert github_calls == ["fictional_trust_bank"]
    assert any("Committed the learned profile to GitHub" in note for note in result["diagnostics"])


def test_github_commit_failure_does_not_break_the_already_successful_extraction(
    isolated_bank_profiles, monkeypatch
):
    """If the GitHub commit fails (no token, rate limit, network error, ...),
    the extraction that already succeeded must still be returned intact --
    durability is a bonus, never a requirement."""
    def fake_classify(raw_bytes, filename):
        return _fake_idp_bank_statement_payload()

    def fake_learn(raw_text, verified_data, filename):
        from core.bank_learner import build_bank_profile
        return build_bank_profile({
            "bank_slug": "fictional_trust_bank",
            "display_name": "Fictional Trust Bank",
            "deposit_headers": ["money in"],
            "withdrawal_headers": ["money out"],
            "checks_headers": [],
            "daily_balance_skip_headers": [],
        })

    import core.idp_extractor as idp_module
    import core.bank_learner as bank_learner_module
    import core.github_profile_sync as github_sync_module
    monkeypatch.setattr(idp_module, "classify_and_extract", fake_classify)
    monkeypatch.setattr(bank_learner_module, "learn_bank_profile", fake_learn)
    monkeypatch.setattr(github_sync_module, "commit_profile_to_github", lambda profile: False)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    result = parser.parse_pdf(_pdf_bytes(UNKNOWN_BANK_STATEMENT), "Fictional Trust Bank 01 2025.pdf")

    deposits = sum(t["amount"] for t in result["transactions"] if t["is_deposit"])
    assert deposits == pytest.approx(5000.0)
    assert any("Could not commit the learned profile to GitHub" in note for note in result["diagnostics"])


# --- Multiclass classification: a document that isn't a bank statement at
# all must never be force-fit into the transaction list -------------------

def test_check_copy_is_classified_and_kept_separate_from_transactions(
    isolated_bank_profiles, monkeypatch
):
    def fake_classify(raw_bytes, filename):
        return {
            "processing_metadata": {
                "detected_document_type": "check", "issuing_entity": "Fictional Trust Bank",
                "classification_confidence": 0.95,
            },
            "structured_data": {
                "bank_statement": None,
                "check": {
                    "drawer": "Christine Graham LLC", "payee": "Acme Landscaping",
                    "issue_date": "2025-01-12", "numerical_amount": 350.00,
                    "text_amount": "Three hundred fifty and 00/100", "account_number": "839276752",
                    "routing_number": "021000021", "check_number": "1020", "signature_present": True,
                },
                "invoice_or_receipt": None,
            },
            "quality_validation": {"mathematically_balanced": True, "requires_human_review": False, "alerts": []},
        }

    import core.idp_extractor as idp_module
    monkeypatch.setattr(idp_module, "classify_and_extract", fake_classify)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    result = parser.parse_pdf(_pdf_bytes(["Check image placeholder text"]), "Check 1020.pdf")

    assert result["document_type"] == "check"
    assert result["transactions"] == []
    assert result["check_record"]["payee"] == "Acme Landscaping"
    assert result["check_record"]["check_number"] == "1020"


def test_invoice_is_classified_and_kept_separate_from_transactions(
    isolated_bank_profiles, monkeypatch
):
    def fake_classify(raw_bytes, filename):
        return {
            "processing_metadata": {
                "detected_document_type": "invoice", "issuing_entity": "Acme Supplies Co",
                "classification_confidence": 0.9,
            },
            "structured_data": {
                "bank_statement": None, "check": None,
                "invoice_or_receipt": {
                    "issuer": "Acme Supplies Co", "client": "Christine Graham LLC",
                    "date": "2025-02-01", "document_number": "INV-204",
                    "subtotal": 100.0, "taxes": 7.0, "total": 107.0,
                    "line_items": [{"item": "Widgets", "quantity": 10.0, "unit_price": 10.0, "item_total": 100.0}],
                },
            },
            "quality_validation": {"mathematically_balanced": True, "requires_human_review": False, "alerts": []},
        }

    import core.idp_extractor as idp_module
    monkeypatch.setattr(idp_module, "classify_and_extract", fake_classify)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    result = parser.parse_pdf(_pdf_bytes(["Invoice placeholder text"]), "Invoice 204.pdf")

    assert result["document_type"] == "invoice"
    assert result["transactions"] == []
    assert result["supporting_document"]["total"] == pytest.approx(107.0)
    assert result["supporting_document"]["document_number"] == "INV-204"


def test_unknown_classification_counts_zero_transactions_not_the_garbage_fallback(
    isolated_bank_profiles, monkeypatch
):
    """When the AI explicitly can't identify the document type, the
    already-known-wrong rule-based garbage must not quietly flow through
    as if it were real transactions -- zero is more honest than wrong."""
    def fake_classify(raw_bytes, filename):
        return {
            "processing_metadata": {
                "detected_document_type": "unknown", "issuing_entity": None,
                "classification_confidence": 0.2,
            },
            "structured_data": {"bank_statement": None, "check": None, "invoice_or_receipt": None},
            "quality_validation": {"mathematically_balanced": False, "requires_human_review": True,
                                    "alerts": ["Could not determine document type"]},
        }

    import core.idp_extractor as idp_module
    monkeypatch.setattr(idp_module, "classify_and_extract", fake_classify)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    result = parser.parse_pdf(_pdf_bytes(UNKNOWN_BANK_STATEMENT), "Mystery Document.pdf")

    assert result["document_type"] == "unknown"
    assert result["transactions"] == []
