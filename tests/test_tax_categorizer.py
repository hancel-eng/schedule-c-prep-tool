import pytest
from core.tax_categorizer import TaxCategorizer
from core.exception_analyzer import ExceptionAnalyzer

def test_tax_categorization_and_non_pnl():
    cat = TaxCategorizer()

    # Test advertising
    c, s, _ = cat.categorize_transaction("Google Ads", "Google Ads Marketing", -150.0, False)
    assert c == "Line 8: Advertising"
    assert s == "High Confidence"

    # Test Non-P&L transfer
    c_tr, s_tr, _ = cat.categorize_transaction("Online Transfer", "Transfer to Chase Savings", -500.0, False)
    assert c_tr.startswith("Non-P&L:")

    # Test gross receipts
    c_dep, s_dep, _ = cat.categorize_transaction("Client Payment", "Deposit from Client", 1200.0, True)
    assert c_dep == "Income: Gross Receipts"

def test_exception_analyzer_fixed_asset_and_personal():
    analyzer = ExceptionAnalyzer()
    
    txs = [
        {
            "date": "2026-03-15",
            "payee": "Apple Store",
            "description": "MacBook Pro Purchase",
            "amount": -3200.0, # > $2500 threshold
            "category": "Line 13: Depreciation and section 179",
            "confidence_state": "High Confidence",
            "is_deposit": False
        },
        {
            "date": "2026-05-10",
            "payee": "Trader Joe's",
            "description": "Grocery Personal",
            "amount": -85.50,
            "category": "Line 27a: Other expenses",
            "confidence_state": "High Confidence",
            "is_deposit": False
        }
    ]

    res = analyzer.analyze_exceptions(txs)
    assert len(res["potential_fixed_assets"]) == 1
    assert len(res["potential_personal"]) == 1


# --- Regression tests for Non-P&L detection -------------------------------
# Money movement wrongly booked to the P&L directly misstates net profit, so
# these use real statement wordings rather than the idealized strings the
# original keyword list was written against.

@pytest.mark.parametrize("description", [
    "CREDIT CARD AUTOMATIC PAYMENT",
    "CAPITAL ONE MOBILE PYMT",
    "PAYMENT - THANK YOU",
    "ELECTRONIC PAYMENT THANK YOU",
    "CHASE CREDIT CRD AUTOPAY",
    "CARD PYMT 1221",
])
def test_credit_card_payments_are_excluded_from_pnl(description):
    cat, state, _ = TaxCategorizer().categorize_transaction(
        payee=description, description=description, amount=-450.0, is_deposit=False
    )
    assert cat == "Non-P&L: Credit Card Payment"
    assert state == "High Confidence"


@pytest.mark.parametrize("description,expected", [
    ("ONLINE TRANSFER TO SAVINGS XXX44", "Non-P&L: Internal Transfer"),
    ("Transfer from Chase Business Checking", "Non-P&L: Internal Transfer"),
    ("OWNERS DRAW", "Non-P&L: Owner Draw / Contribution"),
    ("Distribution to Member", "Non-P&L: Owner Draw / Contribution"),
    ("SBA LOAN PROCEEDS", "Non-P&L: Loan Proceeds / Repayment"),
    ("IRS TREAS 310 TAX REFUND", "Non-P&L: Tax Refund / Reimbursement"),
])
def test_non_pnl_activity_is_excluded(description, expected):
    cat, _, _ = TaxCategorizer().categorize_transaction(
        payee=description, description=description, amount=-1000.0, is_deposit=False
    )
    assert cat == expected


@pytest.mark.parametrize("description,expected", [
    # A contractor payment is a real expense, not a card payment.
    ("Upwork Contractor Payment", "Line 11: Contract labor"),
    # A finance charge is real Line 16b interest, not a summary row.
    ("PURCHASE INTEREST CHARGE", "Line 16b: Other interest"),
    ("Chevron Gas Station", "Line 9: Car and truck expenses"),
])
def test_real_expenses_are_not_swallowed_by_non_pnl_patterns(description, expected):
    cat, _, _ = TaxCategorizer().categorize_transaction(
        payee=description, description=description, amount=-100.0, is_deposit=False
    )
    assert cat == expected


# --- Confidence states -----------------------------------------------------

def test_opaque_description_is_unresolved_not_guessed():
    _, state, score = TaxCategorizer().categorize_transaction(
        payee="POS DEBIT 4412", description="POS DEBIT 4412",
        amount=-88.0, is_deposit=False
    )
    assert state == "Unresolved"
    assert score == 0.0


def test_unmatched_but_readable_vendor_is_needs_review():
    _, state, _ = TaxCategorizer().categorize_transaction(
        payee="Bob Smith Hardware", description="Bob Smith Hardware",
        amount=-88.0, is_deposit=False
    )
    assert state == "Needs Review"


def test_all_three_confidence_states_are_reachable():
    c = TaxCategorizer()
    states = {
        c.categorize_transaction("Chevron", "Chevron", -50.0, False)[1],
        c.categorize_transaction("Bob Smith Hardware", "Bob Smith Hardware", -50.0, False)[1],
        c.categorize_transaction("POS DEBIT 4412", "POS DEBIT 4412", -50.0, False)[1],
    }
    assert states == {"High Confidence", "Needs Review", "Unresolved"}


# --- Sub-merchant "*" descriptors and apostrophe-free vendor names ---------

def test_asterisk_separated_merchant_descriptor_still_matches():
    """Card-network billing descriptors use "*" as a separator, not a space
    (a real statement reads "Google *Ads9829", not "Google Ads")."""
    cat, _, _ = TaxCategorizer().categorize_transaction(
        "Recurring Card Transaction Google *Ads 9829",
        "Recurring Card Transaction Google *Ads 9829",
        -500.0, False
    )
    assert cat == "Line 8: Advertising"


def test_apostrophe_free_vendor_name_still_matches_dictionary_entry():
    """A real bank statement prints "O Reilly" (space, no apostrophe), not
    "O'Reilly" -- the dictionary's own apostrophed spelling must still match."""
    cat, _, _ = TaxCategorizer().categorize_transaction(
        "PIN Purchase O Reilly 5307 North Port FL",
        "PIN Purchase O Reilly 5307 North Port FL",
        -25.0, False
    )
    assert cat == "Line 9: Car and truck expenses"


# --- Loan draws/repayments and abbreviated refund codes --------------------

def test_named_online_lender_loan_draw_excluded_from_gross_receipts():
    """Confirmed on a real statement: a $37,500 loan draw from
    "Headwaycapital" was counted as Gross Receipts."""
    cat, _, _ = TaxCategorizer().categorize_transaction(
        "Headwaycapital 2 Headway Dakota Gearhea 301006345", "",
        37500.0, True
    )
    assert cat == "Non-P&L: Loan Proceeds / Repayment"


def test_lender_repayment_descriptor_excluded_from_expenses():
    """The lender's own ACH repayment descriptor code, not a spelled-out
    company name -- confirmed on a real statement across 9 repayments."""
    cat, _, _ = TaxCategorizer().categorize_transaction(
        "Hwcrcvbls 23 Headway Dakota Gearhea 245977356", "",
        -3160.57, False
    )
    assert cat == "Non-P&L: Loan Proceeds / Repayment"


def test_irs_tax_refund_abbreviated_as_ref_is_excluded():
    """The standard ACH descriptor abbreviates "refund" to "REF" -- "IRS
    TREAS 310 TAX REF" is the literal code the IRS uses on every refund."""
    cat, _, _ = TaxCategorizer().categorize_transaction(
        "Irs Treas 310 Tax Ref Gearheart, Dak", "", 2205.0, True
    )
    assert cat == "Non-P&L: Tax Refund / Reimbursement"


def test_returned_deposit_item_is_not_gross_receipts():
    """A bounced check reversal is not new revenue."""
    cat, _, _ = TaxCategorizer().categorize_transaction(
        "Returned Deposit Item # of Itm(S) 0001", "", 2100.0, True
    )
    assert cat == "Non-P&L: Returned/Reversed Deposit"
