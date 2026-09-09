from typing import Any, Dict, List

# Dollar tolerance for treating a reconciliation difference as clean. Statements
# round to the cent, so anything above this is a real discrepancy, not drift.
TOLERANCE = 0.01


class ReconciliationChecker:
    """Reconciles extracted transactions against statement-declared totals.

    Two independent checks per document:

    1. *Statement integrity* -- does the statement's own arithmetic hold?
       beginning balance + deposits - withdrawals == ending balance. A failure
       here means the balances were misread, not that transactions are missing.

    2. *Extraction completeness* -- do the transactions actually pulled out of
       the document add up to the totals the statement declares? This is the
       check that catches a regex parser silently dropping rows, and it is the
       reason the balances are captured at all.

    A document that declares no balances cannot be reconciled. That is reported
    as "Not Reconcilable", never as a pass -- an unverifiable statement is a
    finding a preparer needs to see.
    """

    def check_document(self, filename: str, statement_summary: Dict[str, Any],
                       transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Reconcile one parsed document. Returns a preparer-readable result."""
        beginning = statement_summary.get("beginning_balance") or 0.0
        ending = statement_summary.get("ending_balance") or 0.0
        declared_deposits = statement_summary.get("total_deposits") or 0.0
        # A bank statement's own "Withdrawals" figure routinely excludes Checks
        # and Fees, which it breaks out as their own summary totals (a real
        # statement's own arithmetic: Beginning + Deposits - Withdrawals -
        # Fees - Checks == Ending, exactly). Extraction captures all of it, so
        # the declared side needs to as well or a fully-correct extraction
        # gets flagged as a discrepancy.
        declared_withdrawals = (
            (statement_summary.get("total_withdrawals") or 0.0)
            + (statement_summary.get("total_checks") or 0.0)
            + (statement_summary.get("total_fees") or 0.0)
        )
        doc_type = statement_summary.get("document_type", "unknown")

        extracted_deposits = sum(
            tx["amount"] for tx in transactions if tx.get("is_deposit")
        )
        extracted_withdrawals = sum(
            abs(tx["amount"]) for tx in transactions if not tx.get("is_deposit")
        )

        result = {
            "source_file": filename,
            "document_type": doc_type,
            "transaction_count": len(transactions),
            "beginning_balance": beginning,
            "ending_balance": ending,
            "declared_deposits": declared_deposits,
            "declared_withdrawals": declared_withdrawals,
            "extracted_deposits": extracted_deposits,
            "extracted_withdrawals": extracted_withdrawals,
        }

        if not beginning and not ending and not declared_deposits and not declared_withdrawals:
            result.update({
                "status": "Not Reconcilable",
                "balance_difference": None,
                "deposit_difference": None,
                "withdrawal_difference": None,
                "notes": (
                    "The document declared no balances or rollup totals, so its "
                    "extraction cannot be verified. Confirm manually that every "
                    "transaction was captured."
                ),
            })
            return result

        # Check 1 -- the statement's own arithmetic.
        if doc_type == "credit_card":
            # On a card statement the balance grows with charges and shrinks
            # with payments, the reverse of a deposit account.
            expected_ending = beginning + declared_withdrawals - declared_deposits
        else:
            expected_ending = beginning + declared_deposits - declared_withdrawals

        balance_difference = round(expected_ending - ending, 2) if ending else None

        # Check 2 -- did we extract everything the statement says is there?
        deposit_difference = (
            round(declared_deposits - extracted_deposits, 2)
            if declared_deposits else None
        )
        withdrawal_difference = (
            round(declared_withdrawals - extracted_withdrawals, 2)
            if declared_withdrawals else None
        )

        notes = []
        failed = False

        if balance_difference is not None and abs(balance_difference) > TOLERANCE:
            failed = True
            notes.append(
                f"Statement balances do not foot: beginning + credits - debits "
                f"differs from the declared ending balance by "
                f"${balance_difference:,.2f}."
            )

        if deposit_difference is not None and abs(deposit_difference) > TOLERANCE:
            failed = True
            notes.append(
                f"${abs(deposit_difference):,.2f} of declared deposits was "
                f"{'not extracted' if deposit_difference > 0 else 'over-extracted'}. "
                f"Statement declares ${declared_deposits:,.2f}; "
                f"${extracted_deposits:,.2f} was captured."
            )

        if withdrawal_difference is not None and abs(withdrawal_difference) > TOLERANCE:
            failed = True
            notes.append(
                f"${abs(withdrawal_difference):,.2f} of declared withdrawals was "
                f"{'not extracted' if withdrawal_difference > 0 else 'over-extracted'}. "
                f"Statement declares ${declared_withdrawals:,.2f}; "
                f"${extracted_withdrawals:,.2f} was captured."
            )

        partial = (
            balance_difference is None
            and deposit_difference is None
            and withdrawal_difference is None
        )

        if failed:
            status = "Discrepancy"
        elif partial:
            status = "Not Reconcilable"
            notes.append(
                "The document declared some balance information but not enough "
                "to reconcile against. Verify manually."
            )
        else:
            status = "Reconciled"
            notes.append("Extracted transactions agree with the statement totals.")

        result.update({
            "status": status,
            "balance_difference": balance_difference,
            "deposit_difference": deposit_difference,
            "withdrawal_difference": withdrawal_difference,
            "notes": " ".join(notes),
        })
        return result

    def summarize(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Roll per-document results into a single dashboard indicator."""
        reconciled = [r for r in results if r["status"] == "Reconciled"]
        discrepancies = [r for r in results if r["status"] == "Discrepancy"]
        unverifiable = [r for r in results if r["status"] == "Not Reconcilable"]

        if discrepancies:
            status = "Discrepancy"
            message = (
                f"{len(discrepancies)} of {len(results)} document(s) do not "
                f"reconcile. Review before relying on the totals."
            )
        elif not results:
            status = "Not Reconcilable"
            message = "No documents were available to reconcile."
        elif unverifiable and not reconciled:
            status = "Not Reconcilable"
            message = (
                f"None of the {len(results)} document(s) declared balances that "
                f"could be reconciled against. Verify extraction manually."
            )
        elif unverifiable:
            status = "Partially Reconciled"
            message = (
                f"{len(reconciled)} of {len(results)} document(s) reconciled; "
                f"{len(unverifiable)} declared no usable balances."
            )
        else:
            status = "Reconciled"
            message = f"All {len(results)} document(s) reconciled to statement totals."

        return {
            "status": status,
            "message": message,
            "reconciled_count": len(reconciled),
            "discrepancy_count": len(discrepancies),
            "unverifiable_count": len(unverifiable),
            "total_documents": len(results),
            "results": results,
        }
