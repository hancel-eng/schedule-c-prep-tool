"""The AI reader: splitting files into parts, re-splitting a cut-off part,
stitching parts back together, and reading many files at once. The AI
itself is replaced by a fake; no network calls."""
import io

import pytest
from reportlab.pdfgen import canvas

from core import ai_reader
from core.ai_errors import AIExtractionError, answer_cut_off
from core.ai_reader import merge_parts, plan_parts, read_files, read_part


def _pdf(pages: int) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    for page in range(pages):
        c.drawString(72, 720, f"Synthetic page {page + 1}")
        c.showPage()
    c.save()
    return buffer.getvalue()


def _doc(kind="bank_statement", rows=None, cont=False, **fields):
    base = {
        "document_kind": kind, "continues_from_previous_part": cont, "issuer_name": None,
        "account_holder": None, "account_last4": None, "period_start": None, "period_end": None,
        "opening_balance": None, "closing_balance": None, "summary_totals": [],
        "transactions": rows or [], "check": None, "invoice_or_receipt": None, "unreadable_or_notes": [],
    }
    base.update(fields)
    return base


def _row(amount, direction="debit", desc="Row"):
    return {"date": "2025-01-05", "description": desc, "amount": amount, "direction": direction,
            "running_balance": None, "section_heading": None, "check_number": None, "category_as_written": None}


# --- planning ---------------------------------------------------------------

def test_pdf_is_split_into_parts_of_four_pages():
    source = plan_parts(_pdf(10), "statement.pdf")
    assert source.page_count == 10
    assert [p.pdf_pages for p in source.parts] == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]
    assert source.parts[2].label == "pages 9-10 of 10"


def test_image_is_one_part():
    source = plan_parts(b"\x89PNG fake", "receipt.PNG")
    assert len(source.parts) == 1 and source.parts[0].kind == "image"
    assert source.parts[0].media_type == "image/png"


def test_csv_is_split_by_lines_and_later_parts_carry_header_context():
    rows = ["Date,Description,Amount"] + [f"2025-01-{(i % 28) + 1:02d},Vendor {i},-{i}.00" for i in range(250)]
    source = plan_parts("\n".join(rows).encode(), "ledger.csv")
    assert len(source.parts) == 3
    assert source.parts[0].context_lines == []
    assert "Date | Description | Amount" in source.parts[1].context_lines[0]
    assert sum(len(p.lines) for p in source.parts) == 251


def test_xlsx_sheets_become_separate_sections():
    import pandas as pd
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer) as writer:
        pd.DataFrame({"Date": ["2025-01-01"], "Amount": [5]}).to_excel(writer, sheet_name="Jan", index=False)
        pd.DataFrame({"Date": ["2025-02-01"], "Amount": [7]}).to_excel(writer, sheet_name="Feb", index=False)
    source = plan_parts(buffer.getvalue(), "book.xlsx")
    assert [p.label.split(":")[0] for p in source.parts] == ["Jan", "Feb"]


def test_docx_text_and_tables_are_read():
    import docx
    document = docx.Document()
    document.add_paragraph("Expense report")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Office Depot"
    table.rows[0].cells[1].text = "45.10"
    buffer = io.BytesIO()
    document.save(buffer)
    source = plan_parts(buffer.getvalue(), "report.docx")
    assert source.parts[0].lines == ["Expense report", "Office Depot | 45.10"]


@pytest.mark.parametrize("data, name, code", [
    (b"", "a.pdf", "EMPTY_FILE"),
    (b"not a pdf at all", "a.pdf", "FILE_DAMAGED"),
    (b"x", "photo.heic", "UNSUPPORTED_FILE"),
    (b"x", "old.doc", "UNSUPPORTED_FILE"),
    (b"x", "data.zip", "UNSUPPORTED_FILE"),
])
def test_unreadable_files_get_clear_codes(data, name, code):
    with pytest.raises(AIExtractionError) as info:
        plan_parts(data, name)
    assert info.value.code == code


# --- cut-off answers are split and retried, never used ----------------------

def test_cut_off_part_is_split_until_answers_fit():
    source = plan_parts(_pdf(4), "statement.pdf")
    calls = []

    def fake_ai(src, part, year):
        calls.append(part.pdf_pages)
        if len(part.pdf_pages) > 1:
            raise answer_cut_off("status=incomplete")
        return {"documents": [_doc(rows=[_row(1.0, desc=f"page {part.pdf_pages[0] + 1}")])]}

    payloads = read_part(source, source.parts[0], 2025, call_ai=fake_ai)
    assert [p["documents"][0]["transactions"][0]["description"] for p in payloads] == \
        ["page 1", "page 2", "page 3", "page 4"]
    assert calls[0] == [0, 1, 2, 3]


def test_single_page_still_cut_off_is_reported_not_used():
    source = plan_parts(_pdf(1), "statement.pdf")

    def fake_ai(src, part, year):
        raise answer_cut_off("status=incomplete")

    with pytest.raises(AIExtractionError) as info:
        read_part(source, source.parts[0], 2025, call_ai=fake_ai)
    assert info.value.code == "ANSWER_CUT_OFF"


# --- merging parts back into documents --------------------------------------

def test_statement_spread_over_parts_becomes_one_document():
    payloads = [
        {"_label": "pages 1-4", "documents": [_doc(rows=[_row(10.0)], opening_balance=100.0, closing_balance=50.0,
                                                    account_last4="1234",
                                                    summary_totals=[{"label": "Withdrawals", "amount": 50.0,
                                                                     "effect": "debit", "item_count": None}])]},
        {"_label": "pages 5-8", "documents": [_doc(rows=[_row(40.0)], cont=True, account_last4="1234",
                                                    summary_totals=[{"label": "Withdrawals", "amount": 50.0,
                                                                     "effect": "debit", "item_count": None}])]},
    ]
    docs = merge_parts("s.pdf", payloads)
    assert len(docs) == 1
    assert [r["amount"] for r in docs[0]["transactions"]] == [10.0, 40.0]
    assert len(docs[0]["summary_totals"]) == 1
    assert docs[0]["parts"] == ["pages 1-4", "pages 5-8"]


def test_different_accounts_are_not_merged():
    payloads = [
        {"_label": "p1", "documents": [_doc(account_last4="1111", opening_balance=1.0)]},
        {"_label": "p2", "documents": [_doc(account_last4="2222", cont=True)]},
    ]
    assert len(merge_parts("s.pdf", payloads)) == 2


def test_new_statement_starting_in_a_later_part_is_kept_separate():
    payloads = [
        {"_label": "p1", "documents": [_doc(opening_balance=1.0, period_start="2025-01-01")]},
        {"_label": "p2", "documents": [_doc(opening_balance=2.0, period_start="2025-02-01")]},
    ]
    assert len(merge_parts("s.pdf", payloads)) == 2


def test_two_documents_in_the_same_part_stay_separate():
    payloads = [{"_label": "p1", "documents": [_doc(kind="check", check={"payee": "A"}),
                                                _doc(kind="check", check={"payee": "B"})]}]
    assert len(merge_parts("s.pdf", payloads)) == 2


# --- many files at once ------------------------------------------------------

def test_read_files_reads_in_parallel_and_isolates_a_failed_file():
    def fake_ai(src, part, year):
        if src.filename == "bad.pdf":
            raise AIExtractionError("OUT_OF_CREDIT", "No credit.", "Add credit.")
        return {"documents": [_doc(rows=[_row(1.0)], cont=part.pdf_pages[0] > 0)],
                "_usage": {"input_tokens": 10, "output_tokens": 5}}

    progress = []
    results = read_files([("good.pdf", _pdf(6)), ("bad.pdf", _pdf(2)), ("odd.zip", b"x")], 2025,
                         on_progress=lambda d, t, m: progress.append((d, t)), call_ai=fake_ai)
    good = results["good.pdf"]
    assert good["error"] is None and good["part_count"] == 2
    assert len(good["documents"]) == 1 and len(good["documents"][0]["transactions"]) == 2
    assert good["usage"] == {"input_tokens": 20, "output_tokens": 10}
    assert results["bad.pdf"]["error"]["code"] == "OUT_OF_CREDIT"
    assert results["bad.pdf"]["documents"] == []
    assert results["odd.zip"]["error"]["code"] == "UNSUPPORTED_FILE"
    assert progress[-1] == (3, 3)


def test_ai_request_uses_high_detail_and_the_part_only(monkeypatch):
    source = plan_parts(_pdf(6), "statement.pdf")
    content = ai_reader._content_for(source, source.parts[1], 2025)
    assert content[0]["type"] == "input_file" and content[0]["detail"] == "high"
    from pypdf import PdfReader
    import base64
    sent = base64.b64decode(content[0]["file_data"].split(",", 1)[1])
    assert len(PdfReader(io.BytesIO(sent)).pages) == 2
    assert "pages 5-6 of 6" in content[1]["text"] and "2025" in content[1]["text"]
