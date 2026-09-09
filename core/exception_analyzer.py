from typing import List, Dict, Any

PERSONAL_EXPENSE_KEYWORDS = [
    "venmo", "cash app", "zelle to family", "netflix", "hulu", "spotify",
    "disney", "target", "walmart", "trader joe", "whole foods", "kroger",
    "safeway", "dry cleaner", "spa", "salon", "gym", "fitness", "cinema",
    "ticketmaster", "steam", "playstation", "nintendo"
]

CAPITAL_ASSET_KEYWORDS = [
    "macbook", "laptop", "desktop", "server", "iphone", "ipad",
    "machinery", "equipment", "vehicle", "truck", "trailer", "generator", "hvac"
]

DE_MINIMIS_THRESHOLD = 2500.0 # IRS De Minimis Safe Harbor Threshold

class ExceptionAnalyzer:
    """
    Scans processed transactions and populates exception review queues
    for preparer inspection in 100% English.
    """

    def analyze_exceptions(self, transactions: List[Dict[str, Any]],
                           de_minimis_threshold: float = DE_MINIMIS_THRESHOLD) -> Dict[str, Any]:
        """
        Categorizes transactions into exception review queues.

        de_minimis_threshold defaults to the IRS safe harbor but is overridable
        so the sidebar control actually takes effect.
        """
        uncategorized = []
        potential_personal = []
        potential_fixed_assets = []
        non_pnl_transfers = []
        contract_labor_1099 = []
        unusual_large_txs = []
        contractor_totals = {}

        for tx in transactions:
            payee_lower = tx.get("payee", "").lower()
            desc_lower = tx.get("description", "").lower()
            amt = abs(tx.get("amount", 0.0))
            category = tx.get("category", "")
            conf_state = tx.get("confidence_state", "")

            # 1. Uncategorized / Low Confidence Items
            if conf_state in ["Needs Review", "Unresolved"] or "Uncategorized" in category:
                uncategorized.append({
                    **tx,
                    "reason": "Low confidence classification. Requires preparer category assignment."
                })

            # 2. Potential Personal Expenses
            for kw in PERSONAL_EXPENSE_KEYWORDS:
                if kw in payee_lower or kw in desc_lower:
                    potential_personal.append({
                        **tx,
                        "reason": f"Matched personal keyword '{kw}'. Verify business purpose."
                    })
                    break

            # 3. Potential Fixed Assets / Capital Expenditures (> $2,500 threshold or asset keywords)
            is_asset_kw = any(kw in payee_lower or kw in desc_lower for kw in CAPITAL_ASSET_KEYWORDS)
            if (amt >= de_minimis_threshold or is_asset_kw) and not tx.get("is_deposit"):
                potential_fixed_assets.append({
                    **tx,
                    "reason": f"Amount ${amt:,.2f} exceeds ${de_minimis_threshold:,.0f} threshold or contains capital equipment keywords. Evaluate Section 179 / Depreciation."
                })

            # 4. Non-P&L Transfers & Credit Card Payments
            if category.startswith("Non-P&L:"):
                non_pnl_transfers.append({
                    **tx,
                    "reason": f"Excluded from P&L as {category}."
                })

            # 5. Unusual / Large Transactions (> $5,000)
            if amt >= 5000.0 and not category.startswith("Non-P&L:"):
                unusual_large_txs.append({
                    **tx,
                    "reason": f"Large transaction amount ${amt:,.2f}. Review for materiality."
                })

            # 6. Contract Labor 1099 Accumulator
            if "Contract labor" in category and not tx.get("is_deposit"):
                payee = tx.get("payee", "Unknown Contractor")
                contractor_totals[payee] = contractor_totals.get(payee, 0.0) + amt

        # Flag contractors over $600
        for contractor, total in contractor_totals.items():
            if total >= 600.0:
                contract_labor_1099.append({
                    "contractor": contractor,
                    "total_paid": total,
                    "reason": f"Total paid ${total:,.2f} is \u2265 $600. Verify Form 1099-NEC filing status."
                })

        total_exception_count = (
            len(uncategorized) + len(potential_personal) +
            len(potential_fixed_assets) + len(non_pnl_transfers) + 
            len(contract_labor_1099) + len(unusual_large_txs)
        )

        return {
            "total_exception_count": total_exception_count,
            "uncategorized": uncategorized,
            "potential_personal": potential_personal,
            "potential_fixed_assets": potential_fixed_assets,
            "non_pnl_transfers": non_pnl_transfers,
            "contract_labor_1099": contract_labor_1099,
            "unusual_large_txs": unusual_large_txs
        }
