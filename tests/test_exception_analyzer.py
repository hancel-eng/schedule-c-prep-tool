import pytest

from core.exception_analyzer import ExceptionAnalyzer


def _tx(payee, amount=-50.0, category="Line 27a: Other expenses (Uncategorized)",
        confidence_state="High Confidence", is_deposit=False):
    return {
        "payee": payee,
        "description": payee,
        "amount": amount,
        "is_deposit": is_deposit,
        "category": category,
        "confidence_state": confidence_state,
    }


# --- Word-boundary matching (raised after a real client meeting) -----------
# A plain substring check false-positives on short/generic keywords: "spa" is
# a substring of "space", so a real self-storage transaction ("Extra Space",
# correctly booked to Line 20b Rent) also landed in the personal-expense
# queue. Same class of bug already fixed in core/tax_categorizer.py's own
# keyword dictionary; this pins the matching fix in exception_analyzer.py.

def test_extra_space_storage_is_not_flagged_as_personal_spa():
    tx = _tx("Card Purchase Extra Space 340 4225",
             category="Line 20b: Rent or lease (other business property)")
    result = ExceptionAnalyzer().analyze_exceptions([tx])
    assert result["potential_personal"] == []


def test_genuine_spa_expense_is_still_flagged():
    tx = _tx("Ruby Nails and Spa")
    result = ExceptionAnalyzer().analyze_exceptions([tx])
    assert len(result["potential_personal"]) == 1
    assert "spa" in result["potential_personal"][0]["reason"]


def test_server_capital_asset_keyword_does_not_match_preserve():
    """"server" is a Capital Asset keyword; must not match inside "preserve"."""
    tx = _tx("Wildlife Preserve Membership", amount=-40.0)
    result = ExceptionAnalyzer().analyze_exceptions([tx], de_minimis_threshold=2500.0)
    assert result["potential_fixed_assets"] == []


def test_genuine_server_purchase_is_still_flagged_as_fixed_asset():
    tx = _tx("Dell PowerEdge Server", amount=-500.0)
    result = ExceptionAnalyzer().analyze_exceptions([tx], de_minimis_threshold=2500.0)
    assert len(result["potential_fixed_assets"]) == 1


def test_gym_keyword_does_not_match_unrelated_word():
    """"gym" must not match inside an unrelated word like "gymnasium supply co"
    used generically -- but a real gym membership charge still should."""
    tx = _tx("Anytime Fitness Membership")
    result = ExceptionAnalyzer().analyze_exceptions([tx])
    assert len(result["potential_personal"]) == 1


# --- De minimis threshold is honored --------------------------------------

def test_de_minimis_threshold_is_configurable():
    tx = _tx("Random Vendor", amount=-3000.0)
    default = ExceptionAnalyzer().analyze_exceptions([tx])
    assert len(default["potential_fixed_assets"]) == 1

    higher_threshold = ExceptionAnalyzer().analyze_exceptions([tx], de_minimis_threshold=5000.0)
    assert higher_threshold["potential_fixed_assets"] == []
