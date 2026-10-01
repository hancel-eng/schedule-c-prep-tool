# Architecture

A technical reference for anyone picking up this codebase. Start with
[README.md](README.md) for what the tool does; this document is about how
it's built and why, for anyone about to change it.

## Contents

- [Pipeline, module by module](#pipeline-module-by-module)
- [Confidence states](#confidence-states)
- [Non-P&L exclusions](#non-pl-exclusions)
- [Reconciliation](#reconciliation)
- [Client questions and the answer loop](#client-questions-and-the-answer-loop)
- [The Streamlit session pattern](#the-streamlit-session-pattern)
- [Visual theme](#visual-theme)
- [Unrecognized documents: the multiclass self-teaching fallback](#unrecognized-documents-the-multiclass-self-teaching-fallback)
- [Testing](#testing)

## Pipeline, module by module

`app.py` is orchestration only — it calls into `core/` in order and renders
the result. Business logic lives in `core/` and has no Streamlit dependency,
so every module is independently unit-testable (and is: see
[Testing](#testing)).

1. **`core/deduplication.py`** — `FilenameDeduplicator`. Compares uploaded
   filenames only (never transaction contents — see
   [README.md](README.md#key-design-decisions) for why). A second file with
   an identical name is skipped and flagged; everything else keeps 100% of
   its transactions.

2. **`core/pdf_parser.py`** — `BankPDFParser`. Routes a PDF to one of three
   strategies by filename keyword:
   - a **P&L report** (QuickBooks-style, possibly comparative with one
     column per year — picks the column matching the requested tax year,
     skips subtotal/derived lines so they don't double-count, preserves the
     source's own sign),
   - a **credit card statement** (transaction list + an "Account Summary"
     box with Payments/Transactions/Fees/Interest, parsed separately by
     `_capture_credit_card_balances`),
   - or a **general bank statement / receipt** (section-tracked
     DEPOSITS/WITHDRAWALS/CHECKS, with a two-per-line cleared-checks table
     format and support for a check with no recorded number).

   Also resolves each transaction's calendar year from the statement's own
   closing month (parsed from the filename's `MM DD YYYY` convention) rather
   than blanket-stamping every transaction with one year — a billing cycle
   that closes in January routinely opens in December of the *prior* year.

   Text extraction goes through `_extract_page_text`, which lowers
   `pdfplumber`'s `x_tolerance` and then runs a merge-recovery heuristic
   (`_normalize_spacing`) for statements where the source PDF itself has no
   space between adjacent text runs (confirmed at the character-coordinate
   level on a real statement — two entirely different root causes that both
   look like "the words ran together").

   For the general bank-statement strategy specifically, an unrecognized
   bank's format no longer has to be fixed by hand — see
   [Unrecognized documents: the multiclass self-teaching fallback](#unrecognized-documents-the-multiclass-self-teaching-fallback)
   below, which covers this module together with `bank_profiles.py`,
   `llm_extractor.py`, and `bank_learner.py`.

3. **`core/bank_profiles.py`** — `BankProfile`, `load_bank_profiles`,
   `save_bank_profile`. A learned bank's section-header vocabulary, stored
   as JSON under `bank_profiles/` (committed to the repo — it's a bank's
   public statement wording, never client data), not new Python. Malformed
   or missing profile files are skipped, never raised, since a bad profile
   must not be able to break parsing for every other document.

4. **`core/idp_extractor.py`** — `classify_and_extract`. The paid
   escalation path for a document `pdf_parser.py`'s rules don't recognize
   at all. Sends it to OpenAI (Responses API) for classification
   (`bank_statement` / `check` / `invoice` / `receipt` / `unknown`) and
   type-specific extraction in one call, via a `strict: true` Structured
   Outputs JSON schema covering all four shapes at once — never
   categorization, for a bank statement or anything else. A bank
   statement's transcribed transactions still go through the ordinary
   `TaxCategorizer.categorize_transaction()` call, same as a rule-based
   extraction. Also exports `verify_bank_statement_balance_chain` (an
   independent, Python-side re-check of the per-row running-balance
   arithmetic the model was asked to self-verify — never trusts that
   self-report alone) and `bank_statement_payload_to_transactions` (maps
   the bank-statement shape into the same transaction-dict shape every
   other strategy produces). Needs `OPENAI_API_KEY` in the environment
   (see [README.md](README.md#unrecognized-bank-formats)); a missing key or
   package raises a clean, catchable `RuntimeError`, not a crash.

5. **`core/bank_learner.py`** — `learn_bank_profile`. Asks the same model
   to name the section-header phrases in a statement `idp_extractor.py`
   already classified and transcribed as a bank statement, returning a
   candidate `BankProfile`. Never trusted on its own — see the self-teaching
   section below for how the caller verifies it before saving.

6. **`core/github_profile_sync.py`** — `commit_profile_to_github`. Writes a
   verified, saved profile straight back to `bank_profiles/` on this repo's
   `main` branch via the GitHub Contents API, so it survives the next
   deploy — `core/bank_profiles.py`'s own local save only lasts for the
   currently-running instance, since Streamlit Community Cloud's filesystem
   is ephemeral and re-clones the repo from scratch on every deploy/restart.
   Needs `GITHUB_TOKEN` in the environment, a fine-grained GitHub token
   scoped to **only** this repository with **only** "Contents: Read and
   write" permission (see
   [README.md](README.md#unrecognized-bank-formats) for exact setup steps).
   Without it, never raises and never blocks the extraction that already
   succeeded — a learned profile just goes back to working-for-this-
   instance-only, the same degraded-but-functional state the feature was in
   before this module existed.

7. **`core/check_matcher.py`** — `match_checks_to_transactions`. Fills in
   the payee on a "Check #NNNN" transaction (already extracted from a
   statement's own cleared-checks listing, which never prints a payee)
   using a standalone check-copy classified by `idp_extractor.py`. Matches
   by check number first, amount only as a fallback and only when it
   uniquely identifies one still-unlabeled check transaction — never
   guesses when two checks share an amount. A check that can't be matched
   with confidence is returned, not dropped; `app.py` surfaces it in the
   Supporting Documents tab as unmatched.

8. **`core/spreadsheet_parser.py`** — `SpreadsheetParser`. Client Excel/CSV
   files vary in whether income is the positive or negative sign
   (`_detect_sign_convention` classifies each sheet as `standard`,
   `inverted`, or `unsigned` and normalizes to the tool-wide convention:
   income positive, expenses negative — the same convention the PDF parser
   uses).

9. **`core/totals_parser.py`** — `TotalsParser`. Maps client-provided
   year-end totals to Schedule C lines with special-rule flags (vehicle
   mileage vs. actual, the 50% meals limitation, the 1099 threshold, health
   insurance belonging on Schedule 1 not Schedule C, home office). Not
   currently reachable from the UI — see
   [README.md](README.md#known-limitations--roadmap).

10. **`core/tax_categorizer.py`** — `TaxCategorizer`. The keyword dictionary
   (`SCHEDULE_C_CATEGORIES`) and the Non-P&L exclusion patterns
   (`NON_PNL_PATTERNS`), both matched on **word boundaries**, not bare
   substrings — a short/generic keyword like `mobil` (the gas brand) must
   not match inside an unrelated word like `mobile`. `clean_payee_text`
   normalizes card-network sub-merchant descriptors (`*` as a word
   separator, e.g. `Google *Ads`) and apostrophes (a bank statement
   generally can't print one, so `O'Reilly` in the dictionary is matched the
   same way against a statement's `O Reilly`). Custom per-client rules
   (`custom_rules.json`) take precedence over the built-in dictionary but
   not over Non-P&L detection.

11. **`core/exception_analyzer.py`** — `ExceptionAnalyzer`. Scans every
   transaction into review queues: potential personal expense, potential
   fixed asset (over the de minimis threshold, or matching an
   asset-shaped keyword), 1099 accumulation per contractor, unusual/large
   transactions, and everything already excluded as Non-P&L. A transaction
   already stamped `client_confirmed=True` (see
   [Client questions and the answer loop](#client-questions-and-the-answer-loop))
   is skipped — this, not category text, is what lets an answered question
   stop regenerating.

12. **`core/vendor_grouping.py`** — `group_potential_personal`. Groups
   personal-expense-review transactions by vendor (P2P payment apps grouped
   by service *and* recipient specifically, so different people paid through
   the same app are never merged) and applies the materiality threshold to
   each group's total, not each individual charge.

13. **`core/question_generator.py`** — `ClientQuestionGenerator`. Builds the
   client-facing question list from the exception queues. Every question
   carries `transaction_keys` — a list, even for a single-transaction
   question — back to the exact transaction(s) it concerns (see
   `core/transaction_utils.py`: a stable identity built from
   date + payee + amount + source file, since there's no database or
   synthetic id in a stateless single-session app).

14. **`core/answer_applier.py`** — `apply_client_answers`. Applies an
   answered question to the transaction list. Answers are a fixed
   vocabulary per question type (`ANSWER_OPTIONS`), never free text, so this
   module never has to guess what an answer meant — an answer outside the
   vocabulary (or, for an uncategorized-expense question, not one of the
   tool's own recognized categories) is logged for the preparer to resolve
   by hand and changes nothing. Every transaction it touches is stamped
   `client_confirmed=True`.

15. **`core/reconciliation.py`** — `ReconciliationChecker`. See
    [Reconciliation](#reconciliation).

16. **`core/excel_exporter.py`** — `ExcelWorkpaperExporter`. Builds the
    seven-sheet workbook described in [README.md](README.md#what-it-produces).

## Confidence states

Every transaction is tagged one of three states by `categorize_transaction`,
and the categorizer never silently assigns a category it isn't confident in:

- **High Confidence** — matched a Non-P&L pattern, a custom rule, or a
  keyword.
- **Needs Review** — no match, but the payee text names something a
  preparer could plausibly recognize and resolve.
- **Unresolved** — the payee text carries no vendor identity at all after
  stripping generic tokens and digits (`POS DEBIT 4412`) — there's nothing
  to resolve it from without more information.

Anything short of High Confidence lands in the exception queue.

## Non-P&L exclusions

Detected via `NON_PNL_PATTERNS` and excluded from Gross Receipts / Total
Expenses entirely, each under its own category so a preparer can see *why*:
internal transfers, credit card payments, loan proceeds and repayments,
owner draws/contributions, tax refunds, vendor purchase credits (money back
on an earlier purchase — kept separate from tax refunds specifically,
because on one real engagement 15 of 16 items in a combined bucket turned
out to be vendor credits, reading as if there were 15 tax refunds), and
returned/reversed deposits.

## Reconciliation

`ReconciliationChecker.check_document` runs two independent checks per
statement:

1. **Statement integrity** — does the statement's own declared arithmetic
   hold (`beginning + deposits − withdrawals == ending`)? A failure here
   means a balance was misread, not that a transaction is missing.
2. **Extraction completeness** — do the transactions actually extracted from
   the document add up to the totals the statement itself declares? This is
   the check that catches a regex parser silently dropping rows.

A document that declares no balances is reported **Not Reconcilable**,
never treated as a pass.

Credit card statements need a different comparison axis than bank
statements: `is_deposit` reflects the *business's* cash flow (money into vs.
out of the checking account), and on a credit card almost nothing is
genuinely "money into the business" in that sense — a payment and a new
charge are both `is_deposit=False`. Comparing declared payments against a
near-always-zero deposits figure flagged real statements as a discrepancy
regardless of whether anything was actually missing. For a `credit_card`
document, extracted deposits/withdrawals are computed from **category**
(`Non-P&L: Credit Card Payment` vs. everything else) instead of
`is_deposit`.

## Client questions and the answer loop

The dashboard's **Client Inquiry Questions** tab is editable, not
read-only — each question category gets its own `st.data_editor` table with
an **Answer** column constrained to a `SelectboxColumn` (a fixed dropdown,
never free text):

| Question type | Answer choices | Effect |
|---|---|---|
| Expense Verification | Business / Personal / Owner Draw / Unclear | *Personal* and *Owner Draw* both exclude the group from the P&L (as different Non-P&L categories); *Business* upgrades confidence |
| Asset Purchase | Confirmed Asset / Not an Asset / Unclear | *Confirmed Asset* moves the transaction to Line 13 (Depreciation/Section 179) and logs the client's placed-in-service detail for the preparer |
| Uncategorized Expense | any real Schedule C category | Recategorizes and upgrades confidence — never guesses at an answer that isn't a category the tool itself recognizes |
| Form 1099 Verification | Filed / Will File / Not Required | Logged as a compliance note against the contractor; no dollar amount changes |

Clicking **Apply Client Answers & Recalculate Workpaper** merges the edited
answers back into the full question objects (which still carry
`transaction_keys`/`contractor`, stripped from the editor view to keep it
readable), calls `apply_client_answers`, stores the result in
`st.session_state`, and reruns.

## The Streamlit session pattern

Streamlit reruns the entire script top-to-bottom on every widget
interaction. Re-parsing every uploaded file on every interaction (e.g. every
time the preparer edits an Answer cell) would be slow and would silently
discard any correction applied a moment earlier, so `app.py` guards parsing
behind a signature of the uploaded files (name + size):

```python
upload_signature = tuple(sorted((f.name, f.size) for f in uploaded_files))
already_processed = client_state.get("upload_signature") == upload_signature
```

A genuinely new set of files needs a **Start Processing** click (below)
before it re-parses from scratch and resets `client_state["correction_log"]`
(a new file set starts with a clean slate). Otherwise,
`client_state["all_transactions"]` — the working, possibly client-corrected
transaction list — is reused. **Exceptions and questions are recomputed
fresh on every single run** once processed, regardless of whether this run
re-parsed, straight from the current transaction list: this is what makes
an applied answer's effect show up immediately and keeps an answered
question from reappearing.

### Explicit "Start Processing," not parse-on-upload

Uploading files no longer triggers extraction immediately — raised
directly: "quiero agregar un botón para iniciar a procesar los archivos
cargados, así el usuario tiene control de cuando iniciar a procesar." A
new/changed file set is described ("N file(s) ready") and waits for a
**Start Processing** button click; `st.stop()` halts the rest of the script
for that run if it hasn't been clicked yet, so nothing below (dashboard,
tabs, export) tries to render against data that doesn't exist yet. The
processing block ends with `st.rerun()` rather than falling through inline
to the dashboard code — deliberately, so there is exactly one code path
that reads and displays `client_state` ("already processed"), not two
slightly-different ones (`just finished processing` vs. `was already done`).

### Multiple clients in one session

Several clients can be open in the same browser session at once, switchable
without losing work or re-uploading — raised directly by the client
("tengo como 20 clientes yendo a la vez... tener que volver a entrar a este
cliente en particular"). `st.session_state.clients` is a dict keyed by an
internal id (`"client_1"`, `"client_2"`, ...), each value holding that
client's own metadata (name, tax year, thresholds) and processing results
(`all_transactions`, `correction_log`, reconciliation state, etc.) — the
same fields that used to live flat on `st.session_state` before this
feature, now namespaced per client instead. `client_state =
st.session_state.clients[active_client_key]` at the top of `app.py` is what
every section below reads from and writes to.

Two things make switching actually work, both non-obvious:

- **The sidebar metadata widgets (name, tax year, thresholds) are bound to
  fixed keys** (`client_name_input`, `tax_year_input`, ...), not to the
  active client directly. Switching clients means: copy whatever's
  currently in those keys into the client being switched *away from*
  (`_save_widgets_into_client`), then seed those same keys with the values
  of the client being switched *to* (`_load_client_into_widgets`) — done in
  that order, before the widgets re-render, or the previous client's edits
  would be silently lost. `_switch_to()` is the one place both happen
  together; every entry point (the switcher's `on_change`, "+ New Client")
  goes through it. The switcher selectbox intentionally does **not** use
  `format_func` to show a display label for a raw client-id option (a real
  bug surfaced doing it that way — a stale index lookup inside
  `streamlit.testing.v1`'s own `Selectbox.index` property, not reproducible
  outside tests but not worth the risk in production either) — it uses the
  *labels themselves* as the options, with a label→key lookup on change
  instead.
- **`st.file_uploader` is keyed per client** (`f"uploader_{active_client_key}"`),
  which is what gives each client its own independent set of uploaded
  files. But Streamlit only keeps an uploaded file's bytes alive for a
  widget that's actually re-instantiated on a given run — switch to a
  different client for even one rerun, and the previous client's uploader
  widget isn't part of that run's script execution, and its file bytes can
  be dropped. Confirmed switching away and back: the uploader comes back
  empty. This is harmless *only* because the dashboard's "is there
  something to show" gate is `uploaded_files or has_existing_data`, never
  `uploaded_files` alone — `has_existing_data` is
  `client_state.get("upload_signature") is not None`, independent of
  whatever the live uploader widget currently holds. The already-extracted
  transactions, reconciliation results, and questions all live in
  `client_state`, not in the uploader, so losing the raw uploaded bytes
  after processing costs nothing.

This is still consistent with "nothing is stored outside your browser tab"
(see [README.md](README.md#key-design-decisions)): `st.session_state.clients`
lives only as long as the browser tab does, in memory, never written to
disk or a database. Closing the tab loses every client's work, same as
before this feature — the guarantee this trades away is narrower: not
"nothing persists across a page refresh," but "nothing persists across
closing the tab."

## Visual theme

`theme.py` holds the design tokens (color, type, spacing, radius, elevation)
and the CSS injected once at the top of `app.py`
(`st.markdown(CSS, unsafe_allow_html=True)`). `.streamlit/config.toml`
carries the same palette into Streamlit's own theme engine (native widgets,
the sidebar, fonts via `theme.fontFaces`) so the CSS only needs to cover
what the theme engine doesn't reach. **Pure presentation** — nothing in
`theme.py` or the injected CSS touches parsing, categorization, or any other
`core/` logic.

Two hard constraints worth knowing before touching this:

- **`st.dataframe`/`st.data_editor` paint their content on `<canvas>`**
  (Glide Data Grid), not as styleable DOM text. `[role="columnheader"]` and
  `[role="gridcell"]` exist as real DOM nodes (for accessibility and
  hit-testing), but a CSS `color` rule on them never reaches the canvas
  paint pass — confirmed live with a minimal standalone probe. There is no
  dedicated header-text-color theme key in this Streamlit version either
  (checked every key in `streamlit/config.py`; only `dataframeBorderColor`
  and `dataframeHeaderBackgroundColor` exist for dataframes, and neither
  controls text). The header always renders in the app's single global
  `textColor`, so `dataframeHeaderBackgroundColor` is set to a *light* tint
  that reads correctly against that fixed dark text — don't reintroduce a
  dark dataframe header without solving this differently first. The same
  constraint means a `SelectboxColumn` shows no persistent dropdown-arrow
  affordance until a cell is opened for editing, and there's no icon
  parameter to add one — the `▾` on "Answer" columns is literal text in the
  column label, the one part of the header that *is* real rendered content.

- **`st.container(key="...")` generates a real CSS class**
  (`.st-key-<key>`) on the actual DOM node wrapping that container's
  children — this is what makes the processing-status panel's dark
  background genuinely wrap the step list and progress bar, unlike a raw
  `st.markdown()` `<div>`, which renders as a sibling of the widgets below
  it, not a parent.

- **`data-testid` selectors are undocumented internals** that change
  between Streamlit versions. `requirements.txt` pins the exact version
  used to verify every selector in `theme.py`; bump it deliberately, and
  re-check the selectors against the new version's frontend bundle (or a
  live render) before trusting them again.

## Unrecognized documents: the multiclass self-teaching fallback

The three-strategy PDF architecture (P&L report / credit card / general
bank) and the categorization engine (word-boundary keyword matching,
Non-P&L exclusion patterns) are general-purpose and apply to any uploaded
file. The specific *section-header vocabulary* inside the general-bank
strategy — what phrase means "this is the deposits section," "this is the
cleared-checks listing" — started out hardcoded for two banks (Regions,
Capital One), and this is exactly what broke on a real Chase statement:
Chase's own header wording ("ATM & Debit Card Withdrawals," "Electronic
Withdrawals") matched none of it, and — worse — a repeated "Total ATM
Withdrawals & Debits $0.00" rollup line in Chase's own summary table
happened to contain the same substring the header check looked for,
flipping the parser's internal section state to the wrong value and
turning every row of the statement's daily-balance table into a phantom
deposit (see [CHANGELOG.md](CHANGELOG.md), 2026-09-17 — Gross Receipts was
reported at ~$1.3M on a real engagement where the true figure was
$142,223.72).

Chase's specific header phrases are now hardcoded too, the same way
Regions' and Capital One's are — but the next unrecognized bank no longer
needs a human to notice, diagnose, and patch `pdf_parser.py` by hand before
it's usable. `BankPDFParser.parse_pdf()` runs an escalation chain instead:

1. **Rules engine** (`_parse_general_or_scanned_pdf`, free, instant) — the
   hardcoded vocabulary, same as before.
2. **Self-check** (`_extraction_looks_reliable`) — does what was extracted
   foot against what the statement itself declares (its own "Total
   Deposits" / "Total Withdrawals" figures, captured by
   `_capture_balances` independently of section-header detection)? If
   there's nothing declared to check against, falls back to "did we
   extract at least one transaction at all." A statement from an
   already-known bank passes this and stops here — nothing below ever
   runs for it.
3. **Learned profiles** (`core/bank_profiles.py`, free) — every
   `bank_profiles/*.json` file is tried in turn, feeding its phrases into
   the same rules engine (`_parse_general_or_scanned_pdf(..., profile=...)`
   — additive, not a separate code path) until one passes the same
   self-check.
4. **Multiclass AI classification** (`core/idp_extractor.py`, a few cents,
   only reached if 1–3 all failed) — one call to OpenAI classifies the
   document (`bank_statement` / `check` / `invoice` / `receipt` /
   `unknown`) and extracts it accordingly, via a `strict: true` Structured
   Outputs JSON schema covering all four shapes at once. This step exists
   because step 1–3 always assumed an unrecognized document *was* a
   mis-parsed bank statement — true for a genuinely new bank, but not for
   a document that was never a bank statement at all (a standalone check
   copy, an invoice, a receipt), which used to get silently force-fit into
   the bank-statement pipeline and misread. The branch on
   `detected_document_type`:
   - `bank_statement` — transcribed transactions (date, payee, amount,
     direction, and the running balance on that row if the statement
     prints one) go through the ordinary rule-based `TaxCategorizer`, same
     as every other strategy; an AI model is used for extraction only,
     never for deciding a tax category. Verified by **two** independent
     signals, neither the model's own self-reported
     `quality_validation.mathematically_balanced` alone: the same
     aggregate "declared vs. extracted" check every strategy is held to
     (step 2's `_extraction_looks_reliable`), plus, when the statement
     prints a running balance per row,
     `verify_bank_statement_balance_chain` -- walking `opening_balance`
     forward through every transaction and comparing against each row's
     own `resulting_balance`. This second check is strictly stronger: it's
     what would have caught Wells Fargo's running-balance-misread-as-
     amount bug (see [CHANGELOG.md](CHANGELOG.md), 2026-10-01) on its own,
     row by row, rather than only at the aggregate level after the fact.
   - `check` — extracted fields (drawer, payee, check number, amount) are
     never transactions on their own; see
     [Check copies: filling in a payee a statement never prints](#check-copies-filling-in-a-payee-a-statement-never-prints)
     below.
   - `invoice` / `receipt` — kept as a supporting document (issuer, date,
     total, line items), shown for the preparer's reference; not matched
     against transactions automatically.
   - `unknown` — flagged for manual review. Zero transactions are counted
     from it: the already-known-wrong rule-based garbage from step 1 is
     never allowed to flow through just because *something* was extracted
     from it.
5. **Learning** (`core/bank_learner.py`) — only attempted when step 4
   classified the document as a bank statement and it passed verification.
   Asks the same model to name this bank's section-header phrases, then
   re-runs the rules engine with that candidate profile against the *same*
   document and compares the result to the AI's own verified numbers. Only
   a profile that reproduces them exactly is saved to `bank_profiles/`;
   the app never trusts an unverified profile, and a profile that fails
   this check is simply discarded — that bank keeps using the AI path
   until a profile that passes exists. A failure anywhere in this step is
   swallowed (never allowed to take down an extraction that already
   succeeded at step 4).
6. **Persistence** (`core/github_profile_sync.py`) — only attempted once
   step 5 already saved the profile locally. Commits the same profile
   straight back to `bank_profiles/` on this repo's `main` branch via the
   GitHub Contents API, so it survives the *next deploy* — the local save
   in step 5 alone only lasts for the currently-running instance, since
   Streamlit Community Cloud's filesystem is ephemeral and starts from a
   fresh clone of the repo on every deploy/restart. Without this step (no
   `GITHUB_TOKEN` configured, or the request fails for any reason), the
   bank still works for the rest of this running instance; it just has to
   be re-learned — paying the AI cost again — the next time the app
   redeploys. Same swallow-and-continue discipline as step 5: a failure
   here never takes down the extraction itself.

Net effect: a brand-new bank costs a few cents the first time (maybe twice,
if the first attempt's profile doesn't reproduce cleanly), then $0 forever
after — across every future deploy, not just the current running instance
— with zero code changes required. The sidebar toggle ("Use AI for
documents...") and `OPENAI_API_KEY` gate step 4 entirely — with no key
configured, an unrecognized document stops after step 3 and is flagged in
its diagnostics notes for manual review, exactly as before this fallback
existed; nothing about a recognized bank's behavior changes either way,
and **this whole chain never runs at all for a document the filename-based
router already knows how to read** (a recognized bank/credit-card
statement, or a P&L report) — multiclass classification is additive to the
existing cost-free fast path, not a replacement for it.

### Check copies: filling in a payee a statement never prints

A bank statement's own cleared-checks listing (`CHECK_DETAIL` section in
`_parse_general_or_scanned_pdf`) only ever has a check number and an
amount — no payee, because the statement itself doesn't print one. Raised
directly by the client: "las copias de cheques son necesarias, porque
cuando el cliente paga con cheque hay que saber quién fue el proveedor."

When a separately-uploaded file is classified `check` (step 4 above), its
extracted `payee`/`check_number`/`numerical_amount` become a match
candidate for `core/check_matcher.py`'s `match_checks_to_transactions`,
run once per upload batch in `app.py` after every file has been parsed.
Matching is deliberately conservative, same "never guess" discipline as
everywhere else: check number first (exact, digits only), amount only as
a fallback when the number is missing *and* uniquely identifies one
still-unlabeled check transaction — two checks for the same amount in the
same statement (rent, a recurring vendor) is common enough that guessing
would be worse than leaving both unmatched. A check copy that can't be
matched with confidence is reported back as unmatched (shown in the
**Supporting Documents** tab, which only appears when there's something in
it) rather than silently attached to the nearest plausible transaction or
dropped.

## Testing

```bash
pytest
```

One test file per `core/` module, plus
`tests/test_real_world_regressions.py`: regression tests for bugs found by
running the pipeline against real client statements, each using a
**synthetic fixture built to reproduce the same shape** — never the
client's own files, which must never be committed (see
[README.md](README.md#data-handling)).

`streamlit.testing.v1.AppTest` was also used, ad hoc, to drive the full
`app.py` script headlessly against real files during development
(`AppTest.from_file(...)`, then `file_uploader.upload(filename, content,
mime_type)` with real bytes) — useful for catching Streamlit-specific
integration bugs (e.g. a message wiped out by an `st.rerun()` immediately
after it, never actually visible in a browser) that unit tests on `core/`
alone can't reach. Not currently wired into the `pytest` suite as a
standing test, since it depends on real files that aren't committed to the
repo.
