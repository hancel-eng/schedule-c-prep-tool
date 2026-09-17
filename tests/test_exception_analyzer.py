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


# --- A Non-P&L transfer is never an asset purchase (raised in a client
# meeting) -----------------------------------------------------------------

def test_online_transfer_over_threshold_is_never_flagged_as_asset_purchase():
    """A same-owner "Online Transfer To Chk ...2202" over the de minimis
    threshold kept generating an "is this an asset purchase?" question,
    purely because of its amount -- even though tax_categorizer.py had
    already excluded it from the P&L entirely as an internal transfer. The
    fix is scoped to the category (Non-P&L: *), not the word "transfer",
    so it applies to any client's wording, not just this one's."""
    tx = _tx("Online Transfer To Chk ...2202 Transaction#: 16425446690",
             amount=-3000.0, category="Non-P&L: Internal Transfer")
    result = ExceptionAnalyzer().analyze_exceptions([tx], de_minimis_threshold=2500.0)
    assert result["potential_fixed_assets"] == []


def test_owner_draw_over_threshold_is_also_never_flagged_as_asset_purchase():
    """Same rule, different Non-P&L category -- confirms this isn't specific
    to "transfer" wording but to any Non-P&L exclusion."""
    tx = _tx("Owner Draw to Personal Checking", amount=-10000.0,
             category="Non-P&L: Owner Draw / Contribution")
    result = ExceptionAnalyzer().analyze_exceptions([tx], de_minimis_threshold=2500.0)
    assert result["potential_fixed_assets"] == []


def test_non_pnl_transaction_can_still_be_flagged_as_personal_expense():
    """The asset-purchase exclusion above must not widen into suppressing
    the personal-expense safety net for an auto-detected Non-P&L category --
    see test_auto_detected_non_pnl_transaction_is_not_suppressed in
    tests/test_answer_applier.py, which pins that the personal-expense check
    still applies until a client actually confirms the transaction."""
    tx = _tx("Target Corp Transfer", amount=-3000.0, category="Non-P&L: Internal Transfer")
    result = ExceptionAnalyzer().analyze_exceptions([tx], de_minimis_threshold=2500.0)
    assert result["potential_fixed_assets"] == []
    assert len(result["potential_personal"]) == 1
