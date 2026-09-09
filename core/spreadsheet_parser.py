import pandas as pd
import re
from typing import List, Dict, Any
from core.tax_categorizer import TaxCategorizer

# Words that mark a row as income when the sheet has a single signed Amount
# column. Used both to detect the sheet's sign convention and to classify rows
# in sheets that carry no signs at all.
INCOME_ROW_KEYWORDS = [
    "deposit", "income", "revenue", "sales", "sale", "invoice", "receipts",
    "client payment", "customer payment", "payment received", "gross receipts",
    "fee income", "consulting income", "ingreso", "venta",
]


class SpreadsheetParser:
    """
    Parses client spreadsheets (Excel / CSV) and normalizes varied formats,
    including separate Debit/Credit columns, negative numbers, and custom column names.
    """

    def __init__(self, categorizer: TaxCategorizer = None):
        self.categorizer = categorizer or TaxCategorizer()

    def parse_spreadsheet(self, file_content_or_path, filename: str) -> Dict[str, Any]:
        """
        Reads CSV or Excel spreadsheet and extracts transactions with full diagnostic logging.
        """
        transactions = []
        diagnostics = []

        try:
            if filename.lower().endswith('.csv'):
                df_dict = {"Sheet1": pd.read_csv(file_content_or_path)}
            else:
                df_dict = pd.read_excel(file_content_or_path, sheet_name=None)

            for sheet_name, df in df_dict.items():
                if df.empty or len(df.columns) < 1:
                    continue

                raw_headers = list(df.columns)
                clean_cols = [str(c).strip().lower() for c in df.columns]
                df.columns = clean_cols

                # Detect key columns
                date_col = self._find_column(clean_cols, ['date', 'transaction date', 'dt', 'fecha', 'posted date'])
                payee_col = self._find_column(clean_cols, ['payee', 'vendor', 'name', 'description', 'memo', 'detalles', 'beneficiario', 'merchant'])
                cat_col = self._find_column(clean_cols, ['category', 'type', 'cuenta', 'clasificacion', 'account', 'categoria'])

                # Detect Amount OR separate Debit/Credit columns
                debit_col = self._find_column(clean_cols, ['debit', 'withdrawal', 'expense', 'out', 'egreso', 'gasto', 'charge', 'paid out'])
                credit_col = self._find_column(clean_cols, ['credit', 'deposit', 'income', 'in', 'ingreso', 'payment', 'paid in'])
                amount_col = self._find_column(clean_cols, ['amount', 'monto', 'total', 'net', 'sum', 'val', 'valor'])

                diagnostics.append({
                    "sheet": sheet_name,
                    "total_rows": len(df),
                    "detected_headers": raw_headers,
                    "matched_cols": {
                        "date": date_col,
                        "payee": payee_col,
                        "category": cat_col,
                        "amount": amount_col,
                        "debit": debit_col,
                        "credit": credit_col
                    }
                })

                sign_convention = self._detect_sign_convention(
                    df, amount_col, payee_col, cat_col
                )
                diagnostics[-1]["sign_convention"] = sign_convention

                for idx, row in df.iterrows():
                    date_val = str(row[date_col]).strip() if date_col and pd.notna(row[date_col]) else "2026-01-01"
                    
                    # Extract payee / description
                    payee_val = ""
                    if payee_col and pd.notna(row[payee_col]):
                        payee_val = str(row[payee_col]).strip()
                    elif cat_col and pd.notna(row[cat_col]):
                        payee_val = str(row[cat_col]).strip()
                    else:
                        payee_val = f"Line Item #{idx+1}"

                    orig_cat = str(row[cat_col]).strip() if cat_col and pd.notna(row[cat_col]) else "Spreadsheet Provided"

                    # Calculate Amount & Deposit/Expense Flag
                    amount = 0.0
                    is_deposit = False

                    # Check Separate Debit / Credit Columns first
                    if debit_col and pd.notna(row[debit_col]) and self._clean_number(row[debit_col]) != 0:
                        amount = -abs(self._clean_number(row[debit_col]))
                        is_deposit = False
                    elif credit_col and pd.notna(row[credit_col]) and self._clean_number(row[credit_col]) != 0:
                        amount = abs(self._clean_number(row[credit_col]))
                        is_deposit = True
                    elif amount_col and pd.notna(row[amount_col]):
                        raw_num = self._clean_number(row[amount_col])
                        row_text = f"{payee_val} {orig_cat}".lower()
                        is_income_kw = any(kw in row_text for kw in INCOME_ROW_KEYWORDS)

                        # Normalize to the convention used everywhere else in the
                        # tool: income positive, expenses negative.
                        if sign_convention == "inverted":
                            amount = -raw_num
                        elif sign_convention == "unsigned":
                            amount = abs(raw_num) if is_income_kw else -abs(raw_num)
                        else:  # "standard"
                            amount = raw_num

                        is_deposit = amount > 0

                    if amount == 0.0:
                        continue

                    # Map tax category
                    tax_cat, conf_state, conf_score = self.categorizer.categorize_transaction(
                        payee=payee_val,
                        description=f"{payee_val} (Orig: {orig_cat})",
                        amount=amount,
                        is_deposit=is_deposit
                    )

                    transactions.append({
                        "date": date_val,
                        "month": 1,
                        "payee": payee_val,
                        "description": payee_val,
                        "amount": amount,
                        "is_deposit": is_deposit,
                        "source_file": f"{filename} ({sheet_name})",
                        "category": tax_cat,
                        "confidence_state": conf_state,
                        "confidence_score": conf_score,
                        "original_category": orig_cat
                    })

        except Exception as e:
            diagnostics.append({"error": f"Error parsing spreadsheet {filename}: {str(e)}"})

        return {
            "filename": filename,
            "transactions": transactions,
            "transaction_count": len(transactions),
            "diagnostics": diagnostics
        }

    def _detect_sign_convention(self, df, amount_col, payee_col, cat_col) -> str:
        """Work out how a single Amount column encodes income vs. expense.

        Client sheets are inconsistent. Three conventions occur in practice:

        - "standard"  income positive, expenses negative.
        - "inverted"  expenses positive, income negative (a disbursements
                      register). Reading this as standard books revenue as an
                      expense, which moves net profit by twice the amount.
        - "unsigned"  every value positive; income vs. expense is carried only
                      by the row's own wording.

        Detection is per sheet and decided by how the rows that *look* like
        income are signed, since those are the rows the two conventions
        disagree about.
        """
        if not amount_col:
            return "standard"

        income_values = []
        other_values = []

        for _, row in df.iterrows():
            if amount_col not in row or pd.isna(row[amount_col]):
                continue
            val = self._clean_number(row[amount_col])
            if val == 0.0:
                continue

            parts = []
            for col in (payee_col, cat_col):
                if col and col in row and pd.notna(row[col]):
                    parts.append(str(row[col]))
            row_text = " ".join(parts).lower()

            if any(kw in row_text for kw in INCOME_ROW_KEYWORDS):
                income_values.append(val)
            else:
                other_values.append(val)

        all_values = income_values + other_values
        if not all_values:
            return "standard"

        # Checked first: if nothing anywhere is negative, the sheet is not
        # expressing direction numerically at all, whatever its income rows look
        # like. Reading it as "standard" would turn every expense into income.
        if all(v > 0 for v in all_values):
            return "unsigned"

        if income_values:
            negative_income = sum(1 for v in income_values if v < 0)
            if negative_income > len(income_values) / 2:
                return "inverted"

        return "standard"

    def _clean_number(self, val: Any) -> float:
        """Parses float from messy string (handling $, commas, parentheses for negatives)."""
        if pd.isna(val):
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        
        s = str(val).strip()
        is_neg = '(' in s or '-' in s or 'CR' in s.upper() or 'DR' in s.upper()
        clean = re.sub(r'[^\d.]', '', s)
        try:
            num = float(clean)
            return -num if is_neg else num
        except ValueError:
            return 0.0

    def _find_column(self, columns: List[str], targets: List[str]) -> str:
        for target in targets:
            for col in columns:
                if target in col:
                    return col
        return None
