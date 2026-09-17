# Changelog

Dated history of what changed and why. Newest first. Real dollar figures
below come from testing against a real client engagement (private, not
committed to this repo); identifying details are generalized.

See [ARCHITECTURE.md](ARCHITECTURE.md) for how the current system works,
and [README.md](README.md#known-limitations--roadmap) for what's still
open.

## 2026-09-17 — Meeting follow-up: transfer/asset rule, bulk answers, Gross Receipts breakdown

Three items raised directly in a Tax Savers x Spectr sync, fixed the same
day:

- **An online transfer over the de minimis threshold no longer generates a
  spurious "is this an asset purchase?" question.** `core/exception_analyzer.py`'s
  fixed-asset check only looked at amount, never at whether the transaction
  had already been excluded from the P&L as `Non-P&L: Internal Transfer` (or
  any other Non-P&L category) -- so a same-owner transfer well above $2,500
  still got asked about, purely because of its size, after already being
  correctly excluded from the totals. The fix is scoped to the category, not
  a new "contains the word transfer" check, so it applies to any client's
  bank wording rather than special-casing one account's phrasing. Verified
  against the real 12-month Chase engagement: 17 of this client's own
  recurring transfers would have generated this exact false question before
  the fix. The personal-expense check is deliberately untouched -- an
  auto-detected Non-P&L transaction that also matches a personal keyword is
  still flagged, as a safety net against a wrong auto-categorization slipping
  through unreviewed.
- **Bulk answer.** Each question category's table now has a checkbox column
  plus a "pick one answer, apply to every checked row" control, instead of
  requiring the same answer be selected by hand in each row individually
  (raised directly: "if Lindsay sees these are all personal transfers, she
  can just bulk select and change").
- **Gross Receipts is no longer a single unexplained number.** A "How is
  Gross Receipts calculated?" expander next to the dashboard metric shows
  the plain-English formula, a breakdown by source file, and the largest
  individual deposits that make it up -- so a wrong-looking figure can be
  traced at a glance instead of requiring a dig through the full line-item
  list (raised directly after the Chase bug above: "we need to show a
  formula or where it's getting that number from").

## 2026-09-17 — Chase Gross Receipts bug, and a self-teaching fallback for new banks

- **Fixed: Gross Receipts reported at ~$1.3M on a real client's Chase
  statements where the true figure was $142,223.72.** Root cause: a "Total
  ATM Withdrawals & Debits $0.00" / "Total Card Deposits & Credits $0.00"
  rollup line, printed twice per statement in Chase's own summary
  mini-table, contains the exact same wording the section-header detector
  looked for -- it kept flipping the parser's internal section state, which
  landed on "deposit" right before the statement's daily-balance table (no
  keywords of its own to correct a wrong section) and turned every daily
  balance into a phantom deposit. Also recognized Chase's own header
  vocabulary directly ("Deposits and Additions," "ATM & Debit Card
  Withdrawals," "Electronic Withdrawals," "Daily Ending Balance") instead of
  relying on keyword-guessing alone. Verified against the real 12-month
  engagement: extraction now matches the bank's own per-statement "Deposits
  and Additions" figures to the cent for most months.
- **New: an unrecognized bank's statement format is no longer a manual
  fix.** Previously, a new bank meant someone had to notice a wrong number,
  diagnose the statement's actual wording, and patch `pdf_parser.py` by
  hand -- exactly what the Chase bug above required. Now `parse_pdf()`
  escalates automatically: try the rules engine -> try every bank format
  already learned (`bank_profiles/*.json`, free) -> ask Claude to transcribe
  the one unrecognized statement (`core/llm_extractor.py`, a few cents) ->
  if that reconciles against the statement's own declared totals, ask
  Claude to name that bank's header vocabulary and verify the *ordinary
  rules engine* reproduces the same numbers with it before saving it as a
  reusable profile (`core/bank_learner.py`). A bank costs a few cents once
  (maybe twice), then nothing, forever, automatically. See
  [ARCHITECTURE.md](ARCHITECTURE.md#unrecognized-bank-formats-the-self-teaching-fallback)
  for the full design and
  [README.md](README.md#unrecognized-bank-formats) for how to turn it on
  (`ANTHROPIC_API_KEY` in Streamlit secrets; off by default with no key
  configured, and a sidebar toggle either way). Categorization stays
  100% rule-based regardless of which path extracted a transaction -- the
  model is only ever used to transcribe what's printed, never to decide a
  tax category or a dollar total.

## 2026-09-14 — Password gate and remaining Spanish text

- Added a single shared-password gate in front of the whole app
  (`check_password()` in `app.py`), so the public app URL alone isn't
  enough to use the tool — the password lives in Streamlit secrets, never
  in code. See [README.md](README.md#quick-start) for how to set it
  locally and on Streamlit Community Cloud.
- Translated the last remaining Spanish strings to English: the processing
  panel's 4 step labels and per-file status lines (`theme.py`, `app.py`),
  and the audit-detail expander label — all UI-facing text is now English
  end to end, verified by driving `AppTest` through a real sample-file
  upload and scanning the rendered output for Spanish characters.

## 2026-09-11 — Visual redesign

- Dashboard rebuilt around the tool's primary success metric: speed from
  upload to a sendable client question list. **Client Inquiry Questions**
  promoted to the first, most prominent tab. Debug/diagnostics view, a
  vendor-grouping view superseded by the automatic grouping already in
  Client Inquiry Questions, and duplicate static exception lists removed
  from the default view; line-item search, per-statement reconciliation
  detail, and large-transaction review moved behind a collapsed "audit
  detail" section.
- Added a processing-status panel with a per-file progress bar and 4 visible
  pipeline steps (extraction → 12-month coverage → reconciliation → question
  generation), so a large upload doesn't look frozen.
- Applied a full visual identity (`theme.py`, `.streamlit/config.toml`) —
  brand colors, typography, and component styling. Pure presentation; no
  change to any `core/` logic. See
  [ARCHITECTURE.md](ARCHITECTURE.md#visual-theme) for two hard constraints
  found along the way (dataframe headers render on `<canvas>`, not styleable
  DOM text; `st.container(key=...)` vs. a raw `st.markdown()` div for a
  themed panel that needs to actually wrap its children).
- Fixed, found by live testing after the redesign shipped: dataframe header
  text was unreadable against a dark header background (canvas text always
  renders in the app's single global text color — no separate header-text
  key exists), and `SelectboxColumn` cells showed no dropdown-arrow
  affordance until opened for editing (no icon parameter exists on that
  column type; a `▾` was added to the column label text itself instead).

## 2026-09-10 — Diagnostic review and six fixes

A hand review of a produced workpaper (no code changes during the review
itself) surfaced six issues, each verified against the real 34-statement
engagement before and after:

1. **Vendor purchase credits were mislabeled as tax refunds.** A combined
   `Non-P&L: Tax Refund / Reimbursement` category had 15 of 16 real items be
   vendor credits (money back on an earlier purchase) and only 1 an actual
   IRS refund — split into its own `Non-P&L: Vendor Purchase Credit`
   category.
2. **Credit card reconciliation redesigned** around payments vs. charges
   instead of deposits vs. withdrawals — see
   [ARCHITECTURE.md](ARCHITECTURE.md#reconciliation). Went from flagging 9
   of 11 real card statements as a discrepancy (regardless of whether
   anything was actually missing) to 0.
3. **Cleared-checks parsing now tolerates a missing check number and an
   asterisk** ("Break In Check Number Sequence") between the number and the
   amount — both silently dropped the check line entirely before. Recovered
   roughly $13,700 of real check expense across three statements that
   previously showed $0 for that check.
4. **A bounced-check reversal ("Returned Deposit Item") was being read as a
   deposit** because its own description contains the word "deposit," even
   though it's listed inside the statement's withdrawals section. Zero P&L
   dollar impact (it was already excluded via its own category either way);
   fixed a reconciliation-accuracy and audit-trail issue.
5. **Repeated charges to the same vendor now group into one client
   question**, with a configurable materiality threshold. On the real
   engagement this collapsed 109 individual P2P-payment-app line items (7
   distinct recipients) into 7 questions instead of 109.
6. **"Owner Draw" added as an answer choice.** The largest of those 7
   recipient groups by dollar amount turned out to almost certainly be the
   business owner's own name, paying himself — neither a business expense
   nor a personal one in the sense the other two answers mean.

Net effect on the real engagement: reconciliation discrepancies dropped
from 14 of 34 statements to 10; client questions from 109 to 7; Total
Expenses increased by the amount of the three previously-missing checks
(a more complete number, not a new error) — which flipped reported Net
Profit from positive to negative. Called out explicitly at the time since
it changed the bottom-line sign, not just a line-item detail.

## 2026-09-10 — Client-answer feedback loop

Closed a gap identified directly by the client (Tax Savers CPA): once a
customer answers the auto-generated questions, there was no way to feed
those answers back into the workpaper so totals recalculated.

Built: a stable transaction identity
(`core/transaction_utils.py`, date + payee + amount + source file, no
database needed), every generated question now carrying that identity
(`core/question_generator.py`), an answer-application module
(`core/answer_applier.py`) that recategorizes or confirms a transaction per
a fixed answer vocabulary and never guesses at an answer outside it, and the
editable Client Inquiry Questions UI with an **Apply Client Answers &
Recalculate Workpaper** button. See
[ARCHITECTURE.md](ARCHITECTURE.md#client-questions-and-the-answer-loop) for
the full design, including why a transaction is skipped from re-flagging
based on an explicit `client_confirmed` stamp rather than on its category
text.

Verified with `streamlit.testing.v1.AppTest` driving the real app against
real statement files — caught one bug before it shipped (a "no answers
entered" message being wiped out by an `st.rerun()` immediately after it,
so it never actually rendered in a browser).

Also fixed in this pass: a keyword-matching bug in
`core/exception_analyzer.py` where a short personal-expense keyword (`spa`)
matched inside an unrelated word (`space`), flagging a legitimate
self-storage transaction as a possible personal expense — mirrors an
earlier fix to the same class of bug in `core/tax_categorizer.py` (below).

## 2026-09-09 — Verification against real client statements

Ran the full pipeline against a real 12-month engagement (11 credit card
statements, 11 bank statements, 11 matching scanned-check images, and one
comparative QuickBooks P&L) and traced every reported dollar back to its
source. Reported Gross Receipts dropped from **$2,123,580.74 to
$503,833.34** (bank/card statements only — a P&L uploaded alongside the
statements it summarizes double-counts the same activity; still an open
item, see [README.md](README.md#known-limitations--roadmap)). Ten distinct
bugs found and fixed, each reproduced against the real files, with
regression tests added using synthetic fixtures that reproduce the same
shape (never the client's own files):

- A comparative P&L report (one column per year) always had its *last*
  printed number captured — the prior year's column, not the requested tax
  year — and several subtotal/derived lines (Total Income, Net Income, …)
  were summed on top of the detail lines they restate. One file alone
  contributed **$1,129,847.95** of phantom income this way. Rewritten to
  select the correct year's column, skip every subtotal line, and preserve
  the source's own sign.
- Two distinct causes of merged/glued statement text (a real gap the
  default extraction tolerance missed on one bank; literally zero gap in
  the source PDF's own text stream on another, confirmed at the
  character-coordinate level) broke keyword matching throughout — e.g. a
  $2,250 credit card payment matched a gas-station keyword as a substring
  of "mobile" and was booked as a car/truck expense instead of excluded.
  Fixed with a lower extraction tolerance plus a merge-recovery heuristic;
  closed the whole class of bug with word-boundary matching everywhere a
  keyword is checked (a short keyword like `bp` or `rent` must not match
  inside `current` or `different`).
- Cleared-checks listings were never captured, in any file — the scanned
  check images have no extractable text (the OCR gap), and the main
  statement's own text listing was in a different order/shape than the
  parser assumed. New two-per-line parser matches the statement's own
  declared check total to the cent.
- December transactions inside a January-closing statement were stamped
  with the wrong calendar year (the year in the filename, applied
  blanket-wide) — fixed by resolving each transaction's year from the
  statement's own closing month.
- Card interest charges have no date of their own on the statement and were
  dropped entirely, even after an earlier fix that stopped an
  already-matched line from being discarded (this line was never matched at
  all) — now captured, dated to the statement's closing date.
- A vendor refund/credit fell through Non-P&L detection to Gross Receipts
  because the description didn't contain the literal word "refund."
- The bare section header a real bank statement actually prints
  ("WITHDRAWALS") was never recognized by code that required
  "WITHDRAWALS & DEBITS" — every withdrawal fell through to the deposit
  default and was booked as income across every statement from that bank.
- A "DAILY BALANCE SUMMARY" table (repeating date/balance pairs) was read
  as transactions — one row produced a **$27,647.88 phantom withdrawal**;
  across one statement this fabricated over $150,000 of nonexistent
  expense.
- Balance/rollup capture for reconciliation was gated behind a check
  several real summary lines don't satisfy, and separately pre-empted by an
  unrelated section-header check matching the same substring — a fully
  correct extraction was reporting a reconciliation discrepancy regardless.

## 2026-09-09 — Initial gap analysis and first fixes

Reviewed the existing implementation against the full requirements
specification and fixed what testing against synthetic sample data
surfaced: a spreadsheet sign-convention bug that booked revenue as an
expense on a sheet using the opposite sign convention from the tool's
default, Non-P&L exclusion patterns that missed common real-world wordings
(`CREDIT CARD AUTOMATIC PAYMENT` didn't match a pattern requiring the exact
phrase "credit card payment"), a summary-line filter that discarded genuine
interest expense along with real summary text, a missing third confidence
state (`Unresolved`), reconciliation-to-statement-balances (not previously
implemented at all), and a de minimis threshold control that was collected
from the sidebar but never actually used.

Also: project scaffolding (`.gitignore`, this repo's git history, initial
`README.md`) and pushed to GitHub for the first time.
