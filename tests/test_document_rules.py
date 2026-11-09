"""Rules applied after the AI transcribes: money in/out, card payments vs
refunds, checks against the statement's own printed numbers, coverage.
Synthetic data only."""
from core import document_rules as rules
from core.tax_categorizer import TaxCategorizer


def _row(amount, direction, desc="Vendor", running=None, check_number=None, section=None, date="2025-03-04"):
    return {"date": date, "description": desc, "amount": amount, "direction": direction,
            "running_balance": running, "section_heading": section, "check_number": check_number,
            "category_as_written": None}


def _statement(rows, kind="bank_statement", opening=None, closing=None, totals=None, **extra):
    doc = {"document_kind": kind, "continues_from_previous_part": False, "issuer_name": "Test Bank",
           "account_holder": None, "account_last4": "9999", "period_start": "2025-03-01",
           "period_end": "2025-03-31", "opening_balance": opening, "closing_balance": closing,
           "summary_totals": totals or [], "transactions": rows, "check": None, "invoice_or_receipt": None,
           "unreadable_or_notes": [], "source_file": "s.pdf"}
    doc.update(extra)
    return doc


def _total(label, amount, effect):
    return {"label": label, "amount": amount, "effect": effect, "item_count": None}


def test_bank_statement_that_adds_up_is_reconciled_by_every_check():
    rows = [_row(500.0, "credit", running=1500.0), _row(200.0, "debit", running=1300.0)]
    result = rules.verify_statement(_statement(rows, opening=1000.0, closing=1300.0,
                                               totals=[_total("Deposits", 500.0, "credit"),
                                                       _total("Withdrawals", 200.0, "debit")]))
    assert result["status"] == "Reconciled"
    assert "row by row" in result["notes"]


def test_missing_row_is_a_discrepancy_with_a_plain_explanation():
    rows = [_row(500.0, "credit")]
    result = rules.verify_statement(_statement(rows, opening=1000.0, closing=1300.0,
                                               totals=[_total("Withdrawals", 200.0, "debit")]))
    assert result["status"] == "Discrepancy"
    assert "Off by $200.00" in result["notes"]
    assert "$200.00 missing" in result["notes"]


def test_running_balance_points_at_the_bad_row():
    rows = [_row(100.0, "debit", running=900.0), _row(50.0, "debit", running=800.0)]
    result = rules.verify_statement(_statement(rows, opening=1000.0))
    assert result["status"] == "Discrepancy"
    assert "Row 2" in result["notes"]


def test_summary_total_line_is_not_counted_twice_with_its_parts():
    rows = [_row(30.0, "debit"), _row(70.0, "debit")]
    totals = [_total("Checks paid", 30.0, "debit"), _total("Card purchases", 70.0, "debit"),
              _total("Total withdrawals", 100.0, "debit")]
    assert rules.verify_statement(_statement(rows, totals=totals))["status"] == "Reconciled"


def test_category_lines_without_a_total_are_summed():
    rows = [_row(30.0, "debit"), _row(70.0, "debit")]
    totals = [_total("Checks paid", 30.0, "debit"), _total("Electronic withdrawals", 70.0, "debit")]
    assert rules.verify_statement(_statement(rows, totals=totals))["status"] == "Reconciled"


def test_credit_card_balance_moves_the_other_way():
    rows = [_row(300.0, "debit", "OFFICE DEPOT"), _row(1000.0, "credit", "PAYMENT THANK YOU")]
    result = rules.verify_statement(_statement(rows, kind="credit_card_statement", opening=2000.0, closing=1300.0))
    assert result["status"] == "Reconciled"


def test_no_printed_numbers_is_not_a_pass():
    result = rules.verify_statement(_statement([_row(5.0, "debit")]))
    assert result["status"] == "Not Reconcilable"


def test_card_payment_is_not_income_and_card_refund_is_money_back():
    doc = _statement([_row(1000.0, "credit", "PAYMENT THANK YOU"),
                      _row(44.27, "credit", "Card Credit The Home Depot"),
                      _row(300.0, "debit", "OFFICE DEPOT")], kind="credit_card_statement")
    txs = rules.rows_to_transactions(doc, 0, TaxCategorizer())
    payment, refund, purchase = txs
    assert payment["category"] == "Non-P&L: Credit Card Payment" and not payment["is_deposit"]
    assert refund["is_deposit"] and refund["amount"] == 44.27
    assert not purchase["is_deposit"] and purchase["amount"] == -300.0


def test_bank_rows_get_signed_amounts_and_stable_ids():
    txs = rules.rows_to_transactions(_statement([_row(500.0, "credit"), _row(20.0, "debit")]), 3, TaxCategorizer())
    assert [t["amount"] for t in txs] == [500.0, -20.0]
    assert [t["is_deposit"] for t in txs] == [True, False]
    assert txs[1]["tx_id"] == "s.pdf#doc3#row1"


def test_check_row_without_the_word_check_gets_a_matchable_payee():
    txs = rules.rows_to_transactions(_statement([_row(75.0, "debit", "1043", check_number="1043")]), 0, TaxCategorizer())
    assert txs[0]["payee"] == "Check #1043"


def test_apply_rules_sorts_checks_receipts_and_statements():
    check = {"document_kind": "check", "check": {"payee": "Joe Plumbing", "drawer": "Acme", "issue_date": "2025-03-02",
                                                  "amount": 75.0, "amount_in_words": None, "check_number": "1043",
                                                  "memo": None}, "unreadable_or_notes": []}
    receipt = {"document_kind": "receipt", "invoice_or_receipt": {
        "issuer": "Store", "customer": None, "date": "2025-03-03", "document_number": "9",
        "subtotal": 10.0, "tax": 0.7, "total": 10.7,
        "line_items": [{"description": "Paper", "quantity": 1, "unit_price": 10.0, "line_total": 10.0}]},
        "unreadable_or_notes": []}
    other = {"document_kind": "other", "unreadable_or_notes": ["A blank cover page."]}
    result = rules.apply_rules("mix.pdf", [_statement([_row(75.0, "debit", "Check 1043")]), check, receipt, other],
                               TaxCategorizer())
    assert len(result["transactions"]) == 1
    assert result["check_records"][0]["payee"] == "Joe Plumbing"
    assert result["check_records"][0]["numerical_amount"] == 75.0
    assert result["supporting_documents"][0]["check_status"] == "Reconciled"
    assert "A blank cover page." in result["notes"][0]


def test_receipt_that_doesnt_add_up_is_flagged():
    status, note = rules.verify_invoice_or_receipt({"subtotal": 10.0, "tax": 1.0, "total": 12.0, "line_items": []})
    assert status == "Discrepancy" and "$11.00" in note


def test_coverage_counts_only_the_tax_year():
    txs = [{"date": "2024-12-20"}, {"date": "2025-01-05"}]
    months = rules.months_covered(txs, [("2025-01-01", "2025-02-28")], 2025)
    assert months == [1, 2]
    assert "Mar" in rules.check_12_month_coverage(months)["status_message"]
