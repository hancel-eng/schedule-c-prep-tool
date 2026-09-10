import re
import os
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

from core.tax_categorizer import TaxCategorizer

# Strict Pydantic Data Models
class TransactionItem(BaseModel):
    date: str
    post_date: Optional[str] = None
    payee: str
    description: str
    amount: float
    is_deposit: bool
    category: str
    confidence_state: str
    confidence_score: float
    source_file: str
    original_category: str = "Extracted PDF Document"

class FinancialDocumentData(BaseModel):
    statement_type: str = "general_pdf"
    document_type: str = "general_pdf"
    bank_name: str = "Universal PDF Ingester"
    bank_or_vendor: str = "Universal PDF Ingester"
    account_number: Optional[str] = None
    statement_period: Optional[str] = None
    payment_due_date: Optional[str] = None
    new_balance: Optional[float] = 0.0
    minimum_payment: Optional[float] = 0.0
    beginning_balance: Optional[float] = 0.0
    ending_balance: Optional[float] = 0.0
    total_deposits: Optional[float] = 0.0
    total_withdrawals: Optional[float] = 0.0
    total_checks: Optional[float] = 0.0
    total_fees: Optional[float] = 0.0
    diagnostics_notes: List[str] = Field(default_factory=list)
    transactions: List[TransactionItem] = Field(default_factory=list)

# Backward Compatibility Aliases for Pydantic Models
CreditCardStatementData = FinancialDocumentData
BankStatementData = FinancialDocumentData

# Statement header/footer lines to skip. Deliberately excludes bare "finance
# charge" and "interest charge": on a credit card statement those are genuine
# Line 16b interest expense transactions, not summary rows. Only the "total ..."
# rollups below are treated as summary.
SUMMARY_TERMS = [
    "available credit", "credit limit", "previous balance", "new balance",
    "payment due date", "minimum payment", "statement period", "account summary",
    "daily balance summary", "total fees charged", "total interest charged",
    "total finance charge", "rewards summary", "cash advance credit limit",
    "past due amount", "ending balance", "beginning balance", "total deposits",
    "total withdrawals", "payments and credits", "transactions fees",
    "total transactions for"
]

# Summary lines that need anchoring so they cannot match a vendor description
# (a plain "page " substring matched payees such as "PAGEANT SUPPLY CO").
SUMMARY_REGEXES = [
    r"^page\s+\d+",
    r"\bpage\s+\d+\s+of\s+\d+\b",
    r"^\s*total\b",
    r"^\s*subtotal\b",
]

# Statement balance labels. These lines are skipped as transactions (they are in
# SUMMARY_TERMS) but their amounts are captured so the workpaper can reconcile
# what was extracted against what the statement itself declares -- the strongest
# check that the regex parsers did not silently drop transactions.
BALANCE_PATTERNS = {
    "beginning_balance": [
        r"\b(?:beginning|previous|opening|prior)\s+(?:statement\s+)?balance\b",
        r"\bbalance\s+(?:forward|last\s+statement)\b",
    ],
    "ending_balance": [
        r"\b(?:ending|new|closing|current)\s+balance\b",
        r"\bbalance\s+this\s+statement\b",
    ],
    "total_deposits": [
        r"\btotal\s+(?:deposits|credits|additions)\b",
        r"\bdeposits?\s+(?:and|&)\s+(?:credits|additions)\b",
        r"\bpayments?\s+(?:and|&)\s+credits\b",
        r"\btotal\s+payments?\s+(?:and|&)\s+credits\b",
    ],
    "total_withdrawals": [
        r"\btotal\s+(?:withdrawals|debits|subtractions)\b",
        r"\bwithdrawals?\s+(?:and|&)\s+debits\b",
        r"\b(?:purchases|charges)\s+(?:and|&)\s+(?:adjustments|debits)\b",
        r"\btotal\s+(?:purchases|charges)\b",
    ],
    # A bank statement's "Withdrawals" summary figure routinely excludes
    # Checks and Fees -- both broken out as their own SUMMARY-block totals
    # (confirmed against a real statement: Beginning + Deposits - Withdrawals
    # - Fees - Checks == Ending, exactly). Reconciliation needs these added to
    # "total_withdrawals" or it flags a real, fully-extracted statement as a
    # discrepancy. Anchored to the start of the line so this only matches the
    # bare "Checks $5,130.19 -" / "Fees $8.00 -" summary line, never a section
    # header or an unrelated sentence that happens to contain the word.
    "total_checks": [
        r"^checks\b",
        r"\btotal\s+checks\b",
    ],
    "total_fees": [
        r"^fees\b",
        r"\btotal\s+fees\b",
    ],
}

DEPOSIT_KEYWORDS = [
    "deposit", "dir dep", "direct deposit", "wire recv", "credit refund", "merchant deposit", "square inc", "stripe deposit", "gross sales", "revenue"
]

WITHDRAWAL_KEYWORDS = [
    "withdrawal", "debit card", "check #", "chk", "fee", "service charge", "payment to", "online pymt", "ach debit",
    # "Card Purchase", "PIN Purchase" and "Recurring Card Transaction" are the
    # actual line prefixes a real Regions statement uses for the vast majority
    # of its withdrawals. Without these, every one of those lines fell through
    # to the section-tracking fallback below -- and see the note on the
    # "WITHDRAWALS" section-header match for why that fallback was itself
    # broken on this exact bank layout, so this list is real defense-in-depth,
    # not a redundant safety net.
    "card purchase", "pin purchase", "recurring card transaction", "ach debit",
    # "Returned Deposit Item" (a bounced check reversal) contains the literal
    # word "deposit", which DEPOSIT_KEYWORDS below would otherwise match --
    # and on a real statement this line sits inside the WITHDRAWALS section,
    # correctly reducing the balance, not adding to it. Checked before
    # DEPOSIT_KEYWORDS in the classification order, so this wins regardless
    # of which section the line happens to be in.
    "returned deposit", "return item",
]

class BankPDFParser:
    """
    Universal PDF Financial Document Ingester.
    Handles digital bank statements, credit cards, P&L reports, invoice images,
    check photos, and scanned receipt PDFs using a 3-strategy multi-pipeline engine.
    """

    def __init__(self, categorizer: TaxCategorizer = None):
        self.categorizer = categorizer or TaxCategorizer()

    def parse_pdf(self, file_path_or_bytes, filename: str) -> Dict[str, Any]:
        """
        Universal multi-strategy parsing pipeline for any PDF structure.
        """
        statement_year = self._extract_year_from_filename(filename) or 2025
        statement_end_month = self._extract_statement_end_month_from_filename(filename)
        doc_data = FinancialDocumentData(bank_or_vendor=filename)

        # Detect Document Category
        is_pnl = "p & l" in filename.lower() or "pnl" in filename.lower() or "profit" in filename.lower()
        is_credit_card = any(kw in filename.lower() for kw in ["capital one", "spark", "card", "chase card", "amex", "citi"])

        if is_pnl:
            doc_data = self._parse_pnl_report(file_path_or_bytes, filename, statement_year)
        elif is_credit_card:
            doc_data = self._parse_credit_card_pdf(file_path_or_bytes, filename, statement_year, statement_end_month)
        else:
            doc_data = self._parse_general_or_scanned_pdf(file_path_or_bytes, filename, statement_year, statement_end_month)

        tx_dicts = [tx.model_dump() for tx in doc_data.transactions]
        months_found = list(set([self._extract_month(tx['date']) for tx in tx_dicts if tx.get('date')]))

        return {
            "filename": filename,
            "statement_summary": doc_data.model_dump(exclude={"transactions"}),
            "transactions": tx_dicts,
            "transaction_count": len(tx_dicts),
            "months_found": months_found,
            "diagnostics": [
                f"Parsed {doc_data.bank_or_vendor} ({doc_data.document_type})",
                f"Extracted {len(tx_dicts)} items cleanly via Universal Strategy Pipeline."
            ] + list(doc_data.diagnostics_notes)
        }

    # -------------------------------------------------------------------------
    # STRATEGY 1: P&L REPORT / FINANCIAL SUMMARY PARSER (e.g. TD Tree 2025 P & L.pdf)
    # -------------------------------------------------------------------------
    # Section headers that carry no trailing dollar amount of their own. Every
    # line between one of these and the next is a detail or subtotal line that
    # inherits this section's sign. QuickBooks-style P&L exports use this exact
    # header vocabulary; matched case-insensitively against the whole line.
    PNL_INCOME_SECTION_HEADERS = {"income", "other income"}
    PNL_EXPENSE_SECTION_HEADERS = {"cost of goods sold", "expense", "other expense"}
    PNL_NEUTRAL_HEADERS = {"ordinary income/expense", "other income/expense"}

    # Lines that restate a sum of the detail lines already counted above them.
    # Counting these too would double (or triple) the same dollars. Matched as
    # a prefix/whole-line check against the lowercased, stripped line.
    PNL_SUBTOTAL_PREFIXES = ("total ", "gross profit", "net ordinary income",
                             "net other income", "net income", "net loss")

    def _parse_pnl_report(self, file_source, filename: str, year: int) -> FinancialDocumentData:
        """Parses a QuickBooks-style Profit & Loss export.

        These reports are frequently comparative: two amount columns, one per
        year (e.g. "Jan - Dec 25   Jan - Dec 24"). Getting this parser right
        requires three things a naive "grab the trailing number" approach gets
        wrong, each of which silently inflated totals when tested against a
        real two-column report:

        1. Pick the column for the requested tax year, not just whichever
           number happens to be printed last on the line.
        2. Skip subtotal/derived lines (Total Income, Net Ordinary Income,
           Gross Profit, ...) entirely -- they restate detail lines already
           counted, so including them double- or triple-counts the same
           dollars.
        3. Preserve the sign QuickBooks printed (a loss stays a loss) instead
           of forcing every "income"-labeled line positive.
        """
        data = FinancialDocumentData(document_type="pnl_report", bank_or_vendor="P&L Summary Report", statement_type="pnl_report", bank_name="P&L Report")
        transactions = []
        diagnostics_notes = []

        if not pdfplumber:
            return data

        money_token = re.compile(r'^\(?[+-]?\$?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?$')

        try:
            with pdfplumber.open(file_source) as pdf:
                full_text = "\n".join(self._extract_page_text(page) for page in pdf.pages)
        except Exception as e:
            print(f"Error parsing P&L PDF {filename}: {e}")
            data.transactions = transactions
            return data

        lines = full_text.split("\n")

        # Locate the column header ("Jan - Dec 25   Jan - Dec 24") and work out
        # which column (0-indexed) holds the requested tax year. A header can
        # repeat once per page in a multi-page export; the first one found
        # sets the column for the whole document.
        column_index = 0
        column_count = 1
        header_found = False
        for line in lines:
            years_in_line = re.findall(r'\b(20\d{2}|\d{2})\b', line)
            if len(years_in_line) < 2:
                continue
            if not any(k in line.lower() for k in ["jan", "dec", "-", "through", "accrual", "cash basis"]):
                continue
            normalized_years = [int(y) if len(y) == 4 else 2000 + int(y) for y in years_in_line]
            if year in normalized_years:
                column_index = normalized_years.index(year)
                column_count = len(normalized_years)
                header_found = True
                diagnostics_notes.append(
                    f"Comparative P&L detected ({len(normalized_years)} year columns); "
                    f"using column {column_index + 1} for tax year {year}."
                )
                break
            elif len(normalized_years) > 1:
                # A comparative header exists but doesn't name the requested
                # year -- use its first column and say so plainly rather than
                # silently guessing.
                column_index = 0
                column_count = len(normalized_years)
                header_found = True
                diagnostics_notes.append(
                    f"WARNING: comparative P&L header found ({normalized_years}) but "
                    f"tax year {year} is not one of them. Defaulting to the first "
                    f"column ({normalized_years[0]}) -- verify this is correct."
                )
                break

        if not header_found:
            diagnostics_notes.append(
                "No comparative year header found; treating as a single-column P&L."
            )

        current_section = None  # "income" | "expense" | None (unknown / neutral)

        for raw_line in lines:
            clean_line = raw_line.strip()
            if not clean_line:
                continue
            lower = clean_line.lower()

            if lower in self.PNL_INCOME_SECTION_HEADERS:
                current_section = "income"
                continue
            if lower in self.PNL_EXPENSE_SECTION_HEADERS:
                current_section = "expense"
                continue
            if lower in self.PNL_NEUTRAL_HEADERS:
                continue

            if lower.startswith(self.PNL_SUBTOTAL_PREFIXES) or lower.startswith("total"):
                continue

            tokens = clean_line.split()
            if len(tokens) < 2:
                continue

            # Pull however many trailing tokens are actually money-shaped
            # (up to the number of columns this report has). Stop as soon as a
            # token doesn't look like an amount -- that's where the label ends.
            amount_tokens = []
            i = len(tokens) - 1
            while i >= 0 and len(amount_tokens) < column_count and money_token.match(tokens[i]):
                amount_tokens.insert(0, tokens[i])
                i -= 1
            label_tokens = tokens[:i + 1]

            if not amount_tokens or not label_tokens:
                continue

            cat_raw = " ".join(label_tokens).strip(" .")
            if len(cat_raw) < 2:
                continue

            # Use the column matching the requested year when this line
            # actually has that many columns; a line with fewer amounts than
            # the header advertises (rare formatting slip) falls back to
            # whichever single amount it does have.
            col = column_index if column_index < len(amount_tokens) else len(amount_tokens) - 1
            amt = self._clean_amount(amount_tokens[col])
            if amt == 0.0:
                continue

            if current_section == "income":
                is_deposit = True
            elif current_section == "expense":
                is_deposit = False
            else:
                # No section header seen yet (or a neutral one) -- fall back to
                # the line's own wording, and otherwise trust QuickBooks' sign:
                # negative stays an expense-shaped entry, positive stays income.
                is_deposit = "income" in lower or "revenue" in lower or "sales" in lower or amt > 0

            # Preserve the sign QuickBooks printed. A loss (negative income
            # line) must stay negative, not get flipped positive by category.
            final_amt = amt if is_deposit else -abs(amt)
            if is_deposit and amt < 0:
                final_amt = amt  # a negative "income" line is a real loss

            cat, conf_state, conf_score = self.categorizer.categorize_transaction(
                payee=cat_raw,
                description=cat_raw,
                amount=final_amt,
                is_deposit=is_deposit
            )

            transactions.append(TransactionItem(
                date=f"12/31/{year}",
                payee=cat_raw,
                description=f"P&L Line Item: {cat_raw}",
                amount=final_amt,
                is_deposit=is_deposit,
                category=cat,
                confidence_state=conf_state,
                confidence_score=conf_score,
                source_file=filename,
                original_category="P&L Report"
            ))

        data.transactions = transactions
        data.diagnostics_notes = diagnostics_notes
        return data

    # -------------------------------------------------------------------------
    # STRATEGY 2: CREDIT CARD STATEMENT PARSER (Capital One, Chase Card, Amex)
    # -------------------------------------------------------------------------
    def _parse_credit_card_pdf(self, file_source, filename: str, year: int,
                               statement_end_month: Optional[int] = None) -> FinancialDocumentData:
        data = FinancialDocumentData(document_type="credit_card", bank_or_vendor="Capital One Business Spark", statement_type="credit_card", bank_name="Capital One Business Spark")
        transactions = []

        if not pdfplumber:
            return data

        try:
            with pdfplumber.open(file_source) as pdf:
                for page in pdf.pages:
                    text = self._extract_page_text(page)
                    lines = text.split("\n")

                    for line in lines:
                        clean_line = line.strip()
                        # Capture balance/rollup figures unconditionally, not
                        # only on lines _is_summary() flags: bare SUMMARY-block
                        # lines like "Fees $8.00 -" or "Checks $5,130.19 -" are
                        # real balance data but aren't themselves classified as
                        # "summary" text, so gating capture behind that check
                        # silently dropped them (found by reconciliation math
                        # not closing on a real statement, even after every
                        # transaction was captured correctly). Safe to call on
                        # every line: it is a no-op unless a BALANCE_PATTERNS
                        # regex matches, and first-occurrence-wins protects
                        # against a coincidental match later in the document.
                        self._capture_balances(clean_line, data)
                        self._capture_credit_card_balances(clean_line, data)
                        if self._is_summary(clean_line):
                            continue

                        m = re.search(r'(\d{1,2}/\d{1,2}|[A-Z][a-z]{2}\s+\d{1,2})\s+(?:(\d{1,2}/\d{1,2}|[A-Z][a-z]{2}\s+\d{1,2})\s+)?(.+?)\s+([+-]?\$?\s*\(?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?)$', clean_line)
                        if not m:
                            # Interest and fee charges are listed with no date
                            # of their own ("Interest Charge on Purchases
                            # $95.58"), so they never match the dated pattern
                            # above and were previously dropped entirely --
                            # real Line 16b interest expense silently missing
                            # from every card statement. Only tried once the
                            # dated pattern has already failed, so a genuinely
                            # dated line (e.g. "Jan 15 Jan 16 Annual Fee
                            # $99.00") is never intercepted here. Dated to the
                            # statement's own closing date since no finer date
                            # is printed for these lines.
                            fee_match = re.match(
                                r'^(Interest Charge on [A-Za-z ]+?|Annual Fee|Late Fee|Cash Advance Fee|Balance Transfer Fee|Foreign Transaction Fee)\s+\$?\s*(\d{1,3}(?:,\d{3})*\.\d{2})$',
                                clean_line, re.IGNORECASE
                            )
                            if fee_match:
                                fee_desc = fee_match.group(1).strip()
                                fee_amt = self._clean_amount(fee_match.group(2))
                                if fee_amt != 0.0:
                                    fee_date = f"{statement_end_month}/28/{year}" if statement_end_month else f"12/31/{year}"
                                    transactions.append(TransactionItem(
                                        date=fee_date,
                                        payee=fee_desc,
                                        description=f"{fee_desc} (statement fee/interest section)",
                                        amount=-abs(fee_amt),
                                        is_deposit=False,
                                        category="Line 16b: Other interest" if "interest" in fee_desc.lower() else "Line 10: Commissions and fees",
                                        confidence_state="High Confidence",
                                        confidence_score=0.95,
                                        source_file=filename,
                                        original_category="Credit Card Statement (Fees/Interest Section)"
                                    ))
                            continue

                        if m:
                            trans_date_raw = m.group(1)
                            post_date_raw = m.group(2)
                            desc_raw = m.group(3).strip()
                            amt_raw = m.group(4)

                            amt = self._clean_amount(amt_raw)
                            if amt == 0.0 or len(desc_raw) < 2:
                                continue

                            trans_year = self._resolve_transaction_year(trans_date_raw, statement_end_month, year)
                            trans_date = f"{trans_date_raw}/{trans_year}" if '/' in trans_date_raw else f"{trans_date_raw}, {trans_year}"
                            if post_date_raw:
                                post_year = self._resolve_transaction_year(post_date_raw, statement_end_month, year)
                                post_date = f"{post_date_raw}/{post_year}" if '/' in post_date_raw else f"{post_date_raw}, {post_year}"
                            else:
                                post_date = trans_date

                            is_payment = any(kw in desc_raw.lower() for kw in ["payment thank you", "mobile pymt", "autopay", "credit card payment"])
                            is_refund = "refund" in desc_raw.lower() or "credit" in desc_raw.lower() or "-" in amt_raw
                            
                            is_deposit = is_refund and not is_payment
                            final_amt = abs(amt) if is_deposit else -abs(amt)

                            cat, conf_state, conf_score = self.categorizer.categorize_transaction(
                                payee=desc_raw,
                                description=desc_raw,
                                amount=final_amt,
                                is_deposit=is_deposit
                            )

                            transactions.append(TransactionItem(
                                date=trans_date,
                                post_date=post_date,
                                payee=desc_raw,
                                description=desc_raw,
                                amount=final_amt,
                                is_deposit=is_deposit,
                                category=cat,
                                confidence_state=conf_state,
                                confidence_score=conf_score,
                                source_file=filename,
                                original_category="Credit Card Statement"
                            ))
        except Exception as e:
            print(f"Error parsing Credit Card PDF {filename}: {e}")

        data.transactions = transactions
        return data

    # -------------------------------------------------------------------------
    # STRATEGY 3: GENERAL BANK, SCANNED INVOICE, CHECK & RECEIPT PARSER
    # -------------------------------------------------------------------------
    def _parse_general_or_scanned_pdf(self, file_source, filename: str, year: int,
                                      statement_end_month: Optional[int] = None) -> FinancialDocumentData:
        data = FinancialDocumentData(document_type="bank_or_receipt", bank_or_vendor="LifeGreen / Regions Business Checking", statement_type="bank_statement", bank_name="LifeGreen / Regions Business Checking")
        transactions = []

        if not pdfplumber:
            return data

        try:
            with pdfplumber.open(file_source) as pdf:
                current_section = "GENERAL"

                for page_idx, page in enumerate(pdf.pages):
                    text = self._extract_page_text(page)
                    lines = text.split("\n")

                    for line in lines:
                        clean_line = line.strip()
                        l_lower = clean_line.lower()

                        # Run balance/rollup capture before the section-header
                        # checks below, not after: "Total Deposits & Credits
                        # $37,638.76" contains the same "deposits & credits"
                        # substring the very next check uses to detect the
                        # *section header*, so running capture afterward meant
                        # that line was always consumed by the header check
                        # first and its dollar figure never reached
                        # _capture_balances at all (confirmed on a real
                        # statement: total_deposits stayed 0.0 despite the
                        # figure being right there in the text). Safe to call
                        # unconditionally: it is a no-op unless a
                        # BALANCE_PATTERNS regex matches, and first-occurrence-
                        # wins protects against a coincidental later match.
                        self._capture_balances(clean_line, data)

                        if "deposits & credits" in l_lower or "deposits and credits" in l_lower:
                            current_section = "DEPOSIT"
                            continue
                        elif (l_lower.strip().startswith("withdrawals") or
                              "withdrawals & debits" in l_lower or "withdrawals and debits" in l_lower or
                              "electronic debits" in l_lower):
                            # A real Regions statement prints this header as the
                            # bare word "WITHDRAWALS" (and "WITHDRAWALS
                            # (CONTINUED)" on later pages) -- not "WITHDRAWALS &
                            # DEBITS" or "WITHDRAWALS AND DEBITS", which is all
                            # the original match required. That header text
                            # never matched on a real statement, so this branch
                            # never fired and every withdrawal fell through to
                            # the DEPOSIT default below -- every Card/PIN
                            # Purchase in the section was booked as income.
                            # Confirmed by re-running against a real statement:
                            # every "Card Purchase ..." line showed is_deposit
                            # True until this was widened.
                            current_section = "EXPENSE"
                            continue
                        elif l_lower.strip() == "checks" or "checks paid" in l_lower:
                            # A cleared-checks listing. Regions prints these as
                            # "Date Check No. Amount" pairs, TWO pairs per line
                            # (a left column and a right column) -- a different
                            # shape from every other section, so it gets its own
                            # state and its own line format below.
                            current_section = "CHECK_DETAIL"
                            continue
                        elif "daily balance summary" in l_lower:
                            # A repeating "date balance date balance date
                            # balance" table -- every field on these lines is a
                            # date followed by a dollar-looking number, which is
                            # exactly the shape the universal transaction regex
                            # looks for. Left unguarded, it read three daily
                            # balances per line as one transaction with a huge
                            # fabricated amount (confirmed against a real
                            # statement: a $27,647.88 phantom withdrawal from a
                            # line that was actually three balance figures).
                            # Skipped outright until a real section header
                            # (checked above, ahead of this branch) fires again.
                            current_section = "SKIP"
                            continue

                        if current_section == "SKIP":
                            continue

                        if self._is_summary(clean_line):
                            continue

                        # Cleared-checks listing: "MM/DD CheckNo Amount" repeated
                        # once or twice per line. Found via findall (not a single
                        # match) because a line can carry one or two check
                        # entries, and the entry count varies at the end of the
                        # list ("* Break In Check Number Sequence." footnotes
                        # excluded since it has no digits to match).
                        if current_section == "CHECK_DETAIL" and "date" not in l_lower:
                            # The check number is optional (a bank sometimes
                            # never recorded one -- confirmed on two real
                            # statements, "09/17  8,000.00" with nothing
                            # between the date and the amount, exactly the
                            # dollar amount missing from reconciliation before
                            # this was fixed), and an asterisk can sit between
                            # the check number and the amount, marking a
                            # "Break In Check Number Sequence" footnote
                            # ("12/15  1272 * 260.00") -- \*? absorbs it
                            # without requiring it.
                            check_entries = re.findall(
                                r'(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(?:(\d{3,6})\s+)?\*?\s*(\d{1,3}(?:,\d{3})*\.\d{2})',
                                clean_line
                            )
                            if check_entries:
                                for chk_date_raw, chk_num, chk_amt_raw in check_entries:
                                    chk_amt = self._clean_amount(chk_amt_raw)
                                    if chk_amt == 0.0:
                                        continue
                                    chk_year = self._resolve_transaction_year(chk_date_raw, statement_end_month, year) if len(chk_date_raw) <= 5 else None
                                    chk_date = f"{chk_date_raw}/{chk_year}" if chk_year is not None else chk_date_raw
                                    final_amt = -abs(chk_amt)
                                    chk_label = f"Check #{chk_num}" if chk_num else "Check (no number recorded)"

                                    cat, conf_state, conf_score = self.categorizer.categorize_transaction(
                                        payee=chk_label,
                                        description=chk_label,
                                        amount=final_amt,
                                        is_deposit=False
                                    )
                                    transactions.append(TransactionItem(
                                        date=chk_date,
                                        payee=chk_label,
                                        description=chk_label,
                                        amount=final_amt,
                                        is_deposit=False,
                                        category=cat,
                                        confidence_state=conf_state,
                                        confidence_score=conf_score,
                                        source_file=filename,
                                        original_category="Check Document"
                                    ))
                                continue

                        # Single check reference embedded in a normal
                        # withdrawal/expense line, e.g. "Check #1234 ... 580.00"
                        # -- a different shape from the CHECK_DETAIL listing
                        # above (one check per line, "check"/"#" literally
                        # present), so it keeps its own narrower pattern.
                        check_match = re.search(r'check\s*#?\s*(\d{3,6})\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+\$?\s*(\d{1,3}(?:,\d{3})*\.\d{2})', clean_line, re.IGNORECASE)
                        if check_match and "balance" not in l_lower:
                            chk_num = check_match.group(1)
                            chk_date_raw = check_match.group(2)
                            chk_amt = self._clean_amount(check_match.group(3))

                            chk_year = self._resolve_transaction_year(chk_date_raw, statement_end_month, year) if len(chk_date_raw) <= 5 else None
                            chk_date = f"{chk_date_raw}/{chk_year}" if chk_year is not None else chk_date_raw
                            final_amt = -abs(chk_amt)

                            cat, conf_state, conf_score = self.categorizer.categorize_transaction(
                                payee=f"Check #{chk_num}",
                                description=f"Check #{chk_num}",
                                amount=final_amt,
                                is_deposit=False
                            )
                            transactions.append(TransactionItem(
                                date=chk_date,
                                payee=f"Check #{chk_num}",
                                description=f"Check #{chk_num}",
                                amount=final_amt,
                                is_deposit=False,
                                category=cat,
                                confidence_state=conf_state,
                                confidence_score=conf_score,
                                source_file=filename,
                                original_category="Check Document"
                            ))
                            continue

                        # Universal Line Pattern: Date Description Amount
                        m = re.search(r'(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(.+?)\s+([+-]?\$?\s*\(?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?)$', clean_line)
                        if m:
                            date_raw = m.group(1)
                            desc_raw = m.group(2).strip()
                            amt_raw = m.group(3)

                            amt = self._clean_amount(amt_raw)
                            if amt == 0.0 or len(desc_raw) < 2:
                                continue

                            tx_year = self._resolve_transaction_year(date_raw, statement_end_month, year) if len(date_raw) <= 5 else None
                            tx_date = f"{date_raw}/{tx_year}" if tx_year is not None else date_raw

                            is_withdrawal_kw = any(kw in desc_raw.lower() for kw in WITHDRAWAL_KEYWORDS) or "capital one mobile pymt" in desc_raw.lower() or "card pymt" in desc_raw.lower()
                            is_deposit_kw = any(kw in desc_raw.lower() for kw in DEPOSIT_KEYWORDS)

                            if is_withdrawal_kw:
                                is_deposit = False
                            elif is_deposit_kw or current_section == "DEPOSIT":
                                is_deposit = True
                            else:
                                is_deposit = False

                            final_amt = abs(amt) if is_deposit else -abs(amt)

                            cat, conf_state, conf_score = self.categorizer.categorize_transaction(
                                payee=desc_raw,
                                description=desc_raw,
                                amount=final_amt,
                                is_deposit=is_deposit
                            )

                            transactions.append(TransactionItem(
                                date=tx_date,
                                payee=desc_raw,
                                description=desc_raw,
                                amount=final_amt,
                                is_deposit=is_deposit,
                                category=cat,
                                confidence_state=conf_state,
                                confidence_score=conf_score,
                                source_file=filename,
                                original_category="General PDF Document"
                            ))

                        # Single Invoice / Hand-Signed Receipt Fallback
                        elif ("invoice" in l_lower or "total" in l_lower or "amount due" in l_lower or "receipt" in l_lower) and not transactions:
                            amt_m = re.search(r'([+-]?\$?\s*\(?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?)', clean_line)
                            if amt_m:
                                amt = self._clean_amount(amt_m.group(1))
                                if amt != 0.0:
                                    is_dep = "invoice" not in l_lower and "receipt" not in l_lower
                                    final_a = abs(amt) if is_dep else -abs(amt)
                                    cat, conf_s, conf_sc = self.categorizer.categorize_transaction(
                                        payee=filename.replace(".pdf", ""),
                                        description=clean_line,
                                        amount=final_a,
                                        is_deposit=is_dep
                                    )
                                    transactions.append(TransactionItem(
                                        date=f"01/01/{year}",
                                        payee=filename.replace(".pdf", ""),
                                        description=clean_line,
                                        amount=final_a,
                                        is_deposit=is_dep,
                                        category=cat,
                                        confidence_state="Needs Review",
                                        confidence_score=0.70,
                                        source_file=filename,
                                        original_category="Single Receipt / Invoice PDF"
                                    ))

        except Exception as e:
            print(f"Error parsing General PDF {filename}: {e}")

        data.transactions = transactions
        return data

    def _capture_balances(self, line_clean: str, data: FinancialDocumentData) -> None:
        """Record a statement-declared balance or rollup total, if this line is one.

        First occurrence wins: banks repeat "Ending Balance" in per-page footers
        and daily balance tables, and only the account summary value is
        authoritative.
        """
        line_lower = line_clean.lower()

        for field, patterns in BALANCE_PATTERNS.items():
            if getattr(data, field):
                continue
            if not any(re.search(pat, line_lower) for pat in patterns):
                continue

            amounts = re.findall(
                r'[+-]?\$?\s*\(?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?', line_clean
            )
            if not amounts:
                continue

            value = self._clean_amount(amounts[-1])
            if value != 0.0:
                setattr(data, field, abs(value))

    # Boundaries where pdfplumber's word extraction (or the source PDF itself)
    # can drop a space that is visually present on the statement:
    #   lower -> UPPER   "PurchaseCircle" -> "Purchase Circle"
    #   UPPER-run -> Titlecase  "PYMTAuth" -> "PYMT Auth"  (needed because the
    #     lower->UPPER rule alone misses an all-caps word directly followed by
    #     a capitalized word, e.g. Capital One's own statement text has zero
    #     gap between "PYMT" and "AuthDate" -- confirmed at the character level,
    #     not just an extraction-tolerance issue)
    #   digit <-> letter  "5200North" -> "5200 North", "9023Sarasota" -> "9023 Sarasota"
    # This matters beyond cosmetics: every keyword match and every Non-P&L
    # exclusion pattern relies on word boundaries, so merged text silently
    # breaks both.
    _SPACE_LOWER_UPPER = re.compile(r'(?<=[a-z])(?=[A-Z])')
    _SPACE_ACRONYM_TITLE = re.compile(r'(?<=[A-Z]{2})(?=[A-Z][a-z])')
    _SPACE_DIGIT_LETTER = re.compile(r'(?<=[0-9])(?=[A-Za-z])')
    _SPACE_LETTER_DIGIT = re.compile(r'(?<=[A-Za-z])(?=[0-9])')

    def _normalize_spacing(self, text: str) -> str:
        text = self._SPACE_LOWER_UPPER.sub(' ', text)
        text = self._SPACE_ACRONYM_TITLE.sub(' ', text)
        text = self._SPACE_DIGIT_LETTER.sub(' ', text)
        text = self._SPACE_LETTER_DIGIT.sub(' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    def _extract_page_text(self, page) -> str:
        """Extracts a page's text with a lower x_tolerance than pdfplumber's
        default (3pt), which recovers real gaps between words that the default
        merges on some statement layouts (observed on real Regions statements),
        then runs the merge-recovery heuristic above for gaps the source PDF
        never had at all (observed on real Capital One statements)."""
        raw = page.extract_text(layout=False, x_tolerance=1) or ""
        return "\n".join(self._normalize_spacing(line) for line in raw.split("\n"))

    def _capture_credit_card_balances(self, line_clean: str, data: FinancialDocumentData) -> None:
        """Captures the Account Summary box a credit card statement prints on
        its first page: "Previous Balance", "New Balance" (already handled by
        _capture_balances -- "previous"/"new balance" match its generic
        beginning/ending patterns), and four lines that move the balance in
        between: "Payments - $X", "Other Credits $X" (reduce it), and
        "Transactions + $X", "Cash Advances + $X", "Fees Charged + $X",
        "Interest Charged + $X" (increase it).

        Unlike _capture_balances' first-occurrence-wins semantics, these four
        "increase" lines are summed: a card statement has no single "Total New
        Charges" line the way a bank statement has "Total Withdrawals", so the
        four components have to be added up here to reconstruct the equivalent
        figure. Left uncaptured, every card statement's balance check compared
        the ending balance against the beginning balance alone (no payments or
        charges applied at all) and reported every single statement as a
        discrepancy of exactly its stated activity -- confirmed against a real
        statement, where the reported gap matched Previous Balance minus New
        Balance exactly.
        """
        line_lower = line_clean.lower()
        # The Account Summary box sits beside a two-column "Payment
        # Information" block on the statement; pdfplumber's reading order
        # occasionally merges a line from one column into the other (confirmed
        # on a real statement: "Payment Due Date For online and phone
        # payments, the Previous Balance $4,280.82" -- an unrelated sentence
        # that happens to start with "Payment" and end with the beginning
        # balance figure, which this method would otherwise misread as a
        # $4,280.82 payment). A genuine "Payments - $2,250.00" summary line is
        # short; a merged run-on sentence is not, so line length is the guard.
        if len(line_clean.split()) > 5:
            return

        amounts = re.findall(r'[+-]?\$?\s*\(?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?', line_clean)
        if not amounts:
            return
        value = abs(self._clean_amount(amounts[-1]))
        if value == 0.0:
            return

        if re.match(r'^payments?\b', line_lower) or re.match(r'^other\s+credits\b', line_lower):
            data.total_deposits = (data.total_deposits or 0.0) + value
        elif re.match(r'^(?:transactions|cash\s+advances|fees\s+charged|interest\s+charged)\b', line_lower):
            data.total_withdrawals = (data.total_withdrawals or 0.0) + value

    def _clean_amount(self, val: str) -> float:
        if not val:
            return 0.0
        s = str(val).strip()
        is_neg = '-' in s or '(' in s or 'DR' in s.upper()
        clean = re.sub(r'[^\d.]', '', s)
        try:
            num = float(clean)
            return -num if is_neg else num
        except ValueError:
            return 0.0

    def _is_summary(self, line_clean: str) -> bool:
        line_lower = line_clean.lower()
        for term in SUMMARY_TERMS:
            if term in line_lower:
                return True
        for pattern in SUMMARY_REGEXES:
            if re.search(pattern, line_lower):
                return True
        return False

    def _extract_year_from_filename(self, filename: str) -> int:
        m = re.search(r'\b(202\d)\b', filename)
        return int(m.group(1)) if m else 2025

    def _extract_statement_end_month_from_filename(self, filename: str) -> Optional[int]:
        """Recovers the statement's closing month from filenames of the form
        "<Bank> <Acct> MM DD YYYY[ suffix].pdf" -- the naming convention these
        statements actually use, and the only reliable source of the closing
        date without depending on any particular statement's own wording.

        Needed because a billing cycle that closes in January routinely opens
        in December of the *prior* year (e.g. "Dec 26, 2024 - Jan 25, 2025"),
        and every transaction date on the statement omits the year. Stamping
        every transaction with the year from the filename, as a naive
        implementation does, silently reassigns December's transactions to
        the wrong tax year.
        """
        m = re.search(r'\b(\d{1,2})\s+(\d{1,2})\s+(20\d{2})\b', filename)
        if not m:
            return None
        month = int(m.group(1))
        return month if 1 <= month <= 12 else None

    def _resolve_transaction_year(self, date_fragment: str, statement_end_month: Optional[int],
                                  statement_year: int) -> int:
        """Picks the calendar year for a transaction date that has no year of
        its own, given the year printed on the statement (its closing year)
        and the statement's closing month.

        A transaction whose month number is *greater* than the statement's
        closing month cannot belong to the closing year -- a statement that
        closes in January cannot contain a November or December transaction
        from that same January's calendar year, only from the year before.
        """
        if statement_end_month is None:
            return statement_year
        tx_month = self._extract_month(date_fragment)
        if tx_month > statement_end_month:
            return statement_year - 1
        return statement_year

    def _extract_month(self, date_str: str) -> int:
        try:
            m_match = re.search(r'^(\d{1,2})[/-]', date_str)
            if m_match:
                return int(m_match.group(1))
            months_map = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
            for m_name, m_num in months_map.items():
                if m_name in date_str.lower():
                    return m_num
        except Exception:
            pass
        return 1

    def check_12_month_coverage(self, months_found: List[int]) -> Dict[str, Any]:
        unique_months = set([m for m in months_found if m and 1 <= m <= 12])
        missing_months = sorted(list(set(range(1, 13)) - unique_months))
        
        is_complete = len(missing_months) == 0
        return {
            "is_complete": is_complete,
            "present_months_count": len(unique_months),
            "missing_months": missing_months,
            "status_message": "All 12 months present" if is_complete else f"Missing {len(missing_months)} statement month(s): {missing_months}"
        }
