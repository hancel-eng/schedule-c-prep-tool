from typing import Dict, Any, List

class ClientQuestionGenerator:
    """
    Generates a client-friendly inquiry checklist based on identified exceptions.
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
                "date": item.get("date", "N/A"),
                "payee": item.get("payee", "N/A"),
                "amount": f"${abs(item.get('amount', 0.0)):,.2f}",
                "question": f"We noticed a payment of ${abs(item.get('amount', 0.0)):,.2f} to '{item.get('payee')}' on {item.get('date')}. Could you confirm if this was a 100% business expense and describe its business purpose?",
                "client_response": ""
            })
            item_id += 1

        # 2. Questions on Fixed Assets / Equipment
        for item in exceptions_dict.get("potential_fixed_assets", []):
            questions.append({
                "item_id": f"Q-{item_id:03d}",
                "category": "Asset Purchase",
                "date": item.get("date", "N/A"),
                "payee": item.get("payee", "N/A"),
                "amount": f"${abs(item.get('amount', 0.0)):,.2f}",
                "question": f"Purchase of ${abs(item.get('amount', 0.0)):,.2f} to '{item.get('payee')}' on {item.get('date')}. Please provide the item description, serial number/model if applicable, and date placed in service for tax depreciation.",
                "client_response": ""
            })
            item_id += 1

        # 3. Questions on Contract Labor 1099s
        for item in exceptions_dict.get("contract_labor_1099", []):
            questions.append({
                "item_id": f"Q-{item_id:03d}",
                "category": "Form 1099 Verification",
                "date": "Full Year 2026",
                "payee": item.get("contractor"),
                "amount": f"${item.get('total_paid'):,.2f}",
                "question": f"Total payments to contractor '{item.get('contractor')}' reached ${item.get('total_paid'):,.2f}. Did you file Form 1099-NEC for this contractor, or would you like us to prepare it?",
                "client_response": ""
            })
            item_id += 1

        # 4. Uncategorized High-Dollar Transactions
        for item in exceptions_dict.get("uncategorized", []):
            if abs(item.get("amount", 0.0)) > 200.0:
                questions.append({
                    "item_id": f"Q-{item_id:03d}",
                    "category": "Uncategorized Expense",
                    "date": item.get("date", "N/A"),
                    "payee": item.get("payee", "N/A"),
                    "amount": f"${abs(item.get('amount', 0.0)):,.2f}",
                    "question": f"Please clarify the business nature/category for the ${abs(item.get('amount', 0.0)):,.2f} payment to '{item.get('payee')}' on {item.get('date')}.",
                    "client_response": ""
                })
                item_id += 1

        return questions
