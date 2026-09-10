from typing import Dict, Any, List

from core.transaction_utils import transaction_key
from core.vendor_grouping import group_potential_personal


class ClientQuestionGenerator:
    """
    Generates a client-friendly inquiry checklist based on identified exceptions.

    Every question carries a `transaction_keys` list (or, for the 1099
    aggregate questions, a `contractor` name) back to the transaction(s) it
    concerns, so an answered question can be applied to the workpaper by
    core.answer_applier without re-matching on fuzzy text later. Most
    questions carry exactly one key; an "Expense Verification" question can
    carry many, one per transaction its vendor group covers (see
    core/vendor_grouping.py) -- a client answers once per vendor, not once
    per charge.
    """

    def generate_question_list(self, exceptions_dict: Dict[str, Any], client_name: str = "Client",
                               materiality_threshold: float = 0.0) -> List[Dict[str, str]]:
        """
        Builds structured client questions from exception categories.

        materiality_threshold suppresses an Expense Verification question for
        a vendor group whose total dollar amount doesn't clear it -- no one
        needs to be asked about a single $3 charge, per the source of this
        idea (a real client meeting). The threshold applies to each group's
        total, not each individual charge, so several small charges to the
        same vendor that add up past the threshold still generate a question.
        """
        questions = []
        item_id = 1

        # 1. Questions on Potential Personal Expenses -- grouped by vendor so
        # a client answers once per recipient, not once per charge. On a real
        # engagement this collapsed 109 individual Cash App line items into 7
        # questions, one per person actually paid.
        vendor_groups = group_potential_personal(
            exceptions_dict.get("potential_personal", []),
            materiality_threshold=materiality_threshold,
        )
        for group in vendor_groups:
            count = group["count"]
            total = group["total_amount"]
            payee = group["payee"]
            if count == 1:
                question = (
                    f"We noticed a payment of ${total:,.2f} to '{payee}' on "
                    f"{group['date_range']}. Could you confirm if this was a "
                    f"100% business expense and describe its business purpose?"
                )
            else:
                question = (
                    f"We noticed {count} payments to '{payee}' between "
                    f"{group['date_range']} totaling ${total:,.2f}. Could you "
                    f"confirm if these were 100% business expenses and describe "
                    f"their business purpose? (If some were business and some "
                    f"personal, let us know which.)"
                )
            questions.append({
                "item_id": f"Q-{item_id:03d}",
                "category": "Expense Verification",
                "transaction_keys": group["transaction_keys"],
                "date": group["date_range"],
                "payee": payee,
                "count": count,
                "amount": f"${total:,.2f}",
                "question": question,
                "client_response": "",
                "answer": "",  # Business | Personal | Owner Draw | Unclear
            })
            item_id += 1

        # 2. Questions on Fixed Assets / Equipment
        for item in exceptions_dict.get("potential_fixed_assets", []):
            questions.append({
                "item_id": f"Q-{item_id:03d}",
                "category": "Asset Purchase",
                "transaction_keys": [transaction_key(item)],
                "date": item.get("date", "N/A"),
                "payee": item.get("payee", "N/A"),
                "count": 1,
                "amount": f"${abs(item.get('amount', 0.0)):,.2f}",
                "question": f"Purchase of ${abs(item.get('amount', 0.0)):,.2f} to '{item.get('payee')}' on {item.get('date')}. Please provide the item description, serial number/model if applicable, and date placed in service for tax depreciation.",
                "client_response": "",
                "answer": "",  # Confirmed Asset | Not an Asset | Unclear
            })
            item_id += 1

        # 3. Questions on Contract Labor 1099s
        for item in exceptions_dict.get("contract_labor_1099", []):
            questions.append({
                "item_id": f"Q-{item_id:03d}",
                "category": "Form 1099 Verification",
                "transaction_keys": [],
                "contractor": item.get("contractor"),
                "date": "Full Year 2026",
                "payee": item.get("contractor"),
                "count": 1,
                "amount": f"${item.get('total_paid'):,.2f}",
                "question": f"Total payments to contractor '{item.get('contractor')}' reached ${item.get('total_paid'):,.2f}. Did you file Form 1099-NEC for this contractor, or would you like us to prepare it?",
                "client_response": "",
                "answer": "",  # Filed | Will File | Not Required
            })
            item_id += 1

        # 4. Uncategorized High-Dollar Transactions
        for item in exceptions_dict.get("uncategorized", []):
            if abs(item.get("amount", 0.0)) > 200.0:
                questions.append({
                    "item_id": f"Q-{item_id:03d}",
                    "category": "Uncategorized Expense",
                    "transaction_keys": [transaction_key(item)],
                    "date": item.get("date", "N/A"),
                    "payee": item.get("payee", "N/A"),
                    "count": 1,
                    "amount": f"${abs(item.get('amount', 0.0)):,.2f}",
                    "question": f"Please clarify the business nature/category for the ${abs(item.get('amount', 0.0)):,.2f} payment to '{item.get('payee')}' on {item.get('date')}.",
                    "client_response": "",
                    "answer": "",  # a Schedule C category, chosen from the same list the tool itself uses
                })
                item_id += 1

        return questions
