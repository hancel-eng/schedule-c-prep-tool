"""Groups repeated charges from the same vendor/recipient into one question
instead of one per transaction, and skips a question entirely when the
dollar amount involved isn't worth a client's time to answer.

Raised in a client meeting (Hancel): a real engagement produced 109 separate
"was this Cash App payment business or personal?" questions that turned out
to be only 7 distinct recipients -- asking about each individually is both
worse for the client (109 near-identical questions to answer) and worse for
the preparer (109 rows to read the answer to) than one question per
recipient with the total and count attached.
"""
import re
from typing import Any, Dict, List

from core.transaction_utils import transaction_key

# P2P payment apps route money to an individual, and the statement descriptor
# names them (e.g. "Card Purchase Cash App*dakota 4829 Oakland CA 94612") --
# grouping by the raw cleaned payee would collapse "Cash App*dakota" and
# "Cash App*moe" into one "cash app" group, hiding that they are different
# people. Extracted separately so the group key stays person-specific.
_P2P_APP_PATTERN = re.compile(
    r'\b(cash\s*app|venmo|zelle|paypal)\b\W*\*?\s*([a-z][a-z0-9]*)', re.IGNORECASE
)


def vendor_group_key(payee: str) -> str:
    """A stable grouping identity for a payee string.

    For a P2P app payment, the service + recipient ("cash app|dakota").
    Otherwise, the first three words of the payee with digits and store
    codes stripped -- close enough to collapse "Circle K # 0768 North Port"
    and "Circle K # 0720 Nokomis" into one vendor without needing a full
    fuzzy-matching pass.
    """
    text = str(payee or "").strip()
    m = _P2P_APP_PATTERN.search(text)
    if m:
        service = re.sub(r'\s+', ' ', m.group(1)).strip().lower()
        return f"{service}|{m.group(2).lower()}"

    # Two words, not three: a third word is frequently the city ("Circle K
    # North Port" vs. "Circle K Nokomis"), which would keep two locations of
    # the same chain from grouping together.
    cleaned = re.sub(r'#?\d+', '', text.lower())
    cleaned = re.sub(r'[^a-z\s]', ' ', cleaned)
    words = re.sub(r'\s+', ' ', cleaned).strip().split()
    return " ".join(words[:2]) if words else text.lower()


def group_potential_personal(items: List[Dict[str, Any]],
                             materiality_threshold: float = 0.0) -> List[Dict[str, Any]]:
    """Groups potential-personal-expense exception items by vendor.

    Returns one entry per vendor group with the transaction keys of every
    member, a representative display payee, transaction count, total dollar
    amount, and the date range covered. A group whose total does not exceed
    materiality_threshold is dropped entirely -- per Hancel's example, no one
    needs a client question about a single $3 charge, but ten $3 charges to
    the same vendor summing past the threshold still deserve one.
    """
    groups: Dict[str, Dict[str, Any]] = {}

    for item in items:
        key = vendor_group_key(item.get("payee", ""))
        group = groups.setdefault(key, {
            "group_key": key,
            "payee": item.get("payee", ""),
            "transaction_keys": [],
            "count": 0,
            "total_amount": 0.0,
            "dates": [],
            "reason": item.get("reason", ""),
        })
        group["transaction_keys"].append(transaction_key(item))
        group["count"] += 1
        group["total_amount"] += abs(item.get("amount", 0.0))
        if item.get("date"):
            group["dates"].append(item["date"])
        # Prefer the shortest/cleanest payee text seen as the display label
        # (statements often repeat the same vendor with varying trailing
        # noise -- city, store number -- across transactions).
        if len(item.get("payee", "")) < len(group["payee"]):
            group["payee"] = item.get("payee", "")

    result = []
    for group in groups.values():
        if group["total_amount"] <= materiality_threshold:
            continue
        dates = sorted(group["dates"])
        group["date_range"] = (
            dates[0] if len(dates) == 1 or not dates
            else f"{dates[0]} – {dates[-1]}"
        )
        del group["dates"]
        result.append(group)

    result.sort(key=lambda g: -g["total_amount"])
    return result
