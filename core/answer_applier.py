"""Closes the loop Lindsay asked about directly: once the client answers the
auto-generated questions, how does the workpaper's numbers update?

This module takes the transaction list and the (now-answered) question list
and applies each recognized answer, producing an updated transaction list
plus an audit log of exactly what changed and why -- every summarized number
must stay traceable to a source, including corrections applied from a
client's answer, not just the original extraction.

Answers are constrained to a small fixed vocabulary per question type (see
ANSWER_OPTIONS) rather than free text, specifically so this module never has
to guess what an answer meant. An answer outside that vocabulary, or left
blank, changes nothing and the exception stays open -- the same
"never fabricate a classification" rule the categorizer itself follows.
"""
from typing import Any, Dict, List, Optional, Tuple

from core.tax_categorizer import SCHEDULE_C_CATEGORIES
from core.transaction_utils import transaction_key

# The answer vocabulary offered per question category. A Streamlit selectbox
# (or an Excel data-validation dropdown, for a preparer working offline)
# should be built from these, not free text.
ANSWER_OPTIONS = {
    "Expense Verification": ["", "Business", "Personal", "Owner Draw", "Unclear"],
    "Asset Purchase": ["", "Confirmed Asset", "Not an Asset", "Unclear"],
    "Form 1099 Verification": ["", "Filed", "Will File", "Not Required"],
    # Populated at call time from the categorizer's own category list, so it
    # can never drift out of sync with what the tool actually recognizes.
    "Uncategorized Expense": None,
}

PERSONAL_CONFIRMED_CATEGORY = "Non-P&L: Personal Expense (Client Confirmed)"
ASSET_CONFIRMED_CATEGORY = "Line 13: Depreciation and section 179"
# The existing Non-P&L category, not a new one -- an owner draw is already a
# recognized exclusion, this just gives a client's answer a direct path to
# it. Added after a real vendor group ("Cash App*dakota") turned out to
# likely be the business owner paying himself, which is neither a business
# expense nor a personal one in the sense those two answers mean.
OWNER_DRAW_CATEGORY = "Non-P&L: Owner Draw / Contribution"

_VALID_RECATEGORIZATION_TARGETS = set(SCHEDULE_C_CATEGORIES.keys()) | {
    "Non-P&L: Internal Transfer",
    "Non-P&L: Credit Card Payment",
    "Non-P&L: Owner Draw / Contribution",
    "Non-P&L: Loan Proceeds / Repayment",
    "Non-P&L: Tax Refund / Reimbursement",
    "Non-P&L: Returned/Reversed Deposit",
    "Non-P&L: Vendor Purchase Credit",
}


def uncategorized_expense_answer_options() -> List[str]:
    """The dropdown choices for an "Uncategorized Expense" question: every
    real Schedule C category the tool itself uses, so a selected answer is
    always something the categorizer would also produce."""
    return [""] + sorted(_VALID_RECATEGORIZATION_TARGETS)


def apply_client_answers(transactions: List[Dict[str, Any]],
                         questions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Applies every answered question to a copy of the transaction list.

    Returns (updated_transactions, correction_log). transactions is not
    mutated in place -- callers (the Streamlit session) hold the previous
    version until they choose to accept the new one.
    """
    updated = [dict(tx) for tx in transactions]
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for tx in updated:
        by_key.setdefault(transaction_key(tx), []).append(tx)

    correction_log: List[Dict[str, Any]] = []

    for q in questions:
        answer = (q.get("answer") or "").strip()
        if not answer:
            continue

        category = q.get("category")
        if category == "Expense Verification":
            _apply_expense_verification(q, answer, by_key, correction_log)
        elif category == "Asset Purchase":
            _apply_asset_purchase(q, answer, by_key, correction_log)
        elif category == "Form 1099 Verification":
            _apply_1099_verification(q, answer, correction_log)
        elif category == "Uncategorized Expense":
            _apply_uncategorized_expense(q, answer, by_key, correction_log)

    return updated, correction_log


def _matching_transactions(q: Dict[str, Any], by_key: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    # transaction_keys is a list even for a single-transaction question (see
    # core/question_generator.py) -- an "Expense Verification" question can
    # carry many keys when it represents a whole vendor group (see
    # core/vendor_grouping.py), and one answer applies to every one of them.
    keys = q.get("transaction_keys") or []
    matches = []
    for key in keys:
        matches.extend(by_key.get(key, []))
    return matches


def _log(correction_log, q, matched_count, old_category, new_category, note, applied):
    correction_log.append({
        "item_id": q.get("item_id"),
        "question_category": q.get("category"),
        "payee": q.get("payee"),
        "date": q.get("date"),
        "amount": q.get("amount"),
        "client_answer": q.get("answer"),
        "client_response": q.get("client_response", ""),
        "matched_transactions": matched_count,
        "old_category": old_category,
        "new_category": new_category,
        "note": note,
        "applied": applied,
    })


def _apply_expense_verification(q, answer, by_key, correction_log):
    matches = _matching_transactions(q, by_key)
    if not matches:
        _log(correction_log, q, 0, None, None,
             "No matching transaction found -- the extraction that produced "
             "this question may have changed since it was generated.", False)
        return

    if answer == "Business":
        for tx in matches:
            tx["confidence_state"] = "High Confidence"
            tx["client_confirmed"] = True
            tx["confidence_score"] = 1.0
        _log(correction_log, q, len(matches), matches[0]["category"], matches[0]["category"],
             "Client confirmed business purpose.", True)
    elif answer == "Personal":
        old_cat = matches[0]["category"]
        for tx in matches:
            tx["category"] = PERSONAL_CONFIRMED_CATEGORY
            tx["confidence_state"] = "High Confidence"
            tx["client_confirmed"] = True
            tx["confidence_score"] = 1.0
            tx["original_category"] = tx.get("original_category", "") or old_cat
        _log(correction_log, q, len(matches), old_cat, PERSONAL_CONFIRMED_CATEGORY,
             "Client confirmed this was a personal expense -- excluded from "
             "Schedule C entirely.", True)
    elif answer == "Owner Draw":
        old_cat = matches[0]["category"]
        for tx in matches:
            tx["category"] = OWNER_DRAW_CATEGORY
            tx["confidence_state"] = "High Confidence"
            tx["client_confirmed"] = True
            tx["confidence_score"] = 1.0
            tx["original_category"] = tx.get("original_category", "") or old_cat
        _log(correction_log, q, len(matches), old_cat, OWNER_DRAW_CATEGORY,
             "Client confirmed this was the owner drawing funds from the "
             "business -- excluded from Schedule C entirely.", True)
    # "Unclear" or any other value: leave the exception open, no change.


def _apply_asset_purchase(q, answer, by_key, correction_log):
    matches = _matching_transactions(q, by_key)
    if not matches:
        _log(correction_log, q, 0, None, None,
             "No matching transaction found -- the extraction that produced "
             "this question may have changed since it was generated.", False)
        return

    old_cat = matches[0]["category"]
    if answer == "Confirmed Asset":
        for tx in matches:
            tx["category"] = ASSET_CONFIRMED_CATEGORY
            tx["confidence_state"] = "High Confidence"
            tx["client_confirmed"] = True
            tx["confidence_score"] = 1.0
        note = (
            "Client confirmed this is a fixed asset. Placed-in-service detail "
            f"from the client: \"{q.get('client_response', '').strip() or 'not provided'}\". "
            "This tool does not compute depreciation or a Section 179 election -- "
            "route to the depreciation schedule."
        )
        _log(correction_log, q, len(matches), old_cat, ASSET_CONFIRMED_CATEGORY, note, True)
    elif answer == "Not an Asset":
        for tx in matches:
            tx["confidence_state"] = "High Confidence"
            tx["client_confirmed"] = True
            tx["confidence_score"] = 1.0
        _log(correction_log, q, len(matches), old_cat, old_cat,
             "Client confirmed this is not a fixed asset; category unchanged.", True)
    # "Unclear": leave open.


def _apply_1099_verification(q, answer, correction_log):
    # No dollar amount changes -- this is a compliance note attached to the
    # contractor, not a recategorization of any transaction.
    correction_log.append({
        "item_id": q.get("item_id"),
        "question_category": q.get("category"),
        "payee": q.get("contractor") or q.get("payee"),
        "date": q.get("date"),
        "amount": q.get("amount"),
        "client_answer": answer,
        "client_response": q.get("client_response", ""),
        "matched_transactions": 0,
        "old_category": None,
        "new_category": None,
        "note": f"1099-NEC status for {q.get('contractor') or q.get('payee')}: {answer}.",
        "applied": True,
    })


def _apply_uncategorized_expense(q, answer, by_key, correction_log):
    matches = _matching_transactions(q, by_key)
    if not matches:
        _log(correction_log, q, 0, None, None,
             "No matching transaction found -- the extraction that produced "
             "this question may have changed since it was generated.", False)
        return

    old_cat = matches[0]["category"]
    if answer in _VALID_RECATEGORIZATION_TARGETS:
        for tx in matches:
            tx["category"] = answer
            tx["confidence_state"] = "High Confidence"
            tx["client_confirmed"] = True
            tx["confidence_score"] = 1.0
        _log(correction_log, q, len(matches), old_cat, answer,
             "Client-confirmed category.", True)
    else:
        # Never guess: an answer that isn't one of the tool's own recognized
        # categories is logged verbatim for the preparer to map by hand,
        # but the transaction itself is left untouched.
        _log(correction_log, q, len(matches), old_cat, None,
             f"Client's answer (\"{answer}\") is not a recognized Schedule C "
             "category -- needs manual mapping by the preparer.", False)
