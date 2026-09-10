import pytest

from core.pdf_parser import BankPDFParser, FinancialDocumentData
from core.reconciliation import ReconciliationChecker


def _tx(amount, is_deposit):
    return {"amount": amount, "is_deposit": is_deposit}


def test_capture_balances_reads_account_summary_lines():
    parser = BankPDFParser()
    data = FinancialDocumentData()

    parser._capture_balances("Beginning Balance $1,000.00", data)
    parser._capture_balances("Total Deposits 5,000.00", data)
    parser._capture_balances("Total Withdrawals $2,500.00", data)
    parser._capture_balances("Ending Balance $3,500.00", data)

    assert data.beginning_balance == 1000.00
    assert data.total_deposits == 5000.00
    assert data.total_withdrawals == 2500.00
    assert data.ending_balance == 3500.00


def test_capture_balances_keeps_first_occurrence():
    """Per-page footers repeat the ending balance; only the summary is authoritative."""
    parser = BankPDFParser()
    data = FinancialDocumentData()

    parser._capture_balances("Ending Balance $3,500.00", data)
    parser._capture_balances("Ending Balance $99.00", data)

    assert data.ending_balance == 3500.00


def test_reconciled_when_extraction_matches_statement():
    checker = ReconciliationChecker()
    summary = {
        "document_type": "bank_statement",
        "beginning_balance": 1000.00,
        "ending_balance": 3500.00,
        "total_deposits": 5000.00,
        "total_withdrawals": 2500.00,
    }
    transactions = [_tx(5000.00, True), _tx(-2500.00, False)]

    result = checker.check_document("statement.pdf", summary, transactions)

    assert result["status"] == "Reconciled"
    assert result["deposit_difference"] == 0.0
    assert result["withdrawal_difference"] == 0.0


def test_discrepancy_when_a_transaction_is_dropped():
    """The check that matters: a dropped row must not pass silently."""
    checker = ReconciliationChecker()
    summary = {
        "document_type": "bank_statement",
        "beginning_balance": 1000.00,
        "ending_balance": 3500.00,
        "total_deposits": 5000.00,
        "total_withdrawals": 2500.00,
    }
    # A $400 withdrawal was never extracted.
    transactions = [_tx(5000.00, True), _tx(-2100.00, False)]

    result = checker.check_document("statement.pdf", summary, transactions)

    assert result["status"] == "Discrepancy"
    assert result["withdrawal_difference"] == 400.00
    assert "not extracted" in result["notes"]


def test_credit_card_balance_direction_is_reversed():
    checker = ReconciliationChecker()
    summary = {
        "document_type": "credit_card",
        "beginning_balance": 500.00,
        "ending_balance": 800.00,
        "total_deposits": 200.00,      # payments/credits
        "total_withdrawals": 500.00,   # purchases/charges
    }
    # A credit card payment is is_deposit=False (it's money leaving the
    # business's checking account) -- extraction distinguishes a payment from
    # a new charge by category, not by is_deposit. See
    # test_credit_card_reconciliation_uses_category_not_is_deposit below for
    # the regression this pins.
    payment = dict(_tx(-200.00, False), category="Non-P&L: Credit Card Payment")
    charge = dict(_tx(-500.00, False), category="Line 27a: Other expenses (Uncategorized)")
    transactions = [payment, charge]

    result = checker.check_document("card.pdf", summary, transactions)

    # 500 + 500 charges - 200 payments == 800
    assert result["status"] == "Reconciled"


def test_credit_card_reconciliation_uses_category_not_is_deposit():
    """A real regression: summing by is_deposit compared declared payments
    against a number that's near-zero on a real credit card (almost nothing
    is genuinely "money into the business" on a card), flagging every single
    card statement as a discrepancy regardless of whether anything was
    actually missing -- confirmed on 9 of 11 real Capital One statements
    with correct extraction underneath. A payment is a real Non-P&L category,
    not a deposit."""
    checker = ReconciliationChecker()
    summary = {
        "document_type": "credit_card",
        "beginning_balance": 4280.82,
        "ending_balance": 2193.83,
        "total_deposits": 2250.00,   # "Payments" from the account summary box
        "total_withdrawals": 163.01,  # "Transactions" (new charges)
    }
    payment = dict(_tx(-2250.00, False), category="Non-P&L: Credit Card Payment")
    purchase = dict(_tx(-163.01, False), category="Line 9: Car and truck expenses")

    result = checker.check_document("card.pdf", summary, [payment, purchase])

    assert result["status"] == "Reconciled"


def test_document_without_balances_is_not_reconcilable_not_a_pass():
    checker = ReconciliationChecker()

    result = checker.check_document("scan.pdf", {"document_type": "bank_or_receipt"},
                                    [_tx(-50.00, False)])

    assert result["status"] == "Not Reconcilable"
    assert result["status"] != "Reconciled"


def test_summary_flags_any_discrepancy():
    checker = ReconciliationChecker()
    results = [
        {"status": "Reconciled"},
        {"status": "Discrepancy"},
        {"status": "Not Reconcilable"},
    ]

    summary = checker.summarize(results)

    assert summary["status"] == "Discrepancy"
    assert summary["discrepancy_count"] == 1
    assert summary["reconciled_count"] == 1
    assert summary["unverifiable_count"] == 1


def test_summary_of_no_documents_is_not_a_pass():
    assert ReconciliationChecker().summarize([])["status"] == "Not Reconcilable"
