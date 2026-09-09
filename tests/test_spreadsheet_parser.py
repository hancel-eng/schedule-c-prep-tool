import io

import pandas as pd
import pytest

from core.spreadsheet_parser import SpreadsheetParser


def _as_xlsx(rows):
    buffer = io.BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer


def _by_payee(transactions):
    return {tx["payee"]: tx for tx in transactions}


def test_inverted_sheet_books_revenue_as_income_not_expense():
    """A disbursements register records expenses positive and income negative.

    Read as the standard convention, revenue lands in expenses and net profit
    moves by twice the amount, so this is the highest-value case to pin down.
    """
    rows = [
        {"Date": "2026-01-10", "Payee": "Staples Office Supplies", "Amount": 145.20, "Category": "Office Expenses"},
        {"Date": "2026-01-15", "Payee": "Google Ads", "Amount": 300.00, "Category": "Marketing"},
        {"Date": "2026-03-01", "Payee": "Client Invoice Payment", "Amount": -4500.00, "Category": "Revenue Deposit"},
    ]

    result = SpreadsheetParser().parse_spreadsheet(_as_xlsx(rows), "inverted.xlsx")
    txs = _by_payee(result["transactions"])

    assert result["diagnostics"][0]["sign_convention"] == "inverted"

    assert txs["Client Invoice Payment"]["amount"] == 4500.00
    assert txs["Client Invoice Payment"]["is_deposit"] is True

    assert txs["Staples Office Supplies"]["amount"] == -145.20
    assert txs["Staples Office Supplies"]["is_deposit"] is False


def test_standard_sheet_is_read_as_is():
    rows = [
        {"Date": "2026-01-10", "Payee": "Staples", "Amount": -145.20, "Category": "Office"},
        {"Date": "2026-03-01", "Payee": "Client Deposit", "Amount": 4500.00, "Category": "Revenue"},
    ]

    result = SpreadsheetParser().parse_spreadsheet(_as_xlsx(rows), "standard.xlsx")
    txs = _by_payee(result["transactions"])

    assert result["diagnostics"][0]["sign_convention"] == "standard"
    assert txs["Client Deposit"]["amount"] == 4500.00
    assert txs["Staples"]["amount"] == -145.20


def test_unsigned_sheet_uses_row_wording_for_direction():
    """Every value positive: direction is carried only by the row's own labels."""
    rows = [
        {"Date": "2026-01-10", "Payee": "Staples", "Amount": 145.20, "Category": "Office"},
        {"Date": "2026-03-01", "Payee": "Customer Payment", "Amount": 4500.00, "Category": "Sales Income"},
    ]

    result = SpreadsheetParser().parse_spreadsheet(_as_xlsx(rows), "unsigned.xlsx")
    txs = _by_payee(result["transactions"])

    assert result["diagnostics"][0]["sign_convention"] == "unsigned"
    assert txs["Customer Payment"]["amount"] == 4500.00
    assert txs["Customer Payment"]["is_deposit"] is True
    assert txs["Staples"]["amount"] == -145.20
    assert txs["Staples"]["is_deposit"] is False


def test_separate_debit_and_credit_columns():
    rows = [
        {"Date": "2026-01-10", "Description": "Staples", "Debit": 145.20, "Credit": None},
        {"Date": "2026-03-01", "Description": "Client Deposit", "Debit": None, "Credit": 4500.00},
    ]

    result = SpreadsheetParser().parse_spreadsheet(_as_xlsx(rows), "debit_credit.xlsx")
    txs = _by_payee(result["transactions"])

    assert txs["Staples"]["amount"] == -145.20
    assert txs["Client Deposit"]["amount"] == 4500.00


def test_original_client_category_is_preserved():
    rows = [{"Date": "2026-01-10", "Payee": "Staples", "Amount": -145.20,
             "Category": "Bob's Office Junk"}]

    result = SpreadsheetParser().parse_spreadsheet(_as_xlsx(rows), "orig.xlsx")

    assert result["transactions"][0]["original_category"] == "Bob's Office Junk"
    assert result["transactions"][0]["category"] == "Line 18: Office expense"


def test_expenses_are_negative_matching_the_pdf_parser_convention():
    """Spreadsheet and PDF transactions land in the same audit trail."""
    rows = [{"Date": "2026-01-10", "Payee": "Staples", "Amount": 145.20, "Category": "Office"}]

    result = SpreadsheetParser().parse_spreadsheet(_as_xlsx(rows), "convention.xlsx")

    assert result["transactions"][0]["amount"] < 0
