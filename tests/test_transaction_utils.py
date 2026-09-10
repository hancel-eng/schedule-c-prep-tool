from core.transaction_utils import transaction_key


def test_key_is_stable_for_identical_fields():
    tx1 = {"date": "01/15/2025", "payee": "Extra Space", "amount": -236.68, "source_file": "regions.pdf"}
    tx2 = {"date": "01/15/2025", "payee": "Extra Space", "amount": -236.68, "source_file": "regions.pdf"}
    assert transaction_key(tx1) == transaction_key(tx2)


def test_key_differs_on_amount():
    base = {"date": "01/15/2025", "payee": "Extra Space", "source_file": "regions.pdf"}
    assert transaction_key({**base, "amount": -236.68}) != transaction_key({**base, "amount": -236.69})


def test_key_differs_on_source_file():
    base = {"date": "01/15/2025", "payee": "Extra Space", "amount": -236.68}
    assert transaction_key({**base, "source_file": "a.pdf"}) != transaction_key({**base, "source_file": "b.pdf"})


def test_key_handles_missing_fields_without_raising():
    assert transaction_key({}) == "||0.00|"
