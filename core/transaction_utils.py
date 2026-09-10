"""Shared helpers for identifying a transaction across the pieces of the
pipeline that need to refer back to one (question generation, applying a
client's answer to a question, the audit trail of what changed and why).

There is no database and no transaction id assigned at extraction time --
the app is explicitly single-session with no state persisted between runs
(see README: "One client = one session"). A transaction is identified by the
same fields an auditor would use to find it on the source document: date,
payee, amount, and source file. That tuple is what this module turns into a
stable string key.
"""
from typing import Any, Dict


def transaction_key(tx: Dict[str, Any]) -> str:
    """A stable, human-traceable identity for one transaction.

    Two genuinely distinct transactions sharing this key (same date, payee,
    amount, and source file) are rare enough on real statements that treating
    them as one unit -- a client's answer about one applies to all of them --
    is the safer default over inventing a synthetic id that would silently
    break the moment the transaction list is regenerated from scratch.
    """
    return "|".join([
        str(tx.get("date", "")),
        str(tx.get("payee", "")),
        f"{tx.get('amount', 0.0):.2f}",
        str(tx.get("source_file", "")),
    ])
