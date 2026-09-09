# Gap Analysis & Roadmap

Reconciliation of the current implementation against the full requirements spec.
Verified by reading every module and running the pipeline end-to-end against
`sample_data/` on 2026-09-09.

**Gaps 2, 3, 5, 8, 9 and the De Minimis control were fixed on 2026-09-09** and
are marked ✅ below with the fix described. Everything still marked ❌ or ⚠️ is
open.

## Status against the spec

| Requirement | Status |
|---|---|
| Bank/credit card statement PDFs (digital) | ✅ Implemented |
| Bank/credit card statements (scanned images) | ❌ No OCR — see Gap 1 |
| Client spreadsheets, varied formats | ✅ Fixed — per-sheet sign convention detection (Gap 2) |
| Client-provided year-end totals | ⚠️ Module exists, not wired into the UI — see Gap 4 |
| Preserve original client category alongside standard category | ✅ Implemented |
| Gross receipts / total expenses / net profit | ✅ Implemented (live Excel formulas) |
| Expense breakdown by Schedule C line | ✅ Implemented |
| Exception queues | ⚠️ Five of six present — see Gap 6 |
| Auto-generated client question list (never auto-sent) | ✅ Implemented |
| Full audit trail, every number traceable | ✅ Implemented |
| Confidence labels per transaction | ✅ Fixed — all three states now reachable (Gap 5) |
| Never fabricate a classification | ✅ Unmatched items fall to Needs Review + exception queue |
| Exclude transfers / card payments / loans / draws / refunds | ✅ Fixed — token-gap regexes (Gap 3) |
| QC: 12-month completeness | ⚠️ PDF-only, year-blind — see Gap 7 |
| QC: duplicate detection | ✅ Filename-based, as specified |
| QC: reconciliation to statement balances | ✅ Implemented — `core/reconciliation.py` (Gap 8) |
| QC: math integrity | ✅ Excel formulas recompute totals from the line items |
| Excel workbook output | ✅ Six sheets (added Reconciliation QC) |
| Preparer summary dashboard | ✅ Streamlit + Excel summary, with a reconciliation indicator |

---

## Correctness bugs — FIXED 2026-09-09

These changed reported dollar totals. Each was reproduced against
`sample_data/`, fixed, and pinned with regression tests.

### Gap 2 — Spreadsheet sign convention misreads income as expense ✅

`core/spreadsheet_parser.py` only consults the deposit keywords when
`amount > 0`. A client sheet that records expenses as positive and income as
negative — a common convention — has its revenue booked as an expense.

Reproduced: a `-4500.00` row labeled `Revenue Deposit` was categorized as a
$4,500 *expense*. Understated gross receipts by $4,500 and overstated expenses
by $4,500 — a $9,000 swing in net profit on a single row.

**Fixed.** `_detect_sign_convention` now classifies each sheet as `standard`,
`inverted`, or `unsigned` and normalizes to the tool-wide convention (income
positive, expenses negative — the same as the PDF parser, which the spreadsheet
path previously did not match either). The convention chosen is recorded in the
parse diagnostics. Covered by `tests/test_spreadsheet_parser.py`.

### Gap 3 — Non-P&L keyword matching misses common wordings ✅

`NON_PNL_PATTERNS` in `core/tax_categorizer.py` matches fixed substrings.
Reproduced: `CREDIT CARD AUTOMATIC PAYMENT` did not match the
`credit card payment` pattern (the word "automatic" sits between), so a $450
card payment was booked as a deductible expense. Credit card payments must
never reach the P&L.

**Fixed.** `NON_PNL_PATTERNS` are now token-gap regexes that tolerate words and
punctuation between the tokens, with an expanded vocabulary across all five
Non-P&L classes. Pinned by a parametrized regression suite of real statement
wordings in `tests/test_tax_categorizer.py`, including negative cases so that
genuine expenses ("Upwork Contractor Payment") are not swallowed.

### Gap 9 — `finance charge` is both a summary term and a real expense ✅

`finance charge` and `total interest charged` appear in `SUMMARY_TERMS`
(`core/pdf_parser.py`), which drops any line containing them — but a finance
charge line on a credit card statement is a genuine Line 16b interest expense,
and it is also listed as a Line 16b keyword. Real interest expense was being
silently discarded.

**Fixed.** Bare `finance charge` was removed from `SUMMARY_TERMS`; only the
`total ...` rollups are treated as summary. The over-broad `"page "` substring,
which matched vendor names such as "PAGEANT SUPPLY CO", was replaced with
anchored regexes in a new `SUMMARY_REGEXES` list.

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

### Gap 5 — The `Unresolved` confidence state is never emitted ✅

`categorize_transaction` returns only `High Confidence` and `Needs Review`.
The spec's third state had no producer, so preparers could not distinguish
"probably right, confirm" from "no idea at all".

**Fixed.** `_is_opaque_payee` returns `Unresolved` (score 0.0) when a
description reduces to nothing but generic statement tokens and stripped digits
(`POS DEBIT 4412`), and `Needs Review` when a readable vendor name survives.

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

### Gap 8 — No reconciliation to statement balances ✅

`FinancialDocumentData` declares `beginning_balance`, `ending_balance`,
`total_deposits`, `total_withdrawals`, but no parser ever populates them.
**Implemented.** `_capture_balances` now populates those fields from account
summary lines (first occurrence wins, since banks repeat them in page footers),
and the new `core/reconciliation.py` runs two checks per document: that the
statement's own balances foot, and that the extracted transactions add up to the
totals it declares. Results surface as a dashboard metric, a Reconciliation QC
tab, and a sixth Excel sheet. A document with no declared balances reports
**Not Reconcilable**, never a pass. Covered by `tests/test_reconciliation.py`.

### Gap 10 — Cross-account transfer matching

The known real-world test case (two personal accounts plus one business
account, transfers mirrored as a debit in one file and a credit in another) is
only handled by per-line keywords. Matching mirrored pairs across files —
same date ±1 day, equal and opposite amount, both flagged as transfer-like —
would catch the cases where the description alone is uninformative. This must
only *flag* candidate pairs for review, never silently drop transactions.

---

## Smaller items

- ✅ **Fixed.** The **De Minimis threshold** sidebar input was collected but
  never passed to `ExceptionAnalyzer`, which hardcoded `$2,500` — the control
  did nothing. `analyze_exceptions` now takes it as a parameter.
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

## Suggested order of remaining work

1. Gap 1 — OCR, needed before real scanned client files can be processed.
2. Gaps 4, 6, 7 — spec completeness (year-end totals input, missing-information
   queue, coverage check across all source types).
3. Gap 10 — cross-account transfer matching.
4. Surfacing silent parse failures in the UI.

### Not verified end to end

The reconciliation logic, categorizer and parsers are covered by tests and were
run against `sample_data/`. The new Streamlit tab and dashboard metric were
confirmed to render and compile, but were not exercised with an actual file
upload in an automated test — Streamlit's uploader does not lend itself to one.
