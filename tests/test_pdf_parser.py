import pytest
from core.pdf_parser import (
    BankPDFParser,
    FinancialDocumentData,
    CreditCardStatementData,
    BankStatementData,
)


def test_credit_card_parser_structure():
    """Credit card strategy returns a typed document with the expected metadata."""
    parser = BankPDFParser()
    data = parser._parse_credit_card_pdf(None, "Capital One Biz Spark 1221 01 25 2025.pdf", 2025)

    assert isinstance(data, CreditCardStatementData)
    assert data.statement_type == "credit_card"
    assert data.bank_name == "Capital One Business Spark"


def test_bank_statement_parser_structure():
    """General/bank strategy returns a typed document with the expected metadata."""
    parser = BankPDFParser()
    data = parser._parse_general_or_scanned_pdf(None, "Regions 6255 01 23 2025.pdf", 2025)

    assert isinstance(data, BankStatementData)
    assert data.statement_type == "bank_statement"
    assert data.bank_name == "LifeGreen / Regions Business Checking"


def test_parse_pdf_routes_credit_card_by_filename():
    """Filename routing picks the credit card strategy for card statements."""
    parser = BankPDFParser()
    res = parser.parse_pdf(None, "Capital One Spark 1221 01 25 2025.pdf")

    assert res["filename"] == "Capital One Spark 1221 01 25 2025.pdf"
    assert res["statement_summary"]["document_type"] == "credit_card"


def test_parse_pdf_routes_pnl_by_filename():
    """Filename routing picks the P&L strategy for profit-and-loss reports."""
    parser = BankPDFParser()
    res = parser.parse_pdf(None, "TD Tree 2025 P & L.pdf")

    assert res["statement_summary"]["document_type"] == "pnl_report"


def test_extract_year_from_filename():
    parser = BankPDFParser()
    assert parser._extract_year_from_filename("Regions 6255 01 23 2024.pdf") == 2024
    assert parser._extract_year_from_filename("statement.pdf") == 2025


def test_clean_amount_handles_negatives_and_formatting():
    parser = BankPDFParser()
    assert parser._clean_amount("$1,234.56") == 1234.56
    assert parser._clean_amount("(89.10)") == -89.10
    assert parser._clean_amount("-45.20") == -45.20
    assert parser._clean_amount("not a number") == 0.0


def test_check_12_month_coverage_reports_gaps():
    parser = BankPDFParser()

    complete = parser.check_12_month_coverage(list(range(1, 13)))
    assert complete["is_complete"] is True
    assert complete["missing_months"] == []

    partial = parser.check_12_month_coverage([1, 2, 3])
    assert partial["is_complete"] is False
    assert partial["present_months_count"] == 3
    assert partial["missing_months"] == [4, 5, 6, 7, 8, 9, 10, 11, 12]
