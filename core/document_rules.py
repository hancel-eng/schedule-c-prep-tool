"""Plain rules applied to what the AI transcribed. No AI in here.

- Turns transcribed rows into the app's transactions (money in / money out,
  signed amounts) and categorizes them with the rule-based TaxCategorizer.
- Tells a credit card payment apart from a refund by rule.
- Checks every statement against its own printed numbers (balances,
  summary totals, running balance per row) and explains the result in
  plain English.
- Works out which months of the tax year are covered.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from core.ai_reader import LIST_KINDS, STATEMENT_KINDS

TOLERANCE = 0.01
CARD_PAYMENT_CATEGORY = "Non-P&L: Credit Card Payment"


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _effective_summary_total(totals: List[Dict[str, Any]], effect: str) -> Optional[float]:
    """Sum of the printed summary lines for one effect. If the box prints
    both a "Total ..." line and the lines it adds up, only the total is
    used, so nothing is counted twice."""
    lines = [t for t in totals if t["effect"] == effect]
    if not lines:
        return None
    for line in lines:
        if "total" in line["label"].lower():
            others = [t["amount"] for t in lines if t is not line]
            if others and abs(sum(others) - line["amount"]) <= TOLERANCE:
                return round(line["amount"], 2)
    return round(sum(t["amount"] for t in lines), 2)


def verify_statement(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Checks a statement (or any list with printed totals) against itself."""
    kind = doc["document_kind"]
    is_card = kind == "credit_card_statement"
    rows = doc.get("transactions") or []
    money_in = round(sum(r["amount"] for r in rows if r["direction"] == "credit"), 2)
    money_out = round(sum(r["amount"] for r in rows if r["direction"] == "debit"), 2)
    opening, closing = doc.get("opening_balance"), doc.get("closing_balance")
    declared_in = _effective_summary_total(doc.get("summary_totals") or [], "credit")
    declared_out = _effective_summary_total(doc.get("summary_totals") or [], "debit")

    in_word = "payments and credits" if is_card else "money in"
    out_word = "charges" if is_card else "money out"
    checks: List[Tuple[bool, str]] = []

    balance_difference = None
    if opening is not None and closing is not None:
        expected = opening + money_out - money_in if is_card else opening + money_in - money_out
        balance_difference = round(expected - closing, 2)
        if abs(balance_difference) <= TOLERANCE:
            checks.append((True, f"Starting balance {_money(opening)} plus and minus every row found "
                                 f"gives exactly the ending balance printed, {_money(closing)}."))
        else:
            checks.append((False, f"Starting balance {_money(opening)} plus and minus every row found gives "
                                  f"{_money(expected)}, but the statement's ending balance is {_money(closing)}. "
                                  f"Off by {_money(abs(balance_difference))}. A row may be missing or misread."))

    deposit_difference = withdrawal_difference = None
    if declared_in is not None:
        deposit_difference = round(declared_in - money_in, 2)
        checks.append(_total_check(in_word, declared_in, money_in, deposit_difference))
    if declared_out is not None:
        withdrawal_difference = round(declared_out - money_out, 2)
        checks.append(_total_check(out_word, declared_out, money_out, withdrawal_difference))

    chain = _running_balance_check(opening, rows, is_card)
    if chain is not None:
        checks.append(chain)

    failed = [text for ok, text in checks if not ok]
    if failed:
        status, notes = "Discrepancy", failed
    elif checks:
        status, notes = "Reconciled", ["Everything adds up: " + " ".join(text for _, text in checks)]
    else:
        status = "Not Reconcilable"
        notes = ["This file has no balances or totals printed, so the app can't double-check it. "
                 "Please look over its rows."]

    unreadable = doc.get("unreadable_or_notes") or []
    if unreadable:
        notes.append("The AI noted: " + " ".join(unreadable))

    return {
        "source_file": doc.get("source_file"),
        "document_label": document_label(doc),
        "document_type": kind,
        "status": status,
        "transaction_count": len(rows),
        "beginning_balance": opening,
        "ending_balance": closing,
        "declared_deposits": declared_in,
        "extracted_deposits": money_in,
        "declared_withdrawals": declared_out,
        "extracted_withdrawals": money_out,
        "balance_difference": balance_difference,
        "deposit_difference": deposit_difference,
        "withdrawal_difference": withdrawal_difference,
        "notes": " ".join(notes),
    }


def _total_check(word: str, declared: float, found: float, difference: float) -> Tuple[bool, str]:
    if abs(difference) <= TOLERANCE:
        return True, f"Total {word} matches the statement ({_money(declared)})."
    direction = "missing" if difference > 0 else "too much"
    return False, (f"The statement says total {word} is {_money(declared)}, but the rows found add up to "
                   f"{_money(found)} ({_money(abs(difference))} {direction}).")


def _running_balance_check(opening: Optional[float], rows: List[Dict[str, Any]],
                           is_card: bool) -> Optional[Tuple[bool, str]]:
    printed = [i for i, r in enumerate(rows) if r.get("running_balance") is not None]
    if not printed:
        return None
    balance = opening
    for index, row in enumerate(rows):
        step = row["amount"] if (row["direction"] == "credit") != is_card else -row["amount"]
        if balance is not None:
            balance = round(balance + step, 2)
        if row.get("running_balance") is None:
            continue
        if balance is not None and abs(balance - row["running_balance"]) > TOLERANCE:
            return False, (f"Row {index + 1} ({row.get('date') or 'no date'}, {row['description'][:40]}) should leave "
                           f"a balance of {_money(balance)}, but the statement prints {_money(row['running_balance'])} "
                           f"there. A row near it may be missing or misread.")
        balance = row["running_balance"]
    return True, "The balance printed on each row matches row by row."


def verify_invoice_or_receipt(data: Dict[str, Any]) -> Tuple[str, str]:
    subtotal, tax, total = data.get("subtotal"), data.get("tax"), data.get("total")
    items = [i["line_total"] for i in data.get("line_items") or [] if i.get("line_total") is not None]
    problems, passed = [], 0
    if subtotal is not None and total is not None:
        expected = subtotal + (tax or 0.0)
        if abs(expected - total) <= TOLERANCE:
            passed += 1
        else:
            problems.append(f"Subtotal plus tax is {_money(expected)}, but the total says {_money(total)}.")
    base = subtotal if subtotal is not None else total
    if items and base is not None:
        if abs(sum(items) - base) <= TOLERANCE:
            passed += 1
        else:
            problems.append(f"The items add up to {_money(sum(items))}, but the document says {_money(base)}.")
    if problems:
        return "Discrepancy", " ".join(problems)
    if passed:
        return "Reconciled", "The amounts on this document add up."
    return "Not Reconcilable", "Not enough amounts printed to double-check this document."


def document_label(doc: Dict[str, Any]) -> str:
    kind = doc["document_kind"].replace("_", " ")
    who = doc.get("issuer_name") or ""
    account = f" ...{doc['account_last4']}" if doc.get("account_last4") else ""
    period = ""
    if doc.get("period_start") or doc.get("period_end"):
        period = f" {doc.get('period_start') or '?'} to {doc.get('period_end') or '?'}"
    return " ".join(f"{who}{account} {kind}{period}".split())


def _payee_for(row: Dict[str, Any]) -> str:
    description = " ".join((row.get("description") or "").split())
    number = row.get("check_number")
    if number and not re.match(r"^check\b", description, re.IGNORECASE):
        return f"Check #{number}" + (f" {description}" if description and description != number else "")
    return description or "(no description)"


def rows_to_transactions(doc: Dict[str, Any], doc_index: int, categorizer) -> List[Dict[str, Any]]:
    kind = doc["document_kind"]
    is_card = kind == "credit_card_statement"
    source_file = doc.get("source_file", "")
    label = document_label(doc)
    results = []
    for row_index, row in enumerate(doc.get("transactions") or []):
        payee = _payee_for(row)
        written = row.get("category_as_written")
        description = f"{payee} (Orig: {written})" if written else payee
        amount = abs(float(row["amount"]))
        money_in = row["direction"] == "credit"

        if is_card and money_in:
            category, state, score = categorizer.categorize_transaction(
                payee=payee, description=description, amount=-amount, is_deposit=False)
            if category == CARD_PAYMENT_CATEGORY:
                is_deposit, signed = False, -amount
            else:
                is_deposit, signed = True, amount
                category, state, score = categorizer.categorize_transaction(
                    payee=payee, description=description, amount=signed, is_deposit=True)
        else:
            is_deposit = money_in and not is_card
            signed = amount if is_deposit else -amount
            category, state, score = categorizer.categorize_transaction(
                payee=payee, description=description, amount=signed, is_deposit=is_deposit)

        results.append({
            "tx_id": f"{source_file}#doc{doc_index}#row{row_index}",
            "date": row.get("date") or doc.get("period_end") or "",
            "payee": payee,
            "description": payee,
            "amount": signed,
            "is_deposit": is_deposit,
            "category": category,
            "confidence_state": state,
            "confidence_score": score,
            "source_file": source_file,
            "source_document": label,
            "section_heading": row.get("section_heading"),
            "original_category": written or kind.replace("_", " ").title(),
        })
    return results


def apply_rules(filename: str, documents: List[Dict[str, Any]], categorizer) -> Dict[str, Any]:
    """Everything the app needs from one file, computed by rules."""
    transactions, checks, supporting, reconciliation, notes = [], [], [], [], []
    for doc_index, doc in enumerate(documents):
        kind = doc["document_kind"]
        if kind in LIST_KINDS:
            transactions.extend(rows_to_transactions(doc, doc_index, categorizer))
            reconciliation.append(verify_statement(doc))
        elif kind == "check" and doc.get("check"):
            data = doc["check"]
            checks.append({
                "check_number": data.get("check_number"),
                "payee": data.get("payee"),
                "drawer": data.get("drawer"),
                "numerical_amount": data.get("amount"),
                "issue_date": data.get("issue_date"),
                "memo": data.get("memo"),
                "source_file": filename,
            })
        elif kind in ("invoice", "receipt") and doc.get("invoice_or_receipt"):
            data = doc["invoice_or_receipt"]
            status, check_note = verify_invoice_or_receipt(data)
            supporting.append({"type": kind, **{k: v for k, v in data.items() if k != "line_items"},
                               "line_item_count": len(data.get("line_items") or []),
                               "check_status": status, "check_notes": check_note, "source_file": filename})
        else:
            what = "; ".join(doc.get("unreadable_or_notes") or []) or "not a financial document the app uses"
            notes.append(f"Found something that isn't a statement, check, invoice, or receipt ({what}). Nothing was counted from it.")
    if not documents:
        notes.append("The AI found nothing financial in this file. Nothing was counted from it.")
    return {
        "filename": filename,
        "transactions": transactions,
        "check_records": checks,
        "supporting_documents": supporting,
        "reconciliation_results": reconciliation,
        "statement_periods": [(d.get("period_start"), d.get("period_end")) for d in documents
                              if d["document_kind"] in STATEMENT_KINDS],
        "notes": notes,
    }


def months_covered(transactions: List[Dict[str, Any]], periods: List[Tuple[Optional[str], Optional[str]]],
                   tax_year: Optional[int]) -> List[int]:
    months = set()
    dates = [tx.get("date") for tx in transactions] + [end for _, end in periods]
    for value in dates:
        match = re.match(r"^(\d{4})-(\d{2})", str(value or ""))
        if match and (tax_year is None or int(match.group(1)) == tax_year):
            months.add(int(match.group(2)))
    return sorted(m for m in months if 1 <= m <= 12)


def check_12_month_coverage(months: List[int]) -> Dict[str, Any]:
    present = set(months)
    missing = sorted(set(range(1, 13)) - present)
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return {
        "is_complete": not missing,
        "present_months_count": len(present),
        "missing_months": missing,
        "status_message": "All 12 months are here." if not missing else
        f"Missing {len(missing)} month(s): {', '.join(names[m - 1] for m in missing)}.",
    }


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    reconciled = [r for r in results if r["status"] == "Reconciled"]
    problems = [r for r in results if r["status"] == "Discrepancy"]
    unchecked = [r for r in results if r["status"] == "Not Reconcilable"]
    if problems:
        status = "Discrepancy"
        message = f"{len(problems)} of {len(results)} statement(s) don't add up yet. Fix them before trusting the totals."
    elif not results:
        status, message = "Not Reconcilable", "No statements to check yet."
    elif unchecked and not reconciled:
        status, message = "Not Reconcilable", "None of the files had totals the app could check against."
    elif unchecked:
        status = "Partially Reconciled"
        message = f"{len(reconciled)} of {len(results)} statement(s) add up; {len(unchecked)} had no totals to check."
    else:
        status, message = "Reconciled", f"All {len(results)} statement(s) add up exactly."
    return {"status": status, "message": message, "reconciled_count": len(reconciled),
            "discrepancy_count": len(problems), "unverifiable_count": len(unchecked),
            "total_documents": len(results), "results": results}
