# Changelog

Dated history of what changed and why. Newest first. Real dollar figures
below come from testing against a real client engagement (private, not
committed to this repo); identifying details are generalized.

See [ARCHITECTURE.md](ARCHITECTURE.md) for how the current system works,
and [README.md](README.md#known-limitations--roadmap) for what's still
open.

## 2026-10-01 — Reconciliation now actually reconciles Chase (1 of 13 -> all)

Raised directly by the client: the numbers the tool produced for Chase and
other banks were close (~95%) but not exact, and the question underneath
that -- "how does the app even know when it's wrong?" -- pointed straight
at a gap flagged weeks earlier and never actually closed: Reconciliation
QC reported only 1 of 13 real Chase statements as "Reconciled," with the
extraction underneath already confirmed entirely correct.

Root cause: Chase doesn't print one "Total Withdrawals" line -- it
declares withdrawals as several of its own category totals ("ATM & Debit
Card Withdrawals," "Electronic Withdrawals," separately "Checks Paid" and
"Fees"). `_capture_balances` used first-occurrence-wins for every declared
figure, which is correct for beginning/ending balance (the same true
value repeated in page footers) but wrong here -- it captured only the
first category and silently dropped the rest, so `declared_withdrawals`
was a fraction of the true total and a fully correct extraction still
failed reconciliation.

Fixed generally, not with a Chase-only patch:

- `total_deposits`/`total_withdrawals`/`total_checks`/`total_fees` are now
  **summed across every matching declared line**, while
  `beginning_balance`/`ending_balance` keep first-occurrence-wins (they
  need the opposite rule -- same true value, not a sum of categories).
  Generalizes to any bank that splits these into category lines, not just
  Chase, present or future.
- `BankPDFParser._extraction_looks_reliable` had the identical structural
  gap independently: it checked declared deposits/withdrawals *first* and
  only fell back to the beginning/ending balance equation when those were
  completely absent -- a bank whose declared totals are captured but
  *incomplete* (exactly Chase's shape above) passed on bad data and never
  reached the stronger, far more bank-agnostic balance-equation check.
  Fixed to run every check the statement's declared figures make
  possible and require **all** of them to agree, matching the discipline
  `ReconciliationChecker` already used.

No bank profile needed changing -- there are no learned profiles yet
(`bank_profiles/` only holds its own README); Chase, Regions, and Capital
One support all lives in the universal balance-capture logic this fix
touches, so every bank already supported benefits automatically, not only
Chase. Verified against the real Chase September summary (reconstructed
from already-debugged figures, never the client's file): declared
withdrawals now sum to $28,134.89 (previously $2,793.00), and a correctly-
extracted statement reports "Reconciled."

See
[ARCHITECTURE.md](ARCHITECTURE.md#reconciliation) for the full design.

## 2026-10-01 — Multiclass document processing: checks, invoices, receipts

Widened the AI escalation path from "assume every unrecognized upload is a
mis-parsed bank statement" into a genuine multiclass IDP (Intelligent
Document Processing) system, after the client asked directly to build the
app around one: a document the rules engine can't read is now classified
first (`bank_statement` / `check` / `invoice` / `receipt` / `unknown`) and
extracted accordingly, instead of being force-fit into the bank-statement
pipeline and silently misread.

- **`core/idp_extractor.py`** (replaces `core/llm_extractor.py`, now
  deleted as dead code) does classification and extraction in one AI
  call, via a `strict: true` Structured Outputs JSON schema covering all
  four document shapes. A bank statement's transactions still go through
  the ordinary rule-based `TaxCategorizer`, same discipline as before.
- **Per-row balance verification.** A bank statement's extraction is now
  checked two ways, never the model's own self-reported
  `quality_validation.mathematically_balanced` alone: the existing
  aggregate "declared vs. extracted" check, plus -- when the statement
  prints a running balance next to each transaction --
  `verify_bank_statement_balance_chain`, an independent Python re-check
  that walks the opening balance forward through every transaction and
  compares against each row's own resulting balance. This is strictly
  stronger than the aggregate check alone: it would have caught the Wells
  Fargo running-balance-misread-as-amount bug (see the entry below) row by
  row, the moment it happened, rather than only at the total.
- **`core/check_matcher.py`** (new) fills in the payee on a "Check #NNNN"
  line item a bank statement's own cleared-checks listing never prints a
  name for, using a separately-uploaded check-copy classified as `check`.
  Matches by check number first, amount only as a fallback and only when
  it uniquely identifies one still-unlabeled check -- never guesses when
  two checks share an amount. Closes a gap raised directly by the client
  weeks earlier: *"las copias de cheques son necesarias, porque cuando el
  cliente paga con cheque hay que saber quién fue el proveedor."*
- **New "Supporting Documents" tab** (only appears when there's something
  in it) shows matched/unmatched check copies and any invoices/receipts
  uploaded for reference -- none of this counts toward Gross Receipts or
  Total Expenses; invoices/receipts are not auto-matched to transactions
  (kept as supporting documentation only, a smaller scope than check
  matching since no one has asked for automatic invoice reconciliation
  yet).

Deliberately **not** a classify-every-upload system: this entire chain is
reached only where it already was -- after the rules engine (and any
learned bank profile) already failed its own reliability check. A
recognized Regions/Capital One/Chase statement, or any bank whose format
has already been learned, never reaches it and costs nothing, same as
before this change.

See
[ARCHITECTURE.md](ARCHITECTURE.md#unrecognized-documents-the-multiclass-self-teaching-fallback)
for the full design and
[README.md](README.md#check-copies) for how to use check matching.

## 2026-10-01 — Fixed: the AI fallback never ran for a real Wells Fargo client

Root cause of "I uploaded the new bank's statements with the API key
configured and got the exact same broken numbers as before": a real Wells
Fargo statement prints its deposit/withdrawal recap as "Deposits/Additions"
and "Withdrawals/Subtractions" -- no "Total" prefix, the exact phrase
`BALANCE_PATTERNS` requires -- so `total_deposits`/`total_withdrawals` both
silently stayed `0.0` even though the statement's own beginning/ending
balance *did* get captured correctly. `_extraction_looks_reliable()` had
no third tier for that case: with nothing declared to compare against, it
fell back straight to "did we extract at least one transaction," which a
badly wrong extraction still passes trivially.

And this statement's extraction genuinely was badly wrong, for an
unrelated reason: Wells Fargo prints a running daily balance on the same
line as most transactions, and the universal date+description+amount
regex always grabs the *last* money-shaped number on a line -- which was
the running balance, not the transaction amount. One statement's
"expenses" came out over $200,000 this way. Both bugs compounding meant
the AI fallback -- specifically built to catch exactly this kind of
garbage -- never even ran, on any of the 12 statements tested.

Fixed generally, not by hand-patching Wells Fargo's layout: added a middle
tier to `_extraction_looks_reliable()` that checks the statement's own
`beginning_balance + deposits - withdrawals == ending_balance` whenever
beginning/ending balance were captured but total_deposits/total_withdrawals
weren't -- a check this method already had the data for but never used.
This is what actually fixes it, deliberately: it strengthens the
self-check for *every* bank with this declared-totals shape, not just
Wells Fargo, consistent with "no more fixes needed per bank, ever" rather
than adding a Wells-Fargo-specific regex. Confirmed on the real statements:
all 12 now correctly fail the self-check and escalate -- the fallback
itself hasn't been tested yet against a real OpenAI response; that's the
next step now that the escalation actually triggers.

## 2026-10-01 — AI fallback switched from Anthropic to OpenAI

The AI fallback (`core/llm_extractor.py`, `core/bank_learner.py`) ran on
Claude (Anthropic) until now. Switched to OpenAI, driven by a budget
constraint, not a technical one -- Tax Savers has OpenAI credits already
available and no budget for a separate Anthropic key.

Both modules were rewritten against OpenAI's Responses API
(`client.responses.create`), not Chat Completions: Chat Completions
doesn't accept an inline base64 PDF (only a `file_id` from a prior
upload), while Responses does, via an `input_file` content part. Verified
directly against the installed SDK's own type definitions and OpenAI's
docs before writing the code, not assumed from training knowledge -- the
model lineup (`gpt-6-astra` / `gpt-6.1-sol` / `gpt-6-luna`) postdates this
project's working knowledge, so pricing and parameter shapes were checked
live rather than guessed. Structured output uses the same
`strict: true` JSON-schema mechanism conceptually as Claude's strict tool
use, just under OpenAI's own parameter names (`text.format`, read back via
`response.output_text`). Picked `gpt-6-luna` -- OpenAI's cheapest current
tier, explicitly positioned for "focused, high-volume tasks" -- over the
flagship tier used for the equivalent Claude call, given this is a literal
transcription task and the whole point of this migration was cost.

Both modules' request/response shapes changed; their test doubles
(`tests/test_llm_extractor.py`, `tests/test_bank_learner.py`) were
rewritten to mock the OpenAI client instead of Anthropic's, never calling
either real API. `ANTHROPIC_API_KEY` is replaced everywhere by
`OPENAI_API_KEY` (Streamlit secrets, `.streamlit/secrets.toml.example`,
`app.py`'s sidebar). `requirements.txt` swaps the `anthropic` package for
`openai`.

Not yet verified against a real OpenAI key or a real statement -- next
step once Lindsay's key is in Streamlit secrets.

## 2026-10-01 — Learned bank profiles now survive a redeploy

A real client statement from a bank outside Regions/Capital One/Chase was
tested and came back badly wrong (every number treated as an expense,
daily balances read as transactions) -- the same symptom class as the
original Chase bug. Root cause this time: the AI fallback that's supposed
to catch exactly this case never had a chance to run, most likely because
no `ANTHROPIC_API_KEY` was configured in that deployment yet (not yet
confirmed with the actual files).

Investigating that raised a separate, real gap regardless of the immediate
cause: even when the self-teaching fallback (see the 2026-09-17 entry
below) *does* learn a new bank successfully, the profile it saves
(`core/bank_profiles.py`) only lives on the currently-running instance's
local disk. Streamlit Community Cloud's filesystem is ephemeral and
re-clones the repo from scratch on every deploy -- so a learned profile
was quietly being thrown away on the next `git push`, forcing that bank to
be re-learned (and re-billed) from scratch. Not caught until now because
no bank had actually gone through the full learn-then-redeploy cycle yet.

Fixed with a new step at the end of the learning pipeline
(`core/github_profile_sync.py`): once a profile is saved locally, the app
also commits it straight back to `bank_profiles/` on this repo's `main`
branch via the GitHub Contents API, using a `GITHUB_TOKEN` scoped to only
this repository with only "Contents: Read and write" permission. Without
that token configured, nothing breaks -- the profile still works for the
rest of the current instance, exactly as before this fix, it just won't
survive the *next* deploy either. See
[ARCHITECTURE.md](ARCHITECTURE.md#unrecognized-documents-the-multiclass-self-teaching-fallback)
and [README.md](README.md#unrecognized-bank-formats) for setup.

Still open: confirming with the actual failing bank's statements whether
the root cause really was a missing API key or a genuine extraction bug —
next step once those files are available.

## 2026-09-17 — Multiple clients per session, and explicit "Start Processing"

- **Several clients can now be open in the same browser session at once**,
  switchable from the sidebar without losing work or re-uploading -- raised
  directly by the client ("tengo como 20 clientes yendo a la vez... tener
  que volver a entrar a este cliente en particular"). Everything that used
  to live flat on `st.session_state` (uploaded files, extracted
  transactions, applied answers, metadata) now lives in a
  `st.session_state.clients[key]` dict per client instead. Deliberately
  scoped to the browser session only, no server-side storage -- closing the
  tab still loses everything, keeping the tool's "nothing is stored outside
  your browser tab" guarantee intact. One real bug found and fixed along
  the way: Streamlit only keeps an uploaded file's bytes alive for a widget
  actually re-instantiated on a given run, so a client's own file uploader
  comes back empty after switching away and back -- the dashboard's
  "there's something to show" check now looks at whether that client has
  *previously processed data*, not at the uploader's current state alone,
  so nothing is actually lost, only the raw file bytes (already fully
  extracted by then). See
  [ARCHITECTURE.md](ARCHITECTURE.md#the-streamlit-session-pattern).
- **Uploading files no longer starts processing automatically.** A new
  "Start Processing" button gives the preparer explicit control over when
  extraction and categorization actually run, instead of it firing the
  instant a file is dropped in.

## 2026-09-17 — Every dashboard figure now shows how it was calculated

Widened the Gross Receipts breakdown (added the same day, below) into a
general rule: "I want all calculations to show how they were calculated, so
anyone reading can understand what was done, without having to invent
anything, and without the AI or anyone hallucinating."

- **Total Expenses** and **Net Profit / (Loss)** each get the same "How is
  this calculated?" expander Gross Receipts already had: Total Expenses
  breaks down by source file, by category, and largest individual expenses
  (with an explicit note that Line 24b/meals transactions are shown at their
  full amount there, not the 50%-limited figure the dashboard metric uses);
  Net Profit shows the plain formula with the two actual numbers plugged in.
- **Schedule C Summary tab** gained a line-item inspector: pick any line
  (including Line 1) and see the exact transactions that sum to it, with the
  same 50%-meals note where relevant.
- **Non-P&L Transfers tab** gained a caption naming what each possible
  category means, next to the list that already shows every excluded
  transaction's own category as its reason.
- Every breakdown is the literal already-computed transaction data, grouped
  and sorted -- never a separately-written description of it, from the AI
  fallback (see below) or otherwise, so there's nothing in any of these
  explanations that isn't traceable to an actual transaction.

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
  [ARCHITECTURE.md](ARCHITECTURE.md#unrecognized-documents-the-multiclass-self-teaching-fallback)
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
