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
- [Saved work and reading in steps](#saved-work-and-reading-in-steps)
- [Visual theme](#visual-theme)
- [Hand corrections and the source of truth](#hand-corrections-and-the-source-of-truth)
- [Retired: rule-based readers and learned bank profiles](#retired-rule-based-readers-and-learned-bank-profiles)
- [Testing](#testing)

## Pipeline, module by module

`app.py` is orchestration only. Business logic lives in `core/` with no
Streamlit dependency, so every module is unit-testable on its own.

Since 2026-11-09 the split is strict: **the AI reads, rules decide.** The
AI only transcribes what is printed. Everything after that (signs, card
payments vs. refunds, categories, reconciliation, questions) is plain
Python.

1. `core/processing.py` `queue_files` skips a file whose name was already
   uploaded for this client (filename only, never transaction-level: that
   silently dropped real transactions when it was tried), saves the rest,
   and reads them in small saved steps (see
   [Saved work and reading in steps](#saved-work-and-reading-in-steps)).
2. `core/ai_reader.py` reads every file with OpenAI (`gpt-6-astra`):
   - `plan_parts` splits the file so no single AI answer is long enough to
     be cut off: PDFs into 4-page parts (sent as real PDF pages with
     `detail: "high"`, which is also the OCR path for scans), Excel/CSV/
     Word into 120-line parts (later parts repeat the first rows as
     `[CONTEXT]` so column headings stay clear), images whole. Supported:
     PDF, JPG/PNG/WEBP/GIF, XLSX/XLSM/XLS/CSV, DOCX. Anything else, or an
     empty, damaged, or password-locked file, gets a clear error code.
   - `read_part` makes one strict-JSON-schema call per part. A response
     whose `status` is `incomplete` is never used: the part is split in
     half and retried automatically, down to one page.
   - `read_files` runs 4 calls in parallel across all files and reports
     progress. A file with any failed part gets its error and contributes
     nothing (a half-read file gives wrong totals).
   - `merge_parts` stitches parts back into whole documents: a statement
     spread over several parts becomes one statement again; a new
     statement, another account, or two documents on the same pages stay
     separate. Repeated summary lines are kept once.
   The AI returns, per document: kind (bank statement, credit card
   statement, check, invoice, receipt, P&L, transaction list, other),
   balances as printed, each summary-box total as printed with whether it
   adds or subtracts, every row (date, description, positive amount,
   credit/debit, running balance if printed, section heading, check
   number, a category only if the source has its own category column),
   and anything it could not read.
3. `core/ai_errors.py` turns every failure (SDK exceptions, cut-off,
   refused, filtered, empty, or invalid answers, bad files) into one
   `AIExtractionError` with a stable code, a plain-English message, what
   to do, and the technical detail. The app shows each failed file in a
   red box.
4. `core/document_rules.py` applies the rules: signed amounts, card payment
   vs. refund (a card credit that the categorizer recognizes as a card
   payment stays a non-income transfer; any other card credit is money
   back), check rows labeled `Check #NNNN` so `core/check_matcher.py` can
   attach payees from check copies, categorization through
   `core/tax_categorizer.py`, verification (below), and month coverage
   (from transaction dates and statement period ends, tax year only).
5. `core/exception_analyzer.py`, `core/vendor_grouping.py`,
   `core/question_generator.py`, `core/answer_applier.py`, and
   `core/excel_exporter.py` are unchanged by the AI-first rebuild.

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

`core/document_rules.verify_statement` checks each statement against its
own printed numbers, using only what the AI transcribed and never the
model's own opinion of whether it balances:

1. **Balance equation:** opening balance plus and minus every extracted
   row must equal the printed closing balance (a credit card moves the
   other way: charges raise it, payments lower it).
2. **Summary totals:** extracted money in and money out must equal the
   summary box. Category lines (Chase's "ATM & Debit Card Withdrawals",
   "Electronic Withdrawals", ...) are summed; if the box also prints a
   "Total ..." line equal to its parts, only the total is used.
3. **Running balance:** where a balance is printed on a row, the
   extracted rows must reach it exactly; the first mismatch names the row.

Every available check must pass for "Reconciled". Any failure is a
"Discrepancy" with a plain-English explanation of how much is off and
where. A document with nothing to check against is "Not Reconcilable",
never a pass. Invoices and receipts get the same treatment (subtotal +
tax = total; line items = subtotal).

Verified on 2026-11-09 with one real Wells Fargo statement read by
`gpt-6-astra`: all three checks passed (69 rows). Deleting one row from
that data was caught by all three, naming the row.

## Client questions and the answer loop

The **3 · Questions** step is editable, not
read-only — each question category gets its own `st.data_editor` table with
an **Answer** column constrained to a `SelectboxColumn` (a fixed dropdown,
never free text):

| Question type | Answer choices | Effect |
|---|---|---|
| Expense Verification | Business / Personal / Owner Draw / Unclear | *Personal* and *Owner Draw* both exclude the group from the P&L (as different Non-P&L categories); *Business* upgrades confidence |
| Asset Purchase | Confirmed Asset / Not an Asset / Unclear | *Confirmed Asset* moves the transaction to Line 13 (Depreciation/Section 179) and logs the client's placed-in-service detail for the preparer |
| Uncategorized Expense | any real Schedule C category | Recategorizes and upgrades confidence — never guesses at an answer that isn't a category the tool itself recognizes |
| Form 1099 Verification | Filed / Will File / Not Required | Logged as a compliance note against the contractor; no dollar amount changes |

Clicking **Save answers** merges the edited
answers back into the full question objects (which still carry
`transaction_keys`/`contractor`, stripped from the editor view to keep it
readable), calls `apply_client_answers`, stores the result in
`st.session_state`, and reruns.

## Saved work and reading in steps

Streamlit reruns the whole script on every click. Since 2026-11-09 nothing
important lives only in `st.session_state`: every client's source of truth
is saved by `core/storage.py` and everything else is rebuilt from it on
every run (`workpaper.rebuild`, rules only, fast).

### Storage (`core/storage.py`)

Two stores with the same methods. `PostgresStore` (a Supabase project,
`DATABASE_URL` in secrets) is the real one; `MemoryStore` is used when no
database is set up (the sidebar warns that work isn't saved) and by the
tests. `MemoryStore` serialises through JSON exactly like the database, so
a value Postgres would refuse fails in the tests too.

Tables, in their own schema `schedule_c` (Supabase only exposes `public`
through its web API; row level security is also on, with no policies, so
only the app's own database login can read them):

| Table | One row per | Holds |
|---|---|---|
| `clients` | client | `state` JSON: name, tax year, thresholds, `files` (AI documents + untouched copy), `manual_edits`, `answered_questions`, `duplicates_flagged` |
| `pending_files` | file not yet read (or failed) | the file's bytes, part count, `failed` flag |
| `part_results` | part already read | the AI's answer for that part, or its error |

`client_record()` keeps only the saved fields and runs them through
`to_json_safe()` (pandas/numpy numbers → Python numbers, dates → text,
NaN → null; Postgres refuses NaN in JSON). The connection pool is created
once per app process (`st.cache_resource`); a connection problem becomes a
`StorageError` with code `DATABASE_UNREACHABLE`, a plain message, a fix,
and a technical detail with the connection string and password removed.

The app saves after every change: metadata widgets (`on_change`), a
finished file, hand fixes (`fix_statements_ui.render(..., save)`), and
applied answers. Each client's widgets have their own keys
(`client_name__<client_id>`), filled from the saved client the first time
they're shown, so switching clients needs no copying back and forth.

### Reading in steps (`core/processing.py`)

1. **Read** saves the uploaded files to `pending_files` (with their part
   count from `ai_reader.plan_parts`) and sets
   `st.session_state.reading[client_id]`.
2. Each script run while reading calls `read_next_batch`: up to 4 parts at
   once, oldest file first. Each worker thread saves its part's answer to
   `part_results` the moment it arrives, so the answer is kept even if the
   page stops waiting for it.
3. While waiting, the main thread redraws progress about once a second.
   That redraw is where Streamlit can end the run when **Stop** is clicked,
   so Stop takes effect within about a second. Parts already sent keep
   going in the background and save themselves; `_in_flight` (per app
   process) stops a quick **Continue** from sending them again.
4. `finish_ready_files` joins the parts of every fully-read file
   (`ai_reader.merge_parts`), saves them into the client's `files`, and only
   then deletes the file's bytes. A file with a failed part keeps its bytes
   and its good parts, records the error, and is marked `failed`; **Try
   again** re-reads only the failed parts.
5. The run ends with `st.rerun()` until nothing is waiting.

Reading only runs while that client's page is open in a browser: there is
no background worker. A tab closed mid-read shows **Reading is paused** with
the count of parts already saved, and **Continue reading** picks up there.

Known limit: two browser tabs editing the same client at the same moment
both save the whole client record, so the later save wins.

## Visual theme

Requested by the client: the app must be very easy to use, "as if for a
child or an elderly person", in plain everyday words. What that became:

- **One step on screen at a time.** `app.py` shows a "What to do next"
  card (`theme.next_step_html`), then a step picker
  (`st.segmented_control`, required, key `step__<client_id>`) with four
  steps: Add files, Check numbers, Questions, Results & download. Each
  label carries its status (✓, "2 to fix", "3 open"). The suggested step
  is chosen in a fixed order: no files → reading paused → files that
  failed → statements that don't add up → open questions → done.
- **Reading takes over the page.** While files are being read, only the
  reading panel and its **Stop for now** button show; Streamlit's greyed-
  out leftovers from the previous page are hidden by CSS
  (`[data-stale="true"]`) so Stop never looks disabled.
- **Type and color.** Atkinson Hyperlegible Next (made for low-vision
  readers) at 15px, compact buttons (38px tall), a deep pine
  sidebar, and color only for state: green done, amber needs you, red
  problem. One light theme on purpose, painted explicitly.
- **Numbers as HTML cards** (`theme.cards_html`) instead of `st.metric`,
  which cut long amounts off ("$6,…").
- **Plain words.** "Business income (Line 1)", "Business costs (Line 28)",
  "Profit or loss (Line 31)"; question groups are asked as questions ("Is
  this a business expense?"); the Answer column sits right after who and
  how much, so it's on screen without scrolling sideways. IRS line numbers
  stay, for the preparer.
- **Motion:** only a pulsing dot while reading (off with reduced motion).
  Cards are redrawn on every click, so animating them flickered.

`theme.py` holds the CSS injected once at the top of `app.py`;
`.streamlit/config.toml` carries the same palette and fonts into
Streamlit's own theme engine. Pure presentation: nothing there touches
`core/`.

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
  children — this is what makes the reading panel's dark background
  genuinely wrap its text, progress bar, and Stop button, unlike a raw
  `st.markdown()` `<div>`, which renders as a sibling of the widgets below
  it, not a parent.

- **`data-testid` selectors are undocumented internals** that change
  between Streamlit versions. `requirements.txt` pins the exact version
  used to verify every selector in `theme.py`; bump it deliberately, and
  re-check the selectors against the new version's frontend bundle (or a
  live render) before trusting them again.

## Hand corrections and the source of truth

`core/workpaper.py` keeps, per client, only the source of truth: the
documents the AI transcribed (plus an untouched copy), every hand
correction (`manual_edits`), and every client answer
(`answered_questions`). `rebuild()` derives everything else from those on
every page load: transactions, categories, check payees, reconciliation,
coverage, and the answer log (answers are replayed in order). Because
nothing derived is ever edited directly, a hand correction can't wipe out
a client answer, and changing the tax year updates coverage everywhere.

Each transcribed row gets a stable `row_id` at ingestion, so edits, adds,
and deletes are logged against the row they touched.
`save_corrections()` logs each change (before/after), marks the document
`corrected_by_hand`, and clears any earlier "checked by hand" mark, since
that note no longer describes what's there. `undo_corrections()` restores
the AI's copy. `mark_checked_by_hand()` requires a note and gives the
statement the status "Checked by Hand", which counts as OK in the summary.

The tab itself (`fix_statements_ui.py`) previews the verification on the
unsaved edits and shows, per row, the balance the rows lead to next to the
printed one, so the break is visible on the row where it happens.

## Retired: rule-based readers and learned bank profiles

Until 2026-11-09 PDFs were read by regex parsers tuned per bank, with AI
only as a fallback and learned "bank profiles" committed to GitHub. All of
that was removed: banks change their layouts, files come in many formats,
and every format-specific rule was another way to misread a statement
silently. Its history is in [CHANGELOG.md](CHANGELOG.md).

## Testing

```bash
pytest
```

One test file per `core/` module. The AI is always replaced by a fake in
tests (`call_ai=` in `core/ai_reader.py`); no test makes a network call.
Fixtures are synthetic; real client files must never be committed (see
[README.md](README.md#data-handling)).
