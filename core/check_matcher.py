"""Fills in the payee on a "Check #1234" / "Check (no number recorded)"
transaction (already extracted from a bank statement's cleared-checks
listing -- see core/pdf_parser.py) using a standalone check-image
extraction (core/idp_extractor.py, structured_data.check) uploaded
separately. Raised directly by the client: "las copias de cheques son
necesarias, porque cuando el cliente paga con cheque hay que saber quién
fue el proveedor" -- a statement's own check listing never prints a payee,
only a check number and amount.

Matching is by check_number first (exact, digits-only), amount as a
tie-breaker / fallback when the number is missing or ambiguous. Never
guesses: a check record that can't be matched with confidence is left
unmatched and reported back, not silently attached to the nearest
plausible transaction.
"""
import re
from typing import Any, Dict, List, Tuple

_CHECK_NUMBER_IN_PAYEE = re.compile(r"check\s*#?\s*(\d+)", re.IGNORECASE)
# Broader than _CHECK_NUMBER_IN_PAYEE on purpose: a cleared-check row with no
# recorded number still starts with "Check" (see core/pdf_parser.py's
# "Check (no number recorded)" label) and must still be a match candidate --
# just one that can only be matched by amount, never by number.
_IS_CHECK_PAYEE = re.compile(r"^check\b", re.IGNORECASE)


def _digits_only(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def match_checks_to_transactions(check_records: List[Dict[str, Any]],
                                  transactions: List[Dict[str, Any]],
                                  tolerance: float = 0.01) -> Tuple[int, List[Dict[str, Any]]]:
    """Mutates `transactions` in place, setting payee/description to the
    check's payee wherever a confident match is found. Returns
    (matched_count, unmatched_check_records) -- the caller surfaces
    unmatched records rather than them silently going nowhere.

    A transaction already carrying a real payee from a prior match is
    skipped (first confident match wins; running this twice over the same
    transactions is safe)."""
    check_txs = [
        tx for tx in transactions
        if not tx.get("is_deposit") and _IS_CHECK_PAYEE.search(str(tx.get("payee", "")).strip())
    ]
    matched_count = 0
    unmatched: List[Dict[str, Any]] = []

    for check in check_records:
        check_number = _digits_only(check.get("check_number"))
        amount = check.get("numerical_amount")
        candidate = None

        if check_number:
            for tx in check_txs:
                tx_number = _CHECK_NUMBER_IN_PAYEE.search(str(tx.get("payee", "")))
                if tx_number and tx_number.group(1) == check_number:
                    candidate = tx
                    break

        if candidate is None and amount is not None:
            # Only treat amount as a safe tie-breaker when it uniquely
            # identifies one still-unlabeled check transaction -- two
            # checks for the same amount in the same statement is common
            # enough (rent, recurring vendor payments) that guessing would
            # be worse than leaving both unmatched.
            same_amount = [tx for tx in check_txs if abs(abs(tx.get("amount", 0.0)) - float(amount)) <= tolerance]
            if len(same_amount) == 1:
                candidate = same_amount[0]

        if candidate is not None and check.get("payee"):
            candidate["payee"] = check["payee"]
            candidate["description"] = check["payee"]
            candidate["original_category"] = (candidate.get("original_category", "") +
                                               " + Check Copy Match").strip(" +")
            matched_count += 1
        else:
            unmatched.append(check)

    return matched_count, unmatched
