import pytest

from core.vendor_grouping import group_potential_personal, vendor_group_key


def _tx(payee, amount, date="01/01/2025", source_file="s.pdf"):
    return {"payee": payee, "date": date, "amount": amount, "source_file": source_file,
            "reason": f"Matched personal keyword 'cash app'. Verify business purpose."}


# --- vendor_group_key --------------------------------------------------------

def test_p2p_app_groups_by_service_and_recipient():
    assert vendor_group_key("Card Purchase Cash App*dakota 4829 Oakland CA") == "cash app|dakota"
    assert vendor_group_key("Card Purchase Cash App*moe 4829 Oakland CA") == "cash app|moe"


def test_different_recipients_produce_different_keys():
    assert vendor_group_key("Cash App*dakota 4829") != vendor_group_key("Cash App*moe 4829")


def test_regular_vendor_collapses_store_number_variants():
    k1 = vendor_group_key("Circle K # 0768 5499 North Port FL")
    k2 = vendor_group_key("Circle K # 0720 5499 Nokomis FL")
    assert k1 == k2


# --- group_potential_personal ------------------------------------------------

def test_the_real_engagement_case_109_items_collapse_to_7_groups():
    """The exact case from the client meeting: 109 Cash App line items across
    7 recipients, quantified from a real engagement."""
    items = []
    recipients = {
        "dakota": (50, 14656.00 / 50),
        "moe": (37, 12400.00 / 37),
        "joseph": (4, 2921.00 / 4),
        "adam": (5, 2050.00 / 5),
        "iesha": (3, 1750.00 / 3),
        "david": (2, 950.00 / 2),
        "powers": (8, 645.00 / 8),
    }
    for name, (count, avg) in recipients.items():
        for i in range(count):
            items.append(_tx(f"Card Purchase Cash App*{name} 4829 Oakland CA", -avg,
                             date=f"{(i % 28) + 1:02d}/01/2025"))

    groups = group_potential_personal(items)

    assert len(groups) == 7
    dakota_group = next(g for g in groups if "dakota" in g["group_key"])
    assert dakota_group["count"] == 50
    assert dakota_group["total_amount"] == pytest.approx(14656.00, abs=0.5)


def test_materiality_threshold_drops_small_groups_entirely():
    items = [_tx("Card Purchase Cash App*small 4829", -1.50)]
    groups = group_potential_personal(items, materiality_threshold=3.0)
    assert groups == []


def test_materiality_threshold_keeps_small_charges_that_add_up():
    """Ten $1 charges to the same vendor individually look trivial, but their
    $10 total is worth a question -- the threshold applies to the group, not
    each line."""
    items = [_tx("Card Purchase Cash App*small 4829", -1.00) for _ in range(10)]
    groups = group_potential_personal(items, materiality_threshold=3.0)
    assert len(groups) == 1
    assert groups[0]["total_amount"] == pytest.approx(10.00)
    assert groups[0]["count"] == 10


def test_group_carries_every_member_transaction_key():
    items = [
        _tx("Cash App*dakota 4829", -50.0, date="01/05/2025"),
        _tx("Cash App*dakota 4829", -75.0, date="01/10/2025"),
    ]
    groups = group_potential_personal(items)
    assert len(groups) == 1
    assert len(groups[0]["transaction_keys"]) == 2
    assert groups[0]["date_range"] == "01/05/2025 – 01/10/2025"


def test_single_transaction_group_has_a_plain_date_not_a_range():
    items = [_tx("Cash App*dakota 4829", -50.0, date="01/05/2025")]
    groups = group_potential_personal(items)
    assert groups[0]["date_range"] == "01/05/2025"
