"""Hand corrections and client answers live side by side: the workpaper is
always rebuilt from the AI's documents + hand corrections + answers.
Synthetic data only."""
import copy

from core import workpaper
from core.answer_applier import uncategorized_expense_answer_options
from core.tax_categorizer import TaxCategorizer
from core.transaction_utils import transaction_key


def _row(amount, direction, desc, date="2025-03-05", running=None):
    return {"date": date, "description": desc, "amount": amount, "direction": direction,
            "running_balance": running, "section_heading": None, "check_number": None,
            "category_as_written": None}


def _files(rows):
    doc = {"document_kind": "bank_statement", "continues_from_previous_part": False, "issuer_name": "Test Bank",
           "account_holder": None, "account_last4": "1111", "period_start": "2025-03-01",
           "period_end": "2025-03-31", "opening_balance": 1000.0, "closing_balance": 1450.0,
           "summary_totals": [{"label": "Deposits", "amount": 600.0, "effect": "credit", "item_count": None},
                              {"label": "Withdrawals", "amount": 150.0, "effect": "debit", "item_count": None}],
           "transactions": rows, "check": None, "invoice_or_receipt": None, "what_it_is": None,
           "could_not_read": [], "source_file": "march.pdf", "parts": ["pages 1-2 of 2"]}
    read = {"march.pdf": {"documents": [doc], "error": None, "usage": {}, "part_count": 1}}
    return workpaper.ingest(read)


def _rows_as_typed(doc):
    return [{k: r.get(k) for k in ("row_id",) + workpaper.EDITABLE_ROW_FIELDS} for r in doc["transactions"]]


def _save(files, edits, rows, doc=None, **overrides):
    doc = doc or files["march.pdf"]["documents"][0]
    args = dict(rows=rows, summary_totals=doc["summary_totals"],
                opening_balance=doc["opening_balance"], closing_balance=doc["closing_balance"])
    args.update(overrides)
    return workpaper.save_corrections(files, edits, "march.pdf", 0, **args)


def _status(files, answers=()):
    return workpaper.rebuild(files, list(answers), 2025, TaxCategorizer())["reconciliation_results"][0]["status"]


def test_adding_the_missing_row_makes_the_statement_add_up():
    files = _files([_row(600.0, "credit", "Client deposit"), _row(100.0, "debit", "Zzqx Unknown Vendor")])
    assert _status(files) == "Discrepancy"
    edits = []
    rows = _rows_as_typed(files["march.pdf"]["documents"][0])
    rows.append({"row_id": None, "date": "2025-03-20", "description": "Bank fee", "amount": 50.0,
                 "direction": "debit", "running_balance": None})
    assert _save(files, edits, rows) == 1
    assert _status(files) == "Reconciled"
    added = files["march.pdf"]["documents"][0]["transactions"][-1]
    assert added["row_id"] == "r3" and added["added_by_hand"]
    assert edits[0]["change"] == "Added row r3"


def test_a_client_answer_survives_a_hand_correction():
    files = _files([_row(600.0, "credit", "Client deposit"), _row(100.0, "debit", "Zzqx Unknown Vendor")])
    built = workpaper.rebuild(files, [], 2025, TaxCategorizer())
    vendor_tx = next(t for t in built["all_transactions"] if "Zzqx" in t["payee"])
    target = uncategorized_expense_answer_options()[1]
    answer = {"category": "Uncategorized Expense", "answer": target, "item_id": "q1", "payee": vendor_tx["payee"],
              "date": vendor_tx["date"], "amount": vendor_tx["amount"],
              "transaction_keys": [transaction_key(vendor_tx)]}
    rows = _rows_as_typed(files["march.pdf"]["documents"][0])
    rows.append({"row_id": None, "date": "2025-03-20", "description": "Bank fee", "amount": 50.0,
                 "direction": "debit", "running_balance": None})
    _save(files, [], rows)
    rebuilt = workpaper.rebuild(files, [answer], 2025, TaxCategorizer())
    vendor_after = next(t for t in rebuilt["all_transactions"] if "Zzqx" in t["payee"])
    assert vendor_after["category"] == target
    assert rebuilt["correction_log"][0]["applied"]


def test_editing_an_amount_and_deleting_a_row_are_logged():
    files = _files([_row(600.0, "credit", "Client deposit"), _row(100.0, "debit", "Vendor"),
                    _row(999.0, "debit", "Misread row")])
    edits = []
    rows = _rows_as_typed(files["march.pdf"]["documents"][0])[:2]
    rows[1]["amount"] = 150.0
    _save(files, edits, rows)
    changes = [e["change"] for e in edits]
    assert "Row r2 amount" in changes and "Deleted row r3" in changes
    assert _status(files) == "Reconciled"


def test_editing_printed_numbers_is_logged():
    files = _files([_row(600.0, "credit", "Deposit"), _row(150.0, "debit", "Vendor")])
    edits = []
    doc = files["march.pdf"]["documents"][0]
    totals = copy.deepcopy(doc["summary_totals"])
    totals[1]["amount"] = 151.0
    _save(files, edits, _rows_as_typed(doc), summary_totals=totals, closing_balance="1449.00")
    assert {e["change"] for e in edits} == {"Closing balance", "Summary totals"}
    assert _status(files) == "Discrepancy"


def test_saving_without_changes_logs_nothing():
    files = _files([_row(600.0, "credit", "Deposit"), _row(150.0, "debit", "Vendor")])
    edits = []
    assert _save(files, edits, _rows_as_typed(files["march.pdf"]["documents"][0])) == 0
    assert edits == [] and not files["march.pdf"]["documents"][0].get("corrected_by_hand")


def test_undo_puts_back_what_the_ai_read():
    files = _files([_row(600.0, "credit", "Deposit"), _row(150.0, "debit", "Vendor")])
    edits = []
    _save(files, edits, [])
    assert files["march.pdf"]["documents"][0]["transactions"] == []
    workpaper.undo_corrections(files, edits, "march.pdf", 0)
    assert len(files["march.pdf"]["documents"][0]["transactions"]) == 2
    assert _status(files) == "Reconciled"


def test_checked_by_hand_counts_as_ok_and_is_cleared_by_a_new_edit():
    files = _files([_row(600.0, "credit", "Deposit")])
    edits = []
    workpaper.mark_checked_by_hand(files, edits, "march.pdf", 0, "Bank confirmed by phone")
    built = workpaper.rebuild(files, [], 2025, TaxCategorizer())
    assert built["reconciliation_results"][0]["status"] == "Checked by Hand"
    assert "Bank confirmed by phone" in built["reconciliation_results"][0]["notes"]
    assert built["reconciliation_summary"]["status"] == "Reconciled"
    rows = _rows_as_typed(files["march.pdf"]["documents"][0])
    rows[0]["amount"] = 601.0
    _save(files, edits, rows)
    assert _status(files) == "Discrepancy"


def test_failed_file_is_kept_with_its_error_and_counts_nothing():
    read = {"bad.pdf": {"documents": [], "error": {"code": "OUT_OF_CREDIT", "message": "x", "fix": "y"},
                        "usage": {}, "part_count": 2}}
    built = workpaper.rebuild(workpaper.ingest(read), [], 2025, TaxCategorizer())
    assert built["all_transactions"] == []
    assert built["diagnostics_log"][0]["ai_error"]["code"] == "OUT_OF_CREDIT"


def test_row_balance_marks_point_at_the_break():
    from core.document_rules import row_balance_marks
    doc = _files([_row(600.0, "credit", "Deposit", running=1600.0),
                  _row(100.0, "debit", "Vendor", running=1450.0)])["march.pdf"]["documents"][0]
    marks = row_balance_marks(doc)
    assert [m["matches"] for m in marks] == [True, False]
    assert marks[1]["expected_balance"] == 1500.0


def test_manual_corrections_sheet_lists_every_hand_fix():
    import openpyxl
    from core.excel_exporter import ExcelWorkpaperExporter
    edits = [{"when": "2026-11-09 17:25 UTC", "file": "march.pdf", "document": "Test Bank statement",
              "change": "Closing balance", "before": 2000.0, "after": 2120.0}]
    buffer = ExcelWorkpaperExporter().generate_workpaper(
        client_name="Test", tax_year=2025, transactions=[], exceptions_dict={}, questions=[],
        coverage_info={"is_complete": True, "status_message": "ok", "missing_months": [], "present_months_count": 12},
        duplicates_info=[], reconciliation_summary={"status": "Reconciled", "message": "ok", "results": []},
        correction_log=[], manual_edits=edits)
    sheet = openpyxl.load_workbook(buffer)["Manual Corrections"]
    assert [c.value for c in sheet[5]] == ["2026-11-09 17:25 UTC", "march.pdf", "Test Bank statement",
                                           "Closing balance", 2000.0, 2120.0]
