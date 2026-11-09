"""One client's workpaper, rebuilt from its source of truth.

Source of truth (what gets saved):
  files              {filename: {"documents", "original_documents", "error",
                      "usage", "part_count"}} -- what the AI transcribed, with
                      any hand corrections applied to "documents"
  manual_edits       audit trail of every hand correction
  answered_questions every client answer applied, in order

Everything else (transactions, categories, reconciliation, coverage, the
answer log) is derived by rules in rebuild(), so a hand correction can
never wipe out a client answer and vice versa.
"""
import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core import document_rules
from core.answer_applier import apply_client_answers
from core.check_matcher import match_checks_to_transactions

EDITABLE_ROW_FIELDS = ("date", "description", "amount", "direction", "running_balance")
EDITABLE_DOC_FIELDS = ("opening_balance", "closing_balance")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def assign_row_ids(documents: List[Dict[str, Any]]) -> None:
    for doc in documents:
        rows = doc.get("transactions") or []
        for index, row in enumerate(rows):
            row.setdefault("row_id", f"r{index + 1}")
        doc["next_row_number"] = len(rows) + 1


def ingest(read_results: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Turns read_files() output into stored files, keeping an untouched copy
    of what the AI read so any hand correction can be undone."""
    files = {}
    for filename, read in read_results.items():
        documents = copy.deepcopy(read.get("documents") or [])
        assign_row_ids(documents)
        files[filename] = {
            "documents": documents,
            "original_documents": copy.deepcopy(documents),
            "error": read.get("error"),
            "usage": read.get("usage") or {},
            "part_count": read.get("part_count", 0),
        }
    return files


def rebuild(files: Dict[str, Dict[str, Any]], answered_questions: List[Dict[str, Any]],
            tax_year: Optional[int], categorizer) -> Dict[str, Any]:
    transactions, reconciliation, checks, supporting, periods, diagnostics = [], [], [], [], [], []
    for filename, entry in files.items():
        usage = entry.get("usage") or {}
        usage_note = (f"AI use: {usage.get('input_tokens', 0):,} tokens in, "
                      f"{usage.get('output_tokens', 0):,} tokens out, {entry.get('part_count', 0)} part(s).")
        if entry.get("error"):
            diagnostics.append({"file": filename, "type": "Not read", "count": 0,
                                "details": ["Nothing from this file was counted.", usage_note],
                                "ai_error": entry["error"]})
            continue
        ruled = document_rules.apply_rules(filename, entry["documents"], categorizer)
        transactions.extend(ruled["transactions"])
        reconciliation.extend(ruled["reconciliation_results"])
        checks.extend(ruled["check_records"])
        supporting.extend(ruled["supporting_documents"])
        periods.extend(ruled["statement_periods"])
        found = [document_rules.document_label(d) for d in entry["documents"]] or ["nothing financial"]
        diagnostics.append({"file": filename, "type": "Read by AI", "count": len(ruled["transactions"]),
                            "details": [f"Found: {', '.join(found)}."] + ruled["notes"] + [usage_note],
                            "ai_error": None})

    _, unmatched_checks = match_checks_to_transactions(checks, transactions)
    transactions, correction_log = apply_client_answers(transactions, answered_questions)
    coverage = document_rules.check_12_month_coverage(
        document_rules.months_covered(transactions, periods, tax_year))
    return {
        "all_transactions": transactions,
        "correction_log": correction_log,
        "diagnostics_log": diagnostics,
        "coverage_info": coverage,
        "reconciliation_results": reconciliation,
        "reconciliation_summary": document_rules.summarize(reconciliation),
        "check_records": checks,
        "unmatched_check_records": unmatched_checks,
        "supporting_documents": supporting,
    }


def statement_documents(files: Dict[str, Dict[str, Any]]) -> List[tuple]:
    """(filename, doc_index, doc) for every statement or transaction list."""
    found = []
    for filename, entry in files.items():
        for index, doc in enumerate(entry.get("documents") or []):
            if doc.get("document_kind") in document_rules.LIST_KINDS:
                found.append((filename, index, doc))
    return found


def _clean_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else round(number, 2)


def save_corrections(files: Dict[str, Dict[str, Any]], manual_edits: List[Dict[str, Any]],
                     filename: str, doc_index: int, *, rows: List[Dict[str, Any]],
                     summary_totals: List[Dict[str, Any]], opening_balance: Any,
                     closing_balance: Any) -> int:
    """Replaces one document's editable values with what the person typed,
    logging every change. Rows keep their row_id; new rows get a fresh one.
    Returns the number of changes logged."""
    doc = files[filename]["documents"][doc_index]
    label = document_rules.document_label(doc)
    changes = []

    def log(what: str, old: Any, new: Any) -> None:
        changes.append({"when": _now(), "file": filename, "document": label,
                        "change": what, "before": old, "after": new})

    for field_name, value in (("opening_balance", opening_balance), ("closing_balance", closing_balance)):
        new = _clean_number(value)
        if new != doc.get(field_name):
            log(f"{field_name.replace('_', ' ').capitalize()}", doc.get(field_name), new)
            doc[field_name] = new

    old_rows = {row["row_id"]: row for row in doc.get("transactions") or []}
    new_rows, kept_ids = [], set()
    for typed in rows:
        amount = _clean_number(typed.get("amount"))
        description = (typed.get("description") or "").strip()
        if amount is None and not description:
            continue
        row_id = typed.get("row_id")
        if row_id in old_rows and row_id not in kept_ids:
            row = dict(old_rows[row_id])
            kept_ids.add(row_id)
            for field_name in EDITABLE_ROW_FIELDS:
                new = _clean_row_value(field_name, typed.get(field_name))
                if new != row.get(field_name):
                    log(f"Row {row_id} {field_name.replace('_', ' ')}", row.get(field_name), new)
                    row[field_name] = new
        else:
            row_id = f"r{doc.get('next_row_number', len(old_rows) + 1)}"
            doc["next_row_number"] = doc.get("next_row_number", len(old_rows) + 1) + 1
            row = {"row_id": row_id, "section_heading": None, "check_number": None,
                   "category_as_written": None, "added_by_hand": True}
            for field_name in EDITABLE_ROW_FIELDS:
                row[field_name] = _clean_row_value(field_name, typed.get(field_name))
            row["amount"] = row["amount"] or 0.0
            log(f"Added row {row_id}", None, _row_summary(row))
        row["edited_by_hand"] = row.get("edited_by_hand") or row_id not in old_rows or row != old_rows.get(row_id)
        new_rows.append(row)
    for row_id, row in old_rows.items():
        if row_id not in kept_ids:
            log(f"Deleted row {row_id}", _row_summary(row), None)
    doc["transactions"] = new_rows

    cleaned_totals = []
    for total in summary_totals:
        amount = _clean_number(total.get("amount"))
        label_text = (total.get("label") or "").strip()
        if amount is None or not label_text:
            continue
        cleaned_totals.append({"label": label_text, "amount": amount,
                               "effect": "credit" if total.get("effect") == "credit" else "debit",
                               "item_count": total.get("item_count")})
    if _totals_key(cleaned_totals) != _totals_key(doc.get("summary_totals") or []):
        log("Summary totals", _totals_summary(doc.get("summary_totals") or []), _totals_summary(cleaned_totals))
        doc["summary_totals"] = cleaned_totals

    if changes:
        doc["corrected_by_hand"] = True
        doc.pop("checked_by_hand", None)
    manual_edits.extend(changes)
    return len(changes)


def _clean_row_value(field_name: str, value: Any) -> Any:
    if field_name in ("amount", "running_balance"):
        number = _clean_number(value)
        return abs(number) if (field_name == "amount" and number is not None) else number
    if field_name == "direction":
        return "credit" if value == "credit" else "debit"
    if field_name == "date":
        if value is None or value == "":
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]
    return (value or "").strip()


def _row_summary(row: Dict[str, Any]) -> str:
    sign = "+" if row.get("direction") == "credit" else "-"
    return f"{row.get('date') or 'no date'} {row.get('description') or ''} {sign}${row.get('amount') or 0:,.2f}".strip()


def _totals_key(totals: List[Dict[str, Any]]) -> List[tuple]:
    return sorted((t["label"].strip().lower(), round(t["amount"], 2), t["effect"]) for t in totals)


def _totals_summary(totals: List[Dict[str, Any]]) -> str:
    return "; ".join(f"{t['label']} {'+' if t['effect'] == 'credit' else '-'}${t['amount']:,.2f}" for t in totals) or "(none)"


def mark_checked_by_hand(files, manual_edits, filename: str, doc_index: int, note: str) -> None:
    doc = files[filename]["documents"][doc_index]
    doc["checked_by_hand"] = {"note": note.strip(), "when": _now()}
    manual_edits.append({"when": _now(), "file": filename, "document": document_rules.document_label(doc),
                         "change": "Marked as checked by hand", "before": None, "after": note.strip()})


def undo_corrections(files, manual_edits, filename: str, doc_index: int) -> None:
    """Puts one document back exactly as the AI read it."""
    entry = files[filename]
    original = copy.deepcopy(entry["original_documents"][doc_index])
    label = document_rules.document_label(entry["documents"][doc_index])
    entry["documents"][doc_index] = original
    manual_edits.append({"when": _now(), "file": filename, "document": label,
                         "change": "Undid all hand corrections (back to what the AI read)",
                         "before": None, "after": None})
