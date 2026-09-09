from typing import Dict, Any, List

class TotalsParser:
    """
    Parses client-provided year-end summary totals in 100% English and flags items
    requiring specialized tax treatment or additional preparer review.
    """

    def process_totals(self, totals_dict: Dict[str, float]) -> Dict[str, Any]:
        """
        Maps year-end totals into Schedule C categories and flags special tax rules.
        """
        categorized_totals = {}
        flags = []

        for item_name, amount in totals_dict.items():
            name_clean = item_name.strip().lower()
            
            # Special Tax Rule Checkers
            if "vehicle" in name_clean or "auto" in name_clean or "gas" in name_clean or "mileage" in name_clean:
                categorized_totals["Line 9: Car and truck expenses"] = amount
                flags.append({
                    "category": "Line 9: Car and truck expenses",
                    "issue": "Auto/Vehicle Expenses Present",
                    "action_needed": "Determine actual expenses vs. standard mileage rate ($0.67/mi). Confirm written vehicle log is available.",
                    "severity": "Needs Review"
                })

            elif "meal" in name_clean or "food" in name_clean or "dining" in name_clean:
                categorized_totals["Line 24b: Deductible meals (50%)"] = amount
                flags.append({
                    "category": "Line 24b: Deductible meals (50%)",
                    "issue": "Meals Subject to 50% Limitation",
                    "action_needed": f"Provided total is ${amount:,.2f}. Net 50% deductible portion is ${amount * 0.5:,.2f}.",
                    "severity": "Informational"
                })

            elif "contract" in name_clean or "subcontractor" in name_clean or "1099" in name_clean:
                categorized_totals["Line 11: Contract labor"] = amount
                flags.append({
                    "category": "Line 11: Contract labor",
                    "issue": "1099 Filing Requirement Review",
                    "action_needed": "Ensure Form 1099-NEC was filed for any contractor paid $600 or more during the tax year.",
                    "severity": "Needs Review"
                })

            elif "equipment" in name_clean or "computer" in name_clean or "asset" in name_clean or "machinery" in name_clean:
                categorized_totals["Line 13: Depreciation and section 179"] = amount
                flags.append({
                    "category": "Line 13: Depreciation and section 179",
                    "issue": "Potential Fixed Capital Asset",
                    "action_needed": "Review whether item should be capitalized & depreciated (or Section 179 expensed) instead of expensed as supplies.",
                    "severity": "Needs Review"
                })

            elif "insurance" in name_clean:
                if "health" in name_clean or "medical" in name_clean:
                    flags.append({
                        "category": "Insurance",
                        "issue": "Potential Personal Health Insurance",
                        "action_needed": "Health insurance for sole proprietors is generally deducted on Form 1040 Schedule 1 (Line 17), NOT on Schedule C.",
                        "severity": "Action Required"
                    })
                else:
                    categorized_totals["Line 15: Insurance (other than health)"] = amount

            elif "tax" in name_clean or "license" in name_clean:
                categorized_totals["Line 23: Taxes and licenses"] = amount
                if "sales tax" in name_clean or "income tax" in name_clean:
                    flags.append({
                        "category": "Line 23: Taxes and licenses",
                        "issue": "Non-Deductible Tax Included",
                        "action_needed": "Federal income taxes and collected sales taxes are NOT deductible expenses on Schedule C.",
                        "severity": "Needs Review"
                    })

            elif "home office" in name_clean or "rent home" in name_clean:
                flags.append({
                    "category": "Home Office",
                    "issue": "Home Office Expense Flagged",
                    "action_needed": "Calculate home office deduction using Form 8829 (actual expenses) or Simplified Method ($5/sq ft up to 300 sq ft).",
                    "severity": "Informational"
                })

            else:
                cat = "Line 18: Office expense" if "office" in name_clean else "Line 27a: Other expenses"
                categorized_totals[cat] = categorized_totals.get(cat, 0.0) + amount

        return {
            "mapped_totals": categorized_totals,
            "special_tax_flags": flags
        }
