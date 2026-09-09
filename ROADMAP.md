# Gap Analysis & Roadmap

Reconciliation of the current implementation against the full requirements spec.
Verified by reading every module and running the pipeline end-to-end against
`sample_data/` on 2026-09-09.

## Status against the spec

| Requirement | Status |
|---|---|
| Bank/credit card statement PDFs (digital) | ✅ Implemented |
| Bank/credit card statements (scanned images) | ❌ No OCR — see Gap 1 |
| Client spreadsheets, varied formats | ⚠️ Implemented, sign-convention bug — see Gap 2 |
| Client-provided year-end totals | ⚠️ Module exists, not wired into the UI — see Gap 4 |
| Preserve original client category alongside standard category | ✅ Implemented |
| Gross receipts / total expenses / net profit | ✅ Implemented (live Excel formulas) |
| Expense breakdown by Schedule C line | ✅ Implemented |
| Exception queues | ⚠️ Five of six present — see Gap 6 |
| Auto-generated client question list (never auto-sent) | ✅ Implemented |
| Full audit trail, every number traceable | ✅ Implemented |
| Confidence labels per transaction | ⚠️ Only two of three states are ever emitted — see Gap 5 |
| Never fabricate a classification | ✅ Unmatched items fall to Needs Review + exception queue |
| Exclude transfers / card payments / loans / draws / refunds | ⚠️ Keyword-based, incomplete — see Gap 3 |
| QC: 12-month completeness | ⚠️ PDF-only, year-blind — see Gap 7 |
| QC: duplicate detection | ✅ Filename-based, as specified |
| QC: reconciliation to statement balances | ❌ Not implemented — see Gap 8 |
| QC: math integrity | ✅ Excel formulas recompute totals from the line items |
| Excel workbook output | ✅ Five sheets |
| Preparer summary dashboard | ✅ Streamlit + Excel summary sheet |

---

## Correctness bugs (highest priority)

These change reported dollar totals and were both reproduced against
`sample_data/`.

### Gap 2 — Spreadsheet sign convention misreads income as expense

`core/spreadsheet_parser.py` only consults the deposit keywords when
`amount > 0`. A client sheet that records expenses as positive and income as
negative — a common convention — has its revenue booked as an expense.

Reproduced: a `-4500.00` row labeled `Revenue Deposit` was categorized as a
$4,500 *expense*. Understated gross receipts by $4,500 and overstated expenses
by $4,500 — a $9,000 swing in net profit on a single row.

Fix: detect the sheet's sign convention per sheet (e.g. by checking whether
rows whose category/payee match income keywords are predominantly negative),
then normalize, rather than assuming positive = expense.

### Gap 3 — Non-P&L keyword matching misses common wordings

`NON_PNL_PATTERNS` in `core/tax_categorizer.py` matches fixed substrings.
Reproduced: `CREDIT CARD AUTOMATIC PAYMENT` did not match the
`credit card payment` pattern (the word "automatic" sits between), so a $450
card payment was booked as a deductible expense. Credit card payments must
never reach the P&L.

Fix: loosen these to token-gap regexes (`credit\s+card\s+\w*\s*pay`) and expand
the vocabulary. This list is the tool's main safeguard against double-counting,
so it deserves a dedicated test suite of real statement description strings.

### Gap 9 — `finance charge` is both a summary term and a real expense

`finance charge` and `total interest charged` appear in `SUMMARY_TERMS`
(`core/pdf_parser.py`), which drops any line containing them — but a finance
charge line on a credit card statement is a genuine Line 16b interest expense,
and it is also listed as a Line 16b keyword. Real interest expense is being
silently discarded.

---

## Feature gaps

### Gap 1 — No OCR for scanned statements

The spec calls for scanned-image statements. `pdfplumber.extract_text()`
returns empty for image-only PDFs, so such a file yields zero transactions with
no visible error. Needs an OCR fallback (`pytesseract` + `pdf2image`, or
`ocrmypdf`) plus a loud warning when a PDF yields no extractable text.

### Gap 4 — Year-end totals input not reachable from the UI

`core/totals_parser.py` is complete and well-specified (vehicle mileage vs.
actual, 50% meals, 1099 threshold, fixed assets, health insurance belongs on
Schedule 1 not Schedule C, home office / Form 8829). But `app.py` accepts
`.json` in the uploader and then never routes it anywhere — so input type 2 is
silently dropped. Wiring it up is small.

### Gap 5 — The `Unresolved` confidence state is never emitted

`categorize_transaction` returns only `High Confidence` and `Needs Review`.
The spec's third state has no producer, so preparers cannot distinguish
"probably right, confirm" from "no idea at all". Suggest: `Unresolved` for a
transaction with no keyword match *and* an unparseable or generic payee.

### Gap 6 — Missing the "missing information" exception queue

Present: uncategorized, potential personal, potential fixed assets,
transfers/non-P&L, unusual/large. Absent: the spec's *missing information*
queue (e.g. a month with no statement, a file that parsed to zero
transactions, a transaction with no payee).

### Gap 7 — 12-month coverage check is PDF-only and year-blind

In `app.py`, `months_found` is populated only from PDF results, so a
spreadsheet-only engagement always reports 11 missing months.
`_extract_month` also ignores the year, so statements spanning two tax years
collapse together.

### Gap 8 — No reconciliation to statement balances

`FinancialDocumentData` declares `beginning_balance`, `ending_balance`,
`total_deposits`, `total_withdrawals`, but no parser ever populates them.
Populating and asserting
`beginning + deposits − withdrawals == ending` per statement is the single
strongest check that no transactions were dropped by the regex parsers.

### Gap 10 — Cross-account transfer matching

The known real-world test case (two personal accounts plus one business
account, transfers mirrored as a debit in one file and a credit in another) is
only handled by per-line keywords. Matching mirrored pairs across files —
same date ±1 day, equal and opposite amount, both flagged as transfer-like —
would catch the cases where the description alone is uninformative. This must
only *flag* candidate pairs for review, never silently drop transactions.

---

## Smaller items

- The **De Minimis threshold** sidebar input in `app.py` is collected but never
  passed to `ExceptionAnalyzer`, which hardcodes `$2,500`. The control does
  nothing.
- **Parse failures are silent.** Every parser wraps its work in
  `try/except` and `print()`s the error, returning partial data. In a tax tool
  a partially-parsed statement is worse than a failed one — surface these in
  the UI and in a QC sheet.
- `_is_summary` drops any line containing `page `, which can match legitimate
  vendor descriptions.
- Credit card refund detection treats any description containing `credit` as a
  refund, flipping vendors like "Credit Union" to income.
- Meals are halved in the summary but stored at full amount in the audit trail
  (correct and annotated — noting it so it is not "fixed" by mistake).

---

## Suggested order of work

1. Gaps 2, 3, 9 — dollar-affecting bugs, with regression tests.
2. Gap 8 — reconciliation, the best structural guard against silent data loss.
3. Gap 1 — OCR, needed before real scanned client files can be processed.
4. Gaps 4, 5, 6, 7 — spec completeness.
5. Gap 10 — cross-account transfer matching.
