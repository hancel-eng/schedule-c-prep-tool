"""The app's only file reader: AI transcribes every file, nothing else.

Pipeline for one uploaded file:
  plan_parts()   split the file into small parts (PDF pages, sheet rows,
                 document lines) so no single AI answer is long enough to be
                 cut off
  read_part()    one AI call per part; if the answer is still cut off, the
                 part is split in half and retried automatically
  merge_parts()  stitch the parts back into whole documents (a statement
                 spread over several parts becomes one statement again)

The AI only transcribes what is printed. It never categorizes, never adds
numbers up, never decides what is income or an expense. Everything after
transcription is done by plain rules in core/document_rules.py.
"""
import base64
import io
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core import ai_errors
from core.ai_errors import AIExtractionError

LLM_MODEL = "gpt-6-astra"
PAGES_PER_PART = 4
LINES_PER_PART = 120
CONTEXT_LINES = 3
REQUEST_TIMEOUT_SECONDS = 300
MAX_RETRIES = 4

PDF_EXTENSIONS = (".pdf",)
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}
SPREADSHEET_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv")
WORD_EXTENSIONS = (".docx",)
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS + tuple(IMAGE_TYPES) + SPREADSHEET_EXTENSIONS + WORD_EXTENSIONS

DOCUMENT_KINDS = ["bank_statement", "credit_card_statement", "check", "invoice", "receipt",
                  "profit_and_loss", "transaction_list", "other"]
STATEMENT_KINDS = {"bank_statement", "credit_card_statement"}
LIST_KINDS = STATEMENT_KINDS | {"profit_and_loss", "transaction_list"}

SYSTEM_PROMPT = """You read financial documents and transcribe them into the \
JSON format you are given. You ONLY transcribe. Never categorize anything for \
taxes. Never add, subtract, or total numbers yourself. Never invent a value \
that is not printed. Never skip a value that is printed.

You receive one part of an uploaded file: some pages of a PDF, an image, or \
some rows of a spreadsheet or Word document shown as text. You are told which \
part it is. The tax year being prepared is given to you; use it, together with \
any statement period printed, to work out the year of dates printed without one.

For every distinct document (or piece of one) in what you receive, add one \
entry to "documents":

- document_kind:
  bank_statement = checking/savings/business bank account statement.
  credit_card_statement = credit card statement.
  check = the image of one written check (front and/or back).
  invoice / receipt = a bill or a proof of purchase.
  profit_and_loss = a profit and loss (income statement) report.
  transaction_list = any other list of transactions: a spreadsheet, a ledger, \
a bank export, a register.
  other = anything else (cover pages, legal disclosures, blank worksheets, \
letters); say briefly what it is in "what_it_is".
- continues_from_previous_part: true only when this entry continues a document \
that started before the part you received (for example pages 5-8 of a \
statement whose first page you were not given).
- issuer_name, account_holder, account_last4 (last 4 digits only), \
period_start, period_end (YYYY-MM-DD): exactly as printed; null if not shown \
in this part.
- opening_balance / closing_balance: as printed (on a credit card: previous \
balance / new balance). null if not in this part.
- summary_totals: every total printed in the statement's account summary or \
activity summary box (the box that shows how the balance moved), one entry \
per printed line, for example "Deposits and additions", \
"ATM & debit card withdrawals", "Checks paid", "Fees", "Payments", \
"Purchases", "Interest charged". Do not include the opening or closing \
balance. effect = "credit" if that total adds money to a bank account or \
lowers what is owed on a credit card, otherwise "debit". item_count only if a \
count is printed beside it. Never take totals from any other box, such as a \
monthly service fee box, fee criteria, rewards, or interest rate tables. \
Empty list if there is no account summary box in this part.
- transactions: EVERY transaction row, in the order printed. That includes \
rows in check listings, fee sections, and interest lines. Never include \
headings, column titles, subtotals, totals, daily balance summaries, or the \
summary box lines. For each row:
  date: YYYY-MM-DD.
  description: the full text exactly as printed, including reference numbers.
  amount: always a positive number.
  direction: "credit" if the row adds money to a bank account or lowers what \
is owed on a credit card, "debit" otherwise.
  running_balance: only if a balance is printed on that same row, else null.
  section_heading: the heading of the section the row is listed under, as \
printed (for example "Electronic Withdrawals", "Payments and Other Credits"); \
null if none.
  check_number: if the row is a check, else null.
  category_as_written: only if the source itself has a category or account \
column (for example a spreadsheet); copy it. Otherwise null.
- For a profit_and_loss report: put each detail line in "transactions" \
(description = the account name, amount, direction "credit" for income lines \
and "debit" for expense lines, section_heading = its section, date = the \
report's period end). Use only the column for the tax year being prepared. \
Never include total, subtotal, gross profit, or net lines.
- check: only for a check: payee, drawer, issue_date, amount, \
amount_in_words, check_number, memo. Otherwise null.
- invoice_or_receipt: only for an invoice or receipt: issuer, customer, date, \
document_number, subtotal, tax, total, and every line item. Otherwise null.
- what_it_is: for document_kind "other", a short description (for example \
"Account disclosures and a blank balancing worksheet"). Otherwise null.
- could_not_read: ONLY places where printed text or numbers could not be read \
with certainty (smudged, cut off, blurry, covered), each as a short sentence \
saying where. Never put general information, explanations, or descriptions \
of the document here. Empty list if everything was readable.

Spreadsheets and text: each data row of a transaction table is one \
transaction. If money in and money out share one amount column, the sign or \
the column headings tell you the direction. Lines marked [CONTEXT] are only \
there so you can understand the column headings; never extract them.

If nothing financial is present, return "documents": []."""


def _nullable(schema_type):
    return {"type": [schema_type, "null"]}


def _object(properties: Dict[str, Any], nullable: bool = False) -> Dict[str, Any]:
    return {
        "type": ["object", "null"] if nullable else "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


_TRANSACTION = _object({
    "date": _nullable("string"),
    "description": {"type": "string"},
    "amount": {"type": "number"},
    "direction": {"type": "string", "enum": ["credit", "debit"]},
    "running_balance": _nullable("number"),
    "section_heading": _nullable("string"),
    "check_number": _nullable("string"),
    "category_as_written": _nullable("string"),
})

_SUMMARY_TOTAL = _object({
    "label": {"type": "string"},
    "amount": {"type": "number"},
    "effect": {"type": "string", "enum": ["credit", "debit"]},
    "item_count": _nullable("integer"),
})

_CHECK = _object({
    "payee": _nullable("string"),
    "drawer": _nullable("string"),
    "issue_date": _nullable("string"),
    "amount": _nullable("number"),
    "amount_in_words": _nullable("string"),
    "check_number": _nullable("string"),
    "memo": _nullable("string"),
}, nullable=True)

_LINE_ITEM = _object({
    "description": {"type": "string"},
    "quantity": _nullable("number"),
    "unit_price": _nullable("number"),
    "line_total": _nullable("number"),
})

_INVOICE_OR_RECEIPT = _object({
    "issuer": _nullable("string"),
    "customer": _nullable("string"),
    "date": _nullable("string"),
    "document_number": _nullable("string"),
    "subtotal": _nullable("number"),
    "tax": _nullable("number"),
    "total": _nullable("number"),
    "line_items": {"type": "array", "items": _LINE_ITEM},
}, nullable=True)

_DOCUMENT = _object({
    "document_kind": {"type": "string", "enum": DOCUMENT_KINDS},
    "continues_from_previous_part": {"type": "boolean"},
    "issuer_name": _nullable("string"),
    "account_holder": _nullable("string"),
    "account_last4": _nullable("string"),
    "period_start": _nullable("string"),
    "period_end": _nullable("string"),
    "opening_balance": _nullable("number"),
    "closing_balance": _nullable("number"),
    "summary_totals": {"type": "array", "items": _SUMMARY_TOTAL},
    "transactions": {"type": "array", "items": _TRANSACTION},
    "check": _CHECK,
    "invoice_or_receipt": _INVOICE_OR_RECEIPT,
    "what_it_is": _nullable("string"),
    "could_not_read": {"type": "array", "items": {"type": "string"}},
})

RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "transcribed_documents",
    "strict": True,
    "schema": _object({"documents": {"type": "array", "items": _DOCUMENT}}),
}


@dataclass
class Part:
    """One piece of a file, small enough for one complete AI answer."""
    filename: str
    kind: str  # "pdf" | "image" | "text"
    label: str
    pdf_pages: Optional[List[int]] = None  # 0-based page indexes into the source PDF
    lines: Optional[List[str]] = None
    context_lines: List[str] = field(default_factory=list)
    media_type: Optional[str] = None


@dataclass
class SourceFile:
    filename: str
    raw_bytes: bytes
    parts: List[Part]
    page_count: Optional[int] = None


def _extension(filename: str) -> str:
    return os.path.splitext(filename.lower())[1]


def _file_error(code: str, message: str, fix: str, detail: str = "") -> AIExtractionError:
    return AIExtractionError(code, message, fix, detail)


def plan_parts(raw_bytes: bytes, filename: str) -> SourceFile:
    """Splits a file into parts. Raises AIExtractionError with a clear code
    for an empty, damaged, locked, or unsupported file."""
    if not raw_bytes:
        raise _file_error("EMPTY_FILE", "This file is empty.", "Upload the file again.")
    ext = _extension(filename)
    if ext in PDF_EXTENSIONS:
        return _plan_pdf(raw_bytes, filename)
    if ext in IMAGE_TYPES:
        return SourceFile(filename, raw_bytes, [Part(filename, "image", "the whole image", media_type=IMAGE_TYPES[ext])])
    if ext in SPREADSHEET_EXTENSIONS:
        return _plan_text(raw_bytes, filename, _spreadsheet_lines(raw_bytes, filename))
    if ext in WORD_EXTENSIONS:
        return _plan_text(raw_bytes, filename, {"Document": _word_lines(raw_bytes, filename)})
    if ext == ".doc":
        raise _file_error("UNSUPPORTED_FILE", "This is an old Word file (.doc), which the app can't open.",
                          "Open it in Word and save it as .docx or PDF, then upload that.")
    if ext in (".heic", ".heif"):
        raise _file_error("UNSUPPORTED_FILE", "This is an iPhone photo format (HEIC) the app can't open.",
                          "Export or save the photo as JPG or PNG, then upload that.")
    raise _file_error("UNSUPPORTED_FILE", f"The app can't read this type of file ({ext or 'no extension'}).",
                      "Upload a PDF, a photo (JPG or PNG), an Excel or CSV file, or a Word (.docx) file.")


def _open_pdf(raw_bytes: bytes, filename: str):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        if reader.is_encrypted and not reader.decrypt(""):
            raise _file_error("FILE_LOCKED", "This PDF is locked with a password.",
                              "Open it, remove the password (or print it to a new PDF), then upload it again.")
        page_count = len(reader.pages)
    except AIExtractionError:
        raise
    except (PdfReadError, ValueError, OSError, KeyError) as e:
        raise _file_error("FILE_DAMAGED", "This PDF seems to be damaged and can't be opened.",
                          "Download or export the PDF again, then upload the new copy.",
                          f"{type(e).__name__}: {e}") from e
    if page_count == 0:
        raise _file_error("EMPTY_FILE", "This PDF has no pages.", "Upload the file again.")
    return reader


def _plan_pdf(raw_bytes: bytes, filename: str) -> SourceFile:
    reader = _open_pdf(raw_bytes, filename)
    total = len(reader.pages)
    parts = []
    for start in range(0, total, PAGES_PER_PART):
        pages = list(range(start, min(start + PAGES_PER_PART, total)))
        parts.append(Part(filename, "pdf", _pages_label(pages, total), pdf_pages=pages))
    return SourceFile(filename, raw_bytes, parts, page_count=total)


def _pages_label(pages: List[int], total: int) -> str:
    if len(pages) == 1:
        return f"page {pages[0] + 1} of {total}"
    return f"pages {pages[0] + 1}-{pages[-1] + 1} of {total}"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        import pandas as pd
        if pd.isna(value):
            return ""
        if isinstance(value, pd.Timestamp):
            return value.date().isoformat() if value == value.normalize() else value.isoformat()
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def _spreadsheet_lines(raw_bytes: bytes, filename: str) -> Dict[str, List[str]]:
    import pandas as pd
    ext = _extension(filename)
    try:
        if ext == ".csv":
            frame = None
            for encoding in ("utf-8-sig", "latin-1"):
                try:
                    frame = pd.read_csv(io.BytesIO(raw_bytes), header=None, dtype=str,
                                        keep_default_na=False, encoding=encoding,
                                        sep=None, engine="python")
                    break
                except UnicodeDecodeError:
                    continue
            sheets = {"CSV": frame}
        else:
            sheets = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=None, header=None, dtype=object)
    except Exception as e:
        raise _file_error("FILE_DAMAGED", "This spreadsheet couldn't be opened.",
                          "Open it in Excel, save it again (as .xlsx or .csv), then upload the new copy.",
                          f"{type(e).__name__}: {e}") from e

    result = {}
    for sheet_name, frame in sheets.items():
        if frame is None:
            continue
        lines = []
        for row_number, row in enumerate(frame.itertuples(index=False), start=1):
            cells = [_cell_text(v) for v in row]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                lines.append(f"R{row_number}: " + " | ".join(cells))
        if lines:
            result[str(sheet_name)] = lines
    if not result:
        raise _file_error("EMPTY_FILE", "This spreadsheet has no data in it.", "Check the file and upload it again.")
    return result


def _word_lines(raw_bytes: bytes, filename: str) -> List[str]:
    try:
        import docx
        document = docx.Document(io.BytesIO(raw_bytes))
    except Exception as e:
        raise _file_error("FILE_DAMAGED", "This Word file couldn't be opened.",
                          "Open it in Word, save it again as .docx or PDF, then upload the new copy.",
                          f"{type(e).__name__}: {e}") from e
    lines = []
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(node.text or "" for node in child.iter() if node.tag.endswith("}t")).strip()
            if text:
                lines.append(text)
        elif tag == "tbl":
            for row in child.iter():
                if not row.tag.endswith("}tr"):
                    continue
                cells = []
                for cell in row.iter():
                    if cell.tag.endswith("}tc"):
                        cells.append("".join(n.text or "" for n in cell.iter() if n.tag.endswith("}t")).strip())
                if any(cells):
                    lines.append(" | ".join(cells))
    if not lines:
        raise _file_error("EMPTY_FILE", "This Word file has no text the app can read.",
                          "If it only contains pictures, save it as PDF and upload that instead.")
    return lines


def _plan_text(raw_bytes: bytes, filename: str, sections: Dict[str, List[str]]) -> SourceFile:
    parts = []
    for section_name, lines in sections.items():
        context = lines[:CONTEXT_LINES]
        for start in range(0, len(lines), LINES_PER_PART):
            chunk = lines[start:start + LINES_PER_PART]
            end = start + len(chunk)
            label = f"{section_name}: lines {start + 1}-{end} of {len(lines)}"
            parts.append(Part(filename, "text", label, lines=chunk,
                              context_lines=context if start > 0 else []))
    return SourceFile(filename, raw_bytes, parts)


def _pdf_bytes_for(source: SourceFile, pages: List[int]) -> bytes:
    from pypdf import PdfWriter
    reader = _open_pdf(source.raw_bytes, source.filename)
    writer = PdfWriter()
    for index in pages:
        writer.add_page(reader.pages[index])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _content_for(source: SourceFile, part: Part, tax_year: Optional[int]) -> List[Dict[str, Any]]:
    intro = (f"File: {part.filename}. This is {part.label}. "
             f"Tax year being prepared: {tax_year or 'unknown'}.")
    if part.kind == "pdf":
        data = base64.standard_b64encode(_pdf_bytes_for(source, part.pdf_pages)).decode()
        return [
            {"type": "input_file", "filename": f"{part.label}.pdf", "detail": "high",
             "file_data": f"data:application/pdf;base64,{data}"},
            {"type": "input_text", "text": intro},
        ]
    if part.kind == "image":
        data = base64.standard_b64encode(source.raw_bytes).decode()
        return [
            {"type": "input_image", "detail": "high", "image_url": f"data:{part.media_type};base64,{data}"},
            {"type": "input_text", "text": intro},
        ]
    text = intro + "\n\n"
    if part.context_lines:
        text += "\n".join(f"[CONTEXT] {line}" for line in part.context_lines) + "\n"
    text += "\n".join(part.lines)
    return [{"type": "input_text", "text": text}]


def _split(part: Part) -> Optional[List[Part]]:
    if part.kind == "pdf" and len(part.pdf_pages) > 1:
        middle = len(part.pdf_pages) // 2
        total_hint = part.label.rsplit(" of ", 1)[-1]
        halves = [part.pdf_pages[:middle], part.pdf_pages[middle:]]
        return [Part(part.filename, "pdf", _pages_label(h, int(total_hint)), pdf_pages=h) for h in halves]
    if part.kind == "text" and len(part.lines) > 1:
        middle = len(part.lines) // 2
        context = part.context_lines or []
        return [
            Part(part.filename, "text", f"{part.label} (first half)", lines=part.lines[:middle], context_lines=context),
            Part(part.filename, "text", f"{part.label} (second half)", lines=part.lines[middle:],
                 context_lines=context or part.lines[:CONTEXT_LINES]),
        ]
    return None


def _client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ai_errors.missing_api_key()
    import openai
    return openai.OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES)


def parse_structured_response(response: Any) -> Dict[str, Any]:
    """Accepts only a complete, non-refused, valid-JSON answer."""
    status = getattr(response, "status", None)
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        if reason == "content_filter":
            raise ai_errors.content_blocked(f"status=incomplete, reason={reason}")
        raise ai_errors.answer_cut_off(f"status=incomplete, reason={reason}")
    if status == "failed":
        raise ai_errors.response_failed(str(getattr(response, "error", "") or "status=failed"))

    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                raise ai_errors.ai_refused(getattr(content, "refusal", "") or "")

    text = getattr(response, "output_text", "") or ""
    if not text.strip():
        raise ai_errors.empty_answer(f"status={status}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ai_errors.unreadable_answer(f"{e}; answer starts with: {text[:200]!r}") from e


def _call_ai(source: SourceFile, part: Part, tax_year: Optional[int]) -> Dict[str, Any]:
    import openai
    client = _client()
    try:
        response = client.responses.create(
            model=LLM_MODEL,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": _content_for(source, part, tax_year)}],
            text={"format": RESPONSE_FORMAT},
        )
    except openai.OpenAIError as e:
        raise ai_errors.from_openai_exception(e) from e
    payload = parse_structured_response(response)
    usage = getattr(response, "usage", None)
    payload["_usage"] = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }
    return payload


def read_part(source: SourceFile, part: Part, tax_year: Optional[int] = None,
              call_ai: Callable = None) -> List[Dict[str, Any]]:
    """Reads one part. Returns one payload per piece actually sent (more than
    one only when the part had to be split because an answer was cut off)."""
    call_ai = call_ai or _call_ai
    try:
        payload = call_ai(source, part, tax_year)
        payload["_label"] = part.label
        return [payload]
    except AIExtractionError as e:
        if e.code != "ANSWER_CUT_OFF":
            raise
        halves = _split(part)
        if not halves:
            raise ai_errors.AIExtractionError(
                e.code, f"The AI's answer for {part.label} was cut off, even at the smallest size.",
                "Nothing from this part was used. Send the technical detail to whoever maintains the app.",
                e.detail) from e
        results = []
        for half in halves:
            results.extend(read_part(source, half, tax_year, call_ai))
        return results


def _normalize(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _same_account(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    left, right = a.get("account_last4"), b.get("account_last4")
    return not left or not right or _digits(left) == _digits(right)


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def merge_parts(filename: str, payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Joins per-part payloads, in file order, into whole documents."""
    documents: List[Dict[str, Any]] = []
    for part_index, payload in enumerate(payloads):
        for entry in payload.get("documents") or []:
            entry = dict(entry)
            entry["parts"] = [payload.get("_label", f"part {part_index + 1}")]
            previous = documents[-1] if documents else None
            looks_like_continuation = (
                part_index > 0 and previous is not None
                and entry["document_kind"] in LIST_KINDS
                and previous["document_kind"] == entry["document_kind"]
                and _same_account(previous, entry)
                and (entry.get("continues_from_previous_part")
                     or (entry.get("opening_balance") is None and not entry.get("period_start")))
                and entry["parts"][0] not in previous["parts"]
            )
            if looks_like_continuation:
                _absorb(previous, entry)
            else:
                entry["source_file"] = filename
                documents.append(entry)
    return documents


def _absorb(target: Dict[str, Any], entry: Dict[str, Any]) -> None:
    for key in ("issuer_name", "account_holder", "account_last4", "period_start", "period_end",
                "opening_balance", "closing_balance"):
        if target.get(key) is None and entry.get(key) is not None:
            target[key] = entry[key]
    seen = {(_normalize(t["label"]), round(t["amount"], 2)) for t in target["summary_totals"]}
    for total in entry.get("summary_totals") or []:
        key = (_normalize(total["label"]), round(total["amount"], 2))
        if key not in seen:
            target["summary_totals"].append(total)
            seen.add(key)
    target["transactions"].extend(entry.get("transactions") or [])
    target["could_not_read"].extend(entry.get("could_not_read") or [])
    target["parts"].extend(entry["parts"])


PARALLEL_CALLS = 4


def read_files(files: List[tuple], tax_year: Optional[int] = None,
               on_progress: Optional[Callable[[int, int, str], None]] = None,
               call_ai: Callable = None, max_workers: int = PARALLEL_CALLS) -> Dict[str, Dict[str, Any]]:
    """Reads many (filename, raw_bytes) files, several AI calls at a time.

    Returns {filename: {"documents", "error", "usage", "part_count"}}. A file
    with any failed part gets "error" set and no documents: a half-read file
    would give wrong totals, so nothing from it is used. on_progress(done,
    total, message) is called from the calling thread only."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, SourceFile] = {}
    tasks = []
    for filename, raw_bytes in files:
        results[filename] = {"documents": [], "error": None, "usage": {"input_tokens": 0, "output_tokens": 0},
                             "part_count": 0, "payloads": {}}
        try:
            source = plan_parts(raw_bytes, filename)
        except AIExtractionError as e:
            results[filename]["error"] = e.to_dict()
            continue
        sources[filename] = source
        results[filename]["part_count"] = len(source.parts)
        tasks.extend((filename, index, part) for index, part in enumerate(source.parts))

    total, done = len(tasks), 0
    if on_progress:
        on_progress(0, total, "Starting")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(read_part, sources[f], part, tax_year, call_ai): (f, index, part)
                   for f, index, part in tasks}
        for future in as_completed(futures):
            filename, index, part = futures[future]
            entry = results[filename]
            try:
                entry["payloads"][index] = future.result()
            except AIExtractionError as e:
                if entry["error"] is None:
                    entry["error"] = {**e.to_dict(), "where": part.label}
            except Exception as e:
                if entry["error"] is None:
                    entry["error"] = {"code": "UNKNOWN_AI_ERROR",
                                      "message": "Something unexpected went wrong while reading this file.",
                                      "fix": "Send the technical detail to whoever maintains the app.",
                                      "detail": f"{type(e).__name__}: {e}", "where": part.label}
            done += 1
            if on_progress:
                on_progress(done, total, f"{filename} ({part.label})")

    for filename, entry in results.items():
        payloads = [p for index in sorted(entry["payloads"]) for p in entry["payloads"][index]]
        for payload in payloads:
            usage = payload.get("_usage") or {}
            entry["usage"]["input_tokens"] += usage.get("input_tokens", 0)
            entry["usage"]["output_tokens"] += usage.get("output_tokens", 0)
        if entry["error"] is None:
            entry["documents"] = merge_parts(filename, payloads)
        del entry["payloads"]
    return results
