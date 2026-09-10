import pytest

from core.answer_applier import (
    ASSET_CONFIRMED_CATEGORY,
    PERSONAL_CONFIRMED_CATEGORY,
    apply_client_answers,
    uncategorized_expense_answer_options,
)
from core.transaction_utils import transaction_key


def _tx(date="01/15/2025", payee="Some Vendor", amount=-100.0, category="Line 27a: Other expenses (Uncategorized)",
        confidence_state="Needs Review", source_file="statement.pdf"):
    return {
        "date": date, "payee": payee, "description": payee, "amount": amount,
        "is_deposit": False, "category": category, "confidence_state": confidence_state,
        "confidence_score": 0.4, "source_file": source_file, "original_category": "",
    }


def _question(tx, category, answer="", client_response="", item_id="Q-001"):
    return {
        "item_id": item_id,
        "category": category,
        "transaction_keys": [transaction_key(tx)],
        "date": tx["date"], "payee": tx["payee"], "amount": f"${abs(tx['amount']):,.2f}",
        "question": "N/A",
        "client_response": client_response,
        "answer": answer,
    }


# --- Expense Verification ---------------------------------------------------

def test_personal_answer_excludes_transaction_from_schedule_c():
    tx = _tx(payee="Nordstrom", amount=-89.99)
    q = _question(tx, "Expense Verification", answer="Personal")

    updated, log = apply_client_answers([tx], [q])

    assert updated[0]["category"] == PERSONAL_CONFIRMED_CATEGORY
    assert updated[0]["category"].startswith("Non-P&L:")
    assert log[0]["applied"] is True
    assert log[0]["old_category"] == "Line 27a: Other expenses (Uncategorized)"


def test_business_answer_upgrades_confidence_without_changing_category():
    tx = _tx(payee="Some Vendor", category="Line 18: Office expense", confidence_state="Needs Review")
    q = _question(tx, "Expense Verification", answer="Business")

    updated, log = apply_client_answers([tx], [q])

    assert updated[0]["category"] == "Line 18: Office expense"
    assert updated[0]["confidence_state"] == "High Confidence"


def test_unclear_answer_changes_nothing():
    tx = _tx()
    q = _question(tx, "Expense Verification", answer="Unclear")

    updated, log = apply_client_answers([tx], [q])

    assert updated[0]["category"] == tx["category"]
    assert updated[0]["confidence_state"] == tx["confidence_state"]
    assert log == []  # nothing applied, no log entry


def test_blank_answer_is_skipped_entirely():
    tx = _tx()
    q = _question(tx, "Expense Verification", answer="")

    updated, log = apply_client_answers([tx], [q])

    assert updated[0] == tx
    assert log == []


# --- Asset Purchase ----------------------------------------------------------

def test_confirmed_asset_recategorizes_to_depreciation_line():
    tx = _tx(payee="Best Buy", amount=-2899.0)
    q = _question(tx, "Asset Purchase", answer="Confirmed Asset",
                  client_response="MacBook Pro, placed in service 03/12/2025")

    updated, log = apply_client_answers([tx], [q])

    assert updated[0]["category"] == ASSET_CONFIRMED_CATEGORY
    assert "MacBook Pro" in log[0]["note"]


def test_not_an_asset_leaves_category_but_upgrades_confidence():
    tx = _tx(payee="Home Depot", amount=-2600.0, category="Line 22: Supplies")
    q = _question(tx, "Asset Purchase", answer="Not an Asset")

    updated, log = apply_client_answers([tx], [q])

    assert updated[0]["category"] == "Line 22: Supplies"
    assert updated[0]["confidence_state"] == "High Confidence"


# --- Uncategorized Expense ---------------------------------------------------

def test_recognized_category_answer_recategorizes_transaction():
    tx = _tx(payee="Aluminum Vinyl and Bld")
    q = _question(tx, "Uncategorized Expense", answer="Line 21: Repairs and maintenance")

    updated, log = apply_client_answers([tx], [q])

    assert updated[0]["category"] == "Line 21: Repairs and maintenance"
    assert updated[0]["confidence_state"] == "High Confidence"
    assert log[0]["applied"] is True


def test_unrecognized_category_answer_is_never_guessed():
    """The client's own words ("landscaping stuff") are not a Schedule C
    category -- the transaction must stay untouched, not silently mapped."""
    tx = _tx(payee="Random Vendor LLC")
    q = _question(tx, "Uncategorized Expense", answer="landscaping stuff")

    updated, log = apply_client_answers([tx], [q])

    assert updated[0]["category"] == tx["category"]  # unchanged
    assert log[0]["applied"] is False
    assert "not a recognized" in log[0]["note"]


def test_uncategorized_answer_options_matches_the_categorizer():
    from core.tax_categorizer import SCHEDULE_C_CATEGORIES
    options = uncategorized_expense_answer_options()
    assert "" in options
    for cat in SCHEDULE_C_CATEGORIES:
        assert cat in options
    assert "Non-P&L: Loan Proceeds / Repayment" in options


# --- Form 1099 Verification --------------------------------------------------

def test_1099_answer_logs_a_compliance_note_without_touching_transactions():
    tx = _tx(payee="John Subcontractor", category="Line 11: Contract labor")
    q = {
        "item_id": "Q-009", "category": "Form 1099 Verification", "transaction_keys": [],
        "contractor": "John Subcontractor", "date": "Full Year 2025", "payee": "John Subcontractor",
        "amount": "$850.00", "question": "N/A", "client_response": "", "answer": "Filed",
    }

    updated, log = apply_client_answers([tx], [q])

    assert updated[0] == tx  # transaction untouched
    assert log[0]["applied"] is True
    assert "Filed" in log[0]["note"]
    assert "John Subcontractor" in log[0]["note"]


# --- Multiple transactions sharing a key ------------------------------------

def test_answer_applies_to_every_transaction_sharing_the_key():
    """Two genuinely identical charges (same date/payee/amount/file) share a
    key by design -- a client's answer about one applies to both."""
    tx1 = _tx(payee="Recurring Subscription", amount=-9.99)
    tx2 = _tx(payee="Recurring Subscription", amount=-9.99)
    q = _question(tx1, "Expense Verification", answer="Personal")

    updated, log = apply_client_answers([tx1, tx2], [q])

    assert all(t["category"] == PERSONAL_CONFIRMED_CATEGORY for t in updated)
    assert log[0]["matched_transactions"] == 2


# --- Missing transaction (stale question) -----------------------------------

def test_answer_for_a_transaction_no_longer_present_is_logged_not_applied():
    tx = _tx()
    stale_q = dict(_question(tx, "Expense Verification", answer="Personal"))
    stale_q["transaction_keys"] = ["some/other/key/that/does/not/exist"]

    updated, log = apply_client_answers([tx], [stale_q])

    assert updated[0] == tx
    assert log[0]["applied"] is False


# --- The loop actually closes: an applied answer doesn't regenerate --------

def test_personal_confirmed_expense_does_not_reappear_as_an_exception():
    """The end-to-end concern the whole feature exists to solve: once the
    client answers, the same question must not come back on the next
    recompute."""
    from core.exception_analyzer import ExceptionAnalyzer
    from core.question_generator import ClientQuestionGenerator

    tx = _tx(payee="Netflix Subscription", amount=-15.99)
    analyzer = ExceptionAnalyzer()
    q_gen = ClientQuestionGenerator()

    # Round 1: flagged, question generated.
    exceptions_1 = analyzer.analyze_exceptions([tx])
    assert len(exceptions_1["potential_personal"]) == 1
    questions_1 = q_gen.generate_question_list(exceptions_1)
    q = next(q for q in questions_1 if q["category"] == "Expense Verification")
    q["answer"] = "Personal"

    # Apply the answer.
    updated, log = apply_client_answers([tx], [q])
    assert log[0]["applied"] is True

    # Round 2: recompute from the corrected transaction -- must be gone.
    exceptions_2 = analyzer.analyze_exceptions(updated)
    assert exceptions_2["potential_personal"] == []
    questions_2 = q_gen.generate_question_list(exceptions_2)
    assert not any(q["category"] == "Expense Verification" for q in questions_2)


def test_auto_detected_non_pnl_transaction_is_not_suppressed():
    """A transaction the categorizer itself (not a client answer) placed in a
    Non-P&L or Line 13 category is a real, still-open exception if it also
    matches a personal/asset keyword -- only client_confirmed=True suppresses
    re-flagging, not the category text alone."""
    from core.exception_analyzer import ExceptionAnalyzer

    tx = _tx(payee="Target Corp Transfer", category="Non-P&L: Internal Transfer",
             confidence_state="High Confidence")
    result = ExceptionAnalyzer().analyze_exceptions([tx])
    assert len(result["potential_personal"]) == 1
