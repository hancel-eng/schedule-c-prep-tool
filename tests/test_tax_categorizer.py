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
