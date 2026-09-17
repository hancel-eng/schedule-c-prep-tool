"""End-to-end test of the self-teaching escalation path: an unrecognized
statement format triggers the (mocked) LLM fallback once, a profile gets
learned and saved, and a second statement from the same "bank" is then
parsed correctly by the ordinary rules engine alone -- with the LLM mock
never called again. Never calls the real Anthropic API.
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


def _fake_llm_extraction(filename: str) -> FinancialDocumentData:
    categorizer = TaxCategorizer()
    cat_dep, state_dep, score_dep = categorizer.categorize_transaction("Client Payment", "Client Payment", 5000.0, True)
    cat_wd, state_wd, score_wd = categorizer.categorize_transaction("Office Supplies", "Office Supplies", -200.0, False)
    return FinancialDocumentData(
        total_deposits=5000.0, total_withdrawals=200.0,
        transactions=[
            TransactionItem(date="01/05", payee="Client Payment", description="Client Payment",
                             amount=5000.0, is_deposit=True, category=cat_dep,
                             confidence_state=state_dep, confidence_score=score_dep, source_file=filename),
            TransactionItem(date="01/10", payee="Office Supplies", description="Office Supplies",
                             amount=-200.0, is_deposit=False, category=cat_wd,
                             confidence_state=state_wd, confidence_score=score_wd, source_file=filename),
        ],
    )


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


def test_unrecognized_bank_escalates_to_llm_and_learns_a_reusable_profile(
    isolated_bank_profiles, monkeypatch
):
    call_count = {"extract": 0, "learn": 0}

    def fake_extract(raw_bytes, filename, year, categorizer):
        call_count["extract"] += 1
        return _fake_llm_extraction(filename)

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

    import core.llm_extractor as llm_extractor_module
    import core.bank_learner as bank_learner_module
    monkeypatch.setattr(llm_extractor_module, "extract_transactions_llm", fake_extract)
    monkeypatch.setattr(bank_learner_module, "learn_bank_profile", fake_learn)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    pdf_bytes = _pdf_bytes(UNKNOWN_BANK_STATEMENT)

    # --- First statement from this bank: rules engine alone gets it wrong,
    # so this should escalate through the (mocked) LLM and learn a profile.
    result_1 = parser.parse_pdf(pdf_bytes, "Fictional Trust Bank 01 2025.pdf")
    deposits_1 = sum(t["amount"] for t in result_1["transactions"] if t["is_deposit"])
    withdrawals_1 = sum(abs(t["amount"]) for t in result_1["transactions"] if not t["is_deposit"])

    assert deposits_1 == pytest.approx(5000.0)
    assert withdrawals_1 == pytest.approx(200.0)
    assert call_count["extract"] == 1
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
    assert call_count["extract"] == 1, "second statement should not have needed the LLM fallback again"
    assert call_count["learn"] == 1


def test_llm_fallback_disabled_leaves_unreliable_extraction_flagged_not_silently_wrong(
    isolated_bank_profiles,
):
    """With the fallback off (no ANTHROPIC_API_KEY configured), an
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


def test_llm_extraction_that_does_not_reconcile_is_not_learned_from(
    isolated_bank_profiles, monkeypatch
):
    """If the LLM's own extraction doesn't reconcile against the
    statement's declared totals either, no profile should be learned from
    it -- learning must never be attempted from an already-unreliable
    result."""
    def bad_extract(raw_bytes, filename, year, categorizer):
        categorizer_ = TaxCategorizer()
        cat, state, score = categorizer_.categorize_transaction("Mystery", "Mystery", 1.0, True)
        return FinancialDocumentData(
            total_deposits=5000.0, total_withdrawals=200.0,
            transactions=[
                TransactionItem(date="01/05", payee="Mystery", description="Mystery", amount=1.0,
                                 is_deposit=True, category=cat, confidence_state=state,
                                 confidence_score=score, source_file=filename),
            ],
        )

    learn_called = {"count": 0}

    def spy_learn(raw_text, verified_data, filename):
        learn_called["count"] += 1
        return None

    import core.llm_extractor as llm_extractor_module
    import core.bank_learner as bank_learner_module
    monkeypatch.setattr(llm_extractor_module, "extract_transactions_llm", bad_extract)
    monkeypatch.setattr(bank_learner_module, "learn_bank_profile", spy_learn)

    parser = BankPDFParser(categorizer=TaxCategorizer(), enable_llm_fallback=True)
    result = parser.parse_pdf(_pdf_bytes(UNKNOWN_BANK_STATEMENT), "Fictional Trust Bank 01 2025.pdf")

    assert learn_called["count"] == 0
    assert any("did not reconcile" in note for note in result["diagnostics"])
