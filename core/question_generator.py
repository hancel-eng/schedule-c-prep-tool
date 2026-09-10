from typing import Dict, Any, List

from core.transaction_utils import transaction_key


class ClientQuestionGenerator:
    """
    Generates a client-friendly inquiry checklist based on identified exceptions.

    Every question carries a `transaction_key` (or, for the 1099 aggregate
    questions, a `contractor` name) back to the transaction(s) it concerns, so
    an answered question can be applied to the workpaper by
    core.answer_applier without re-matching on fuzzy text later.
    """

    def generate_question_list(self, exceptions_dict: Dict[str, Any], client_name: str = "Client") -> List[Dict[str, str]]:
        """
        Builds structured client questions from exception categories.
        """
        questions = []
        item_id = 1

        # 1. Questions on Potential Personal Expenses
        for item in exceptions_dict.get("potential_personal", []):
            questions.append({
                "item_id": f"Q-{item_id:03d}",
                "category": "Expense Verification",
                "transaction_key": transaction_key(item),
                "date": item.get("date", "N/A"),
                "payee": item.get("payee", "N/A"),
                "amount": f"${abs(item.get('amount', 0.0)):,.2f}",
                "question": f"We noticed a payment of ${abs(item.get('amount', 0.0)):,.2f} to '{item.get('payee')}' on {item.get('date')}. Could you confirm if this was a 100% business expense and describe its business purpose?",
                "client_response": "",
                "answer": "",  # Business | Personal | Unclear
            })
            item_id += 1

        # 2. Questions on Fixed Assets / Equipment
        for item in exceptions_dict.get("potential_fixed_assets", []):
            questions.append({
                "item_id": f"Q-{item_id:03d}",
                "category": "Asset Purchase",
                "transaction_key": transaction_key(item),
                "date": item.get("date", "N/A"),
                "payee": item.get("payee", "N/A"),
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
                "transaction_key": None,
                "contractor": item.get("contractor"),
                "date": "Full Year 2026",
                "payee": item.get("contractor"),
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
                    "transaction_key": transaction_key(item),
                    "date": item.get("date", "N/A"),
                    "payee": item.get("payee", "N/A"),
                    "amount": f"${abs(item.get('amount', 0.0)):,.2f}",
                    "question": f"Please clarify the business nature/category for the ${abs(item.get('amount', 0.0)):,.2f} payment to '{item.get('payee')}' on {item.get('date')}.",
                    "client_response": "",
                    "answer": "",  # a Schedule C category, chosen from the same list the tool itself uses
                })
                item_id += 1

        return questions
