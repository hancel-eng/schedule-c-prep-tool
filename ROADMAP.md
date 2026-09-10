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

---

## Real-data verification pass — 2026-09-09

Ran the full pipeline against a real 12-month engagement (11 Capital One card
statements, 11 Regions bank statements + 11 matching scanned-check PDFs, 1
comparative QuickBooks P&L) and traced every reported dollar back to its
source. Reported Gross Receipts dropped from **$2,123,580.74 to $503,833.34**
(bank/card statements only — the P&L file, if also uploaded, still double-
counts the same activity; see Gap 11). Eight distinct bugs were found and
fixed, each reproduced against the real files before and after, with
regression tests added in `tests/test_real_world_regressions.py` using
synthetic fixtures (never the client's own PDFs).

### Fixed

1. **P&L parser read the wrong year column, double-counted subtotals, and
   flipped loss signs.** A comparative P&L ("Jan-Dec 25 | Jan-Dec 24") always
   had its last-printed number captured — the 2024 column, not 2025 — and
   `Total Income`/`Net Ordinary Income`/`Net Income`/`Total Other Income`
   were summed on top of the detail lines they restate. Six duplicate/
   subtotal lines from one file alone contributed **$1,129,847.95** of
   phantom income. Rewritten to pick the requested year's column, skip every
   subtotal/derived line, and preserve the source's own sign.
2. **Merged/glued statement text broke every downstream keyword match.** Two
   distinct causes, confirmed at the pdfplumber character-coordinate level:
   Regions statements have a real gap the default `x_tolerance` (3pt) missed
   (fixed by lowering it); Capital One statements have *zero* gap in the PDF
   text stream itself between fragments like `PYMTAuthDate` (fixed with a
   post-extraction spacing-recovery heuristic). Real-world impact: a $2,250
   card payment matched the "mobil" (gas station) keyword as a substring of
   "mobile" and was booked as a Car/Truck **expense** instead of excluded.
3. **Word-boundary matching added everywhere a keyword is checked.** The
   "mobil"/"mobile" collision above was one instance of a systemic class of
   bug — short/generic keywords (`bp`, `cpa`, `aws` ⊂ "dr**aws**", `rent` ⊂
   "cur**rent**") matched as bare substrings. All 140+ keywords now match on
   word boundaries.
4. **Cleared-checks listings were never captured, in any file, at all.** The
   `*checks.pdf` files are scanned check images (confirmed: zero extractable
   text — the known OCR gap, Gap 1). But the main statement *also* lists
   cleared checks in text, as two "Date CheckNo Amount" pairs per line — a
   different order and shape than the parser's regex assumed. ~$5,130/month
   in real check expenses was silently missing everywhere. New dedicated
   two-per-line parser matches the statement's own "Total Checks" figure to
   the cent.
5. **December transactions in a January-closing statement got the wrong
   year.** A billing cycle "Dec 26, 2024 – Jan 25, 2025" had every
   transaction stamped with the year parsed from the filename (2025),
   including the December ones. Fixed by resolving each transaction's year
   from the statement's own closing month (itself parsed from the filename's
   `MM DD YYYY` convention) rather than blanket-applying one year.
6. **Card interest charges have no date of their own and were dropped
   entirely.** `Interest Charge on Purchases $95.58` never matched the
   dated-transaction pattern (no leading date), so real Line 16b interest
   expense was silently missing from every Capital One statement, even after
   the earlier `SUMMARY_TERMS` fix (which only stopped an already-matched
   line from being discarded — this line was never matched in the first
   place). Now captured, dated to the statement's closing date.
7. **Vendor credits inflated Gross Receipts.** `Card Credit The Home Depot`
   (a refund on an earlier purchase) fell through Non-P&L detection straight
   to `Income: Gross Receipts`, since "credit" alone wasn't a recognized
   refund pattern. Added to `Non-P&L: Tax Refund / Reimbursement`.
8. **The bare "WITHDRAWALS" section header was never recognized — every
   withdrawal in every Regions statement was booked as income.** The
   section-tracking logic required "WITHDRAWALS & DEBITS" or "WITHDRAWALS AND
   DEBITS"; a real statement prints the bare word "WITHDRAWALS". That
   condition never matched, so every `Card Purchase`/`PIN Purchase` line fell
   through to the DEPOSIT default. This was masked in initial testing by bug
   #9 below (a much larger phantom-withdrawal bug) inflating the withdrawal
   side enough that the flip wasn't obviously visible. Fixed the header match
   and added the real vocabulary (`card purchase`, `pin purchase`,
   `recurring card transaction`) to `WITHDRAWAL_KEYWORDS` as defense-in-depth.
9. **The "DAILY BALANCE SUMMARY" table was read as transactions.** Its rows
   ("date balance date balance date balance", three pairs per line) have
   exactly the shape the universal transaction regex looks for. One row
   produced a **$27,647.88 phantom withdrawal**; across one statement this
   fabricated over $150,000 in nonexistent expense. Now skipped outright as
   its own tracked section.
10. **Balance/rollup capture was gated behind `_is_summary()`, which several
    real summary lines don't satisfy**, and separately was *always* consumed
    by the section-header check first when the two matched the same
    substring (`Total Deposits & Credits` vs. the `DEPOSITS & CREDITS`
    header). Between the two, `total_deposits`, and bank-statement `Fees`/
    `Checks` sub-totals were never reaching the reconciliation checker even
    though every relevant transaction was captured correctly — a real
    statement's reconciliation reported "Discrepancy" despite complete,
    correct extraction. Fixed by running balance capture unconditionally,
    before the section-header shortcuts. A parallel gap on credit card
    statements (no `Payments`/`Transactions`/`Interest Charged` capture at
    all) is fixed by a dedicated `_capture_credit_card_balances`.

### New gaps found (not yet fixed)

**Gap 11 — Uploading a P&L summary alongside the statements it summarizes
double-counts the same activity**, even with the parser itself now correct.
This is a scope/workflow issue, not a parsing bug: Lindsay's spec treats
"client-provided totals" (of which a P&L export is one form) as an
*alternative* input to raw statements, not a supplement. The UI should warn,
or exclude one source, when both a P&L/totals file and bank/card statements
covering the same period are uploaded together.

**Gap 12 — Bank-statement gross receipts ($503,833.34) do not match the
business's own QuickBooks P&L ($222,855.01 revenue for 2025).** This is over
double. Not something to silently "fix" in code — it's exactly the kind of
finding that belongs in front of a preparer: either QuickBooks is materially
behind actual bank activity, or the bank-derived total still includes
non-revenue deposits (loan draws, owner contributions, the payroll-company
figures that were previously miscategorized) that need a closer look than
this session's time allowed. Recommend a dedicated pass through the largest
individual deposits before relying on the bank-derived figure.

**Gap 13 — A handful of Capital One and Regions statements still show
$200–$9,500 reconciliation discrepancies** (down from the $1,600–$150,000+
range before today's fixes), mostly on months with card-number changes or
unusual formatting. Each is now individually visible in the Reconciliation QC
tab/sheet rather than silently absorbed into the totals — investigate
per-statement rather than assuming a systemic cause, since the largest
remaining sources (P&L, merged text, bare withdrawal headers, phantom daily
balances) are now fixed.

---

## Client-answer feedback loop — 2026-09-10

From a call with Lindsay: Tax Savers' Schedule C clients arrive at two
different tiers. **Level 2** (12 full statements) is what this tool handles.
**Level 1** (messy, inconsistent expense totals only, no statements) has no
tool yet — Lindsay is sending real client examples before that work starts.

Lindsay's direct question on the call — once a client answers the
auto-generated questions, how does the workpaper's numbers update? — was an
open gap (no way existed to feed an answer back into the app). **Closed this
session.**

### What was built

- `core/transaction_utils.py` — a stable transaction identity (date + payee +
  amount + source file) used to trace a question back to the exact
  transaction(s) it concerns, without needing a database or a synthetic id.
- `core/question_generator.py` — every generated question now carries that
  key (or, for the 1099 aggregate questions, the contractor name).
- `core/answer_applier.py` — applies an answered question to the transaction
  list: a confirmed-personal expense is recategorized to `Non-P&L: Personal
  Expense (Client Confirmed)` and excluded from the P&L entirely; a confirmed
  asset moves to Line 13; an uncategorized item is recategorized only if the
  answer is one of the tool's own recognized categories (an answer in the
  client's own words is logged for the preparer to map by hand, never
  guessed at); a 1099 answer is a compliance note, not a recategorization.
  Every transaction touched is stamped `client_confirmed=True`.
- `core/exception_analyzer.py` — now skips a transaction already stamped
  `client_confirmed`, which is what actually lets the loop close: without
  it, an answered question regenerated identically on every recompute.
  Deliberately keyed off that stamp rather than off category text, so a
  transaction the *categorizer itself* auto-placed in a Non-P&L or Line 13
  category (a real, still-open exception if it also matches a personal/asset
  keyword) is never mistaken for a resolved one.
- `core/excel_exporter.py` — a seventh sheet, **Applied Client Answers**,
  the audit trail of every correction and why.
- `app.py` — the **Client Inquiry Questions** tab is now four editable
  tables (one per question type), each with an **Answer** column
  constrained to a fixed dropdown (never free text) via
  `st.column_config.SelectboxColumn`. An **Apply Client Answers &
  Recalculate Workpaper** button merges the edited answers, calls
  `answer_applier`, and reruns — the dashboard, exception queues, and Excel
  export all reflect the correction immediately. Parsing now only happens
  once per distinct set of uploaded files (guarded by a signature of
  filename+size), with the working transaction list held in
  `st.session_state` between reruns — required so a client's answer
  survives the rerun every Streamlit widget interaction triggers, without
  breaking the documented "one client = one session, nothing persisted"
  design: state still lives only as long as the browser tab does, and a
  different set of uploaded files starts a clean slate.

### Verification

18 new unit tests (`tests/test_answer_applier.py` and
`tests/test_transaction_utils.py`) cover every answer type, the "never guess"
behavior on an unrecognized answer, multiple transactions sharing a key, a
stale question referencing a since-changed transaction, and — the concern
the whole feature exists to address — that an answered question does not
regenerate on the next recompute.

`streamlit.testing.v1.AppTest` drove the actual `app.py` script headlessly
against 6 of the real TD Trees statement PDFs (uploaded via `AppTest`'s
`file_uploader.upload()`, which accepts real bytes): the app rendered with
no exception, session state correctly persisted the working transaction list
across a rerun without re-parsing, and clicking the Apply button round-
tripped cleanly. This caught one real bug before it shipped: a message
meant to show when no answers were entered was being wiped out by an
unconditional `st.rerun()` immediately after it, so it never actually
rendered in a real browser — fixed by only rerunning when something was
actually applied.

**Known test gap:** this Streamlit version's `AppTest` has no typed
accessor for `st.data_editor` (unlike `st.button`, `st.selectbox`, etc.), so
editing a cell and confirming the exact value that reaches `answer_applier`
end-to-end could not be automated — the merge-by-`item_id` logic itself is
covered directly in `tests/test_answer_applier.py`, but the last mile (does
typing "Personal" into the live grid actually reach that logic) needs a
manual click-through the first time this ships.

---

## Six fixes from the 2026-09-10 diagnostic review

Hans reviewed the workpaper produced after the client-answer feedback loop
shipped, diagnosed six specific issues (without changing any code), and
approved all six for the same session. Each is described with the real
numbers that motivated it; each is verified against the same 34 real files.

1. **"Card Credit" split into its own category.** Was folded into
   `Non-P&L: Tax Refund / Reimbursement`; on the real engagement 15 of 16
   items in that bucket were vendor purchase credits (Home Depot, Amazon,
   O'Reilly) and only 1 an actual IRS refund — misleading to a preparer
   skimming the sheet. Now `Non-P&L: Vendor Purchase Credit`.

2. **Credit card reconciliation redesigned around payments vs. charges,
   not deposits vs. withdrawals.** `is_deposit` reflects the business's own
   cash flow; on a credit card almost nothing is ever "money into the
   business" in that sense (a payment and a new charge are both
   `is_deposit=False`), so comparing declared payments against a
   near-always-zero extracted-deposits figure flagged 9 of 11 real Capital
   One statements as a discrepancy regardless of whether anything was
   actually missing. `extracted_deposits`/`extracted_withdrawals` for a
   `credit_card` document are now computed from category
   (`Non-P&L: Credit Card Payment` vs. everything else) instead of
   `is_deposit`. Verified: `deposit_difference` is now `0.0` or `None` on
   9 of 11 real statements (was a discrepancy on all 9). The residual
   `withdrawal_difference` gaps that remain ($44–$137 on most files) are a
   separate, already-documented issue — the Account Summary box's
   "Interest Charged"/"Fees Charged" sub-lines aren't always captured due to
   a two-column PDF merge artifact that varies by statement — not addressed
   here; two of the six could not both be root-caused to the exact same
   two-column-merge cause in the time available, and this one was scoped
   narrower (the comparison axis, not the box-capture completeness).
3. **Cleared-checks regex now accepts a missing check number and an
   asterisk before the amount.** Two real statements list a check with
   *no* check number at all between date and amount (`09/17  8,000.00`);
   one marks a "Break In Check Number Sequence" with a literal asterisk
   between the number and the amount (`1272 * 260.00`). Both silently
   failed to match before. Recovers $9,500 (Sept), $4,000 (June), $260
   (Dec) of real check expense that was previously missing entirely — all
   three statements now show `Reconciled`.
4. **"Returned Deposit Item" no longer forced `is_deposit=True`.** Its own
   description contains the word "deposit", which `DEPOSIT_KEYWORDS`
   matched regardless of the fact that a real statement lists it inside the
   WITHDRAWALS section (a bounced check correctly reduces the balance).
   Added to `WITHDRAWAL_KEYWORDS`, checked first. Zero P&L dollar impact
   (already excluded via its own Non-P&L category either way) — this was a
   `Deposit?` column / reconciliation accuracy fix, and it closed the
   remaining Regions discrepancy exactly (the $2,100 mirrored over/under
   extraction is now gone).
5. **Repeated vendor charges now group into one client question.** 109
   individual "Cash App" line items on the real engagement were only 7
   distinct recipients. `core/vendor_grouping.py` groups by vendor (P2P
   apps grouped by service + recipient specifically, so "Cash App*dakota"
   and "Cash App*moe" never merge) and a new sidebar **Client Question
   Materiality Threshold** drops a group's question entirely when its
   total doesn't clear it — applied to the group's total, not each charge,
   so several small charges that add up past the threshold still ask.
   `transaction_key` (singular) became `transaction_keys` (a list,
   universally, even for the ungrouped question types) so one answer can
   apply to every transaction in a group.
6. **"Owner Draw" added as an Expense Verification answer.** The largest
   Cash App group by dollar amount ("dakota", $14,656 across 50 payments)
   is very likely the business owner's own name appearing throughout the
   statements ("Dakota C Gearheart") — neither a business expense nor a
   personal one in the sense those two answers mean. Recategorizes to the
   existing `Non-P&L: Owner Draw / Contribution` category.

### Net effect on the real engagement

- Reconciliation: 14 of 34 documents in `Discrepancy` → 10 (all four
  Regions bank-statement discrepancies resolved; the residual 9 Capital One
  + 1 unrelated are the separately-scoped Account Summary box gap noted in
  fix #2).
- Client questions: 109 individual Expense Verification items → 7 grouped
  questions.
- Total Expenses: $225,002.29 → $238,762.29 (+$13,760.00, exactly the three
  previously-unextracted checks from fix #3 — a more complete number, not a
  new error).
- Net Profit swung from **+$10,943.90 to –$2,816.10** as a direct result of
  including those previously-invisible checks. Flagged explicitly because
  it changes the bottom-line sign, not just a line-item detail.

### Known nuance not addressed

The P2P vendor-group key doesn't distinguish a charge from a same-vendor
refund/credit (e.g. "Card Purchase Cash App*dakota" and "Card Credit Cash
App*dakota" group together), so answering a group's question also
recategorizes any refund transaction it swept in. Zero P&L impact (a refund
was already excluded via its own Non-P&L category before the group answer,
and stays excluded after, just filed under a different Non-P&L label) — a
cosmetic relabeling, not a correctness issue, but worth knowing about before
reading too much into which Non-P&L subcategory a given refund landed in.
