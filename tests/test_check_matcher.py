"""Unit tests for matching a standalone check-copy extraction against the
"Check #NNNN" transactions a bank statement's own cleared-checks listing
already produced -- see core/check_matcher.py."""
import pytest

from core.check_matcher import match_checks_to_transactions


def _tx(payee, amount=-100.0, is_deposit=False):
    return {"payee": payee, "description": payee, "amount": amount, "is_deposit": is_deposit,
            "original_category": "Check Document"}


def test_matches_by_check_number():
    transactions = [_tx("Check #1020", amount=-350.0), _tx("Check #1021", amount=-50.0)]
    checks = [{"check_number": "1020", "payee": "Acme Landscaping", "numerical_amount": 350.0}]

    matched_count, unmatched = match_checks_to_transactions(checks, transactions)

    assert matched_count == 1
    assert unmatched == []
    assert transactions[0]["payee"] == "Acme Landscaping"
    assert transactions[1]["payee"] == "Check #1021"  # untouched


def test_matches_by_amount_when_number_missing_and_unique():
    transactions = [_tx("Check (no number recorded)", amount=-275.50)]
    checks = [{"check_number": None, "payee": "Vendor Co", "numerical_amount": 275.50}]

    matched_count, unmatched = match_checks_to_transactions(checks, transactions)

    assert matched_count == 1
    assert transactions[0]["payee"] == "Vendor Co"


def test_does_not_guess_when_amount_is_ambiguous():
    """Two checks for the same amount in the same statement is common
    (rent, recurring payments) -- guessing which is which would be worse
    than leaving both unmatched."""
    transactions = [_tx("Check (no number recorded)", amount=-200.0),
                     _tx("Check (no number recorded)", amount=-200.0)]
    checks = [{"check_number": None, "payee": "Landlord LLC", "numerical_amount": 200.0}]

    matched_count, unmatched = match_checks_to_transactions(checks, transactions)

    assert matched_count == 0
    assert unmatched == checks
    assert transactions[0]["payee"] == "Check (no number recorded)"
    assert transactions[1]["payee"] == "Check (no number recorded)"


def test_unmatched_check_is_reported_back_not_dropped_silently():
    transactions = [_tx("Check #1020", amount=-350.0)]
    checks = [{"check_number": "9999", "payee": "Nowhere Vendor", "numerical_amount": 999.0}]

    matched_count, unmatched = match_checks_to_transactions(checks, transactions)

    assert matched_count == 0
    assert unmatched == checks


def test_check_number_match_wins_over_amount_coincidence():
    transactions = [_tx("Check #1020", amount=-350.0), _tx("Check #1099", amount=-350.0)]
    checks = [{"check_number": "1020", "payee": "Correct Vendor", "numerical_amount": 350.0}]

    matched_count, unmatched = match_checks_to_transactions(checks, transactions)

    assert matched_count == 1
    assert transactions[0]["payee"] == "Correct Vendor"
    assert transactions[1]["payee"] == "Check #1099"
