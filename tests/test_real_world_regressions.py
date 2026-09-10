"""Regression tests for bugs found running the pipeline against real client
statements (2026-09-09 real-data verification session). Every fixture below
is synthetic text built to reproduce the exact shape that broke extraction --
never the client's own PDFs, which must never be committed to this repo.
"""
import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from core.pdf_parser import BankPDFParser, FinancialDocumentData
from core.tax_categorizer import TaxCategorizer


def _pdf_from_lines(lines, font_size=9):
    """Builds a minimal one-page PDF with each string on its own line, close
    enough to real statement kerning to exercise the extraction path."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFont("Helvetica", font_size)
    y = 750
    for line in lines:
        c.drawString(40, y, line)
        y -= 14
    c.save()
    buffer.seek(0)
    return buffer


# --- P&L comparative-column parsing -----------------------------------------

def test_pnl_picks_the_requested_tax_year_column_not_the_last_number():
    """A comparative P&L prints two years side by side; grabbing whichever
    number is last on the line silently pulls in the wrong year's figures."""
    pdf = _pdf_from_lines([
        "Jan - Dec 25 Jan - Dec 24",
        "Income",
        "601 - Revenue 222,855.01 423,392.06",
        "Total Income 222,855.01 423,392.06",
        "Expense",
        "850 - Interest Expense 16,790.92 15,511.86",
    ])
    parser = BankPDFParser()
    data = parser._parse_pnl_report(pdf, "Client P&L.pdf", 2025)

    payees = {tx.payee: tx.amount for tx in data.transactions}
    assert payees["601 - Revenue"] == pytest.approx(222855.01)
    assert payees["850 - Interest Expense"] == pytest.approx(-16790.92)
    # Total Income restates Revenue -- must not appear as a second income line.
    assert "Total Income" not in payees


def test_pnl_never_flips_a_loss_positive():
    pdf = _pdf_from_lines([
        "Jan - Dec 25 Jan - Dec 24",
        "Other Income",
        "617 - Gain (Loss) on Sale of Asset -91,095.66 -118,235.38",
    ])
    parser = BankPDFParser()
    data = parser._parse_pnl_report(pdf, "Client P&L.pdf", 2025)
    assert len(data.transactions) == 1
    assert data.transactions[0].amount == pytest.approx(-91095.66)


def test_pnl_falls_back_to_first_column_when_year_absent_and_warns():
    pdf = _pdf_from_lines([
        "Jan - Dec 23 Jan - Dec 22",
        "Income",
        "601 - Revenue 100,000.00 90,000.00",
    ])
    parser = BankPDFParser()
    data = parser._parse_pnl_report(pdf, "Client P&L.pdf", 2025)
    assert data.transactions[0].amount == pytest.approx(100000.00)
    assert any("WARNING" in note for note in data.diagnostics_notes)


# --- Statement filename -> closing month/year (year-rollover) --------------

def test_statement_end_month_from_filename():
    parser = BankPDFParser()
    assert parser._extract_statement_end_month_from_filename(
        "Regions 6255 01 23 2025.pdf") == 1
    assert parser._extract_statement_end_month_from_filename(
        "Capital One Biz Spark 1221 06 24 2025 - card# changed 4464.pdf") == 6
    assert parser._extract_statement_end_month_from_filename(
        "TD Tree 2025 P & L.pdf") is None


def test_december_transaction_in_a_january_statement_gets_prior_year():
    """A billing cycle closing in January routinely opens in December of the
    prior year; every transaction date on the statement omits the year."""
    parser = BankPDFParser()
    assert parser._resolve_transaction_year("Dec 23", statement_end_month=1,
                                            statement_year=2025) == 2024
    assert parser._resolve_transaction_year("Jan 19", statement_end_month=1,
                                            statement_year=2025) == 2025
    assert parser._resolve_transaction_year("12/23", statement_end_month=1,
                                            statement_year=2025) == 2024


# --- Merged-text recovery ----------------------------------------------------

def test_normalize_spacing_recovers_zero_gap_merges():
    """Some banks' own PDFs have no space at all between fragments (confirmed
    at the character-coordinate level on a real Capital One statement)."""
    parser = BankPDFParser()
    assert parser._normalize_spacing("CAPITAL ONE MOBILE PYMTAuthDate 19-Jan") == \
        "CAPITAL ONE MOBILE PYMT Auth Date 19-Jan"
    assert parser._normalize_spacing("Card PurchaseCircle K 5200North Port") == \
        "Card Purchase Circle K 5200 North Port"


def test_merged_credit_card_payment_is_still_excluded_from_pnl():
    """Confirms the normalization fix actually closes the categorization gap:
    without it, this exact real-world string matched the "mobil" (gas brand)
    keyword instead of being excluded as a card payment."""
    categorizer = TaxCategorizer()
    text = "CAPITAL ONE MOBILE PYMT Auth Date 19-Jan -"
    cat, state, _ = categorizer.categorize_transaction(text, text, -2250.0, False)
    assert cat == "Non-P&L: Credit Card Payment"


# --- Vendor credits shouldn't inflate gross receipts ------------------------

def test_card_credit_refund_excluded_from_gross_receipts():
    """A vendor purchase credit is its own category, distinct from a genuine
    tax refund -- lumping them together made 15 of 16 "Tax Refund" items on a
    real engagement actually be Home Depot/O'Reilly/Amazon credits."""
    categorizer = TaxCategorizer()
    cat, _, _ = categorizer.categorize_transaction(
        "Card Credit The Home Depot", "Card Credit The Home Depot", 44.27, True
    )
    assert cat == "Non-P&L: Vendor Purchase Credit"


# --- Word-boundary keyword matching -----------------------------------------

def test_mobile_word_does_not_match_mobil_gas_keyword():
    """"mobil" (Mobil gas stations, a Line 9 keyword) must not match as a
    substring of the unrelated word "mobile"."""
    categorizer = TaxCategorizer()
    cat, _, _ = categorizer.categorize_transaction(
        "Some Mobile App Subscription", "Some Mobile App Subscription", -9.99, False
    )
    assert cat != "Line 9: Car and truck expenses"


# --- Daily Balance Summary must never be read as transactions --------------

def test_daily_balance_summary_rows_are_never_read_as_transactions():
    """A repeating "date balance date balance date balance" table has exactly
    the shape ["date", "description-looking text", "amount"] the universal
    transaction regex looks for. Confirmed on a real statement: one such row
    was read as a single $27,647.88 phantom withdrawal."""
    pdf = _pdf_from_lines([
        "DAILY BALANCE SUMMARY",
        "Date Balance Date Balance Date Balance",
        "12/23 19,884.77 01/03 27,556.38 01/14 15,547.28",
        "12/24 20,884.77 01/06 28,556.38",
    ])
    parser = BankPDFParser()
    data = parser._parse_general_or_scanned_pdf(pdf, "Bank Statement 01 23 2025.pdf", 2025, 1)
    assert data.transactions == []


# --- Bare "WITHDRAWALS" header (no "& Debits" suffix) -----------------------

def test_bare_withdrawals_header_is_recognized():
    """The original section-header match required "WITHDRAWALS & DEBITS" or
    "WITHDRAWALS AND DEBITS" -- text a real statement never actually prints
    (it prints the bare word "WITHDRAWALS"), so every withdrawal fell through
    to the DEPOSIT default and was booked as income."""
    pdf = _pdf_from_lines([
        "DEPOSITS & CREDITS",
        "01/03 Deposit - Thank You 1,000.00",
        "WITHDRAWALS",
        "01/05 Card Purchase Some Vendor FL 42.50",
    ])
    parser = BankPDFParser()
    data = parser._parse_general_or_scanned_pdf(pdf, "Bank Statement 01 23 2025.pdf", 2025, 1)
    by_payee = {tx.payee: tx for tx in data.transactions}
    assert by_payee["Deposit - Thank You"].is_deposit is True
    assert by_payee["Card Purchase Some Vendor FL"].is_deposit is False


# --- Two-checks-per-line cleared-checks listing -----------------------------

def test_two_checks_per_line_are_both_captured():
    """Regions prints cleared checks as two "Date CheckNo Amount" pairs per
    line, not "check# date amount" -- the original pattern's assumed order and
    single-entry-per-line shape meant this whole listing extracted nothing."""
    pdf = _pdf_from_lines([
        "CHECKS",
        "Date Check No. Amount Date Check No. Amount",
        "12/23 1234 580.00 01/15 1238 1,045.25",
        "01/06 1235 630.00",
        "Total Checks $2,255.25",
    ])
    parser = BankPDFParser()
    data = parser._parse_general_or_scanned_pdf(pdf, "Bank Statement 01 23 2025.pdf", 2025, 1)
    checks = {tx.payee: tx.amount for tx in data.transactions}
    assert checks["Check #1234"] == pytest.approx(-580.00)
    assert checks["Check #1238"] == pytest.approx(-1045.25)
    assert checks["Check #1235"] == pytest.approx(-630.00)


# --- Interest charge with no per-line date ----------------------------------

def test_interest_charge_with_no_date_is_still_captured():
    """"Interest Charge on Purchases $95.58" carries no date of its own, so it
    never matched the dated-transaction pattern and was dropped entirely --
    real Line 16b interest expense silently missing from every card statement."""
    pdf = _pdf_from_lines([
        "Jan 19 Jan 20 CAPITAL ONE MOBILE PYMT AuthDate 19-Jan - $2,250.00",
        "Interest Charge on Purchases $95.58",
    ])
    parser = BankPDFParser()
    data = parser._parse_credit_card_pdf(pdf, "Capital One Spark 01 25 2025.pdf", 2025, 1)
    interest = [tx for tx in data.transactions if "Interest" in tx.payee]
    assert len(interest) == 1
    assert interest[0].amount == pytest.approx(-95.58)
    assert interest[0].category == "Line 16b: Other interest"


# --- Balance capture ordering (must run before section-header shortcuts) ---

def test_total_deposits_captured_even_when_line_matches_section_header_text():
    """"Total Deposits & Credits $37,638.76" contains the same "deposits &
    credits" substring the section-header check uses, so capturing balances
    only *after* that check meant this exact line's dollar figure was never
    reachable."""
    parser = BankPDFParser()
    data = FinancialDocumentData()
    parser._capture_balances("Total Deposits & Credits $37,638.76", data)
    assert data.total_deposits == pytest.approx(37638.76)


def test_fees_and_checks_summary_lines_are_captured_though_not_flagged_as_summary():
    parser = BankPDFParser()
    data = FinancialDocumentData()
    parser._capture_balances("Fees $8.00 -", data)
    parser._capture_balances("Checks $5,130.19 -", data)
    assert data.total_fees == pytest.approx(8.00)
    assert data.total_checks == pytest.approx(5130.19)
