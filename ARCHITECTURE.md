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
- [Retired: rule-based readers and learned bank profiles](#retired-rule-based-readers-and-learned-bank-profiles)
- [Testing](#testing)

## Pipeline, module by module

`app.py` is orchestration only. Business logic lives in `core/` with no
Streamlit dependency, so every module is unit-testable on its own.

Since 2026-11-09 the split is strict: **the AI reads, rules decide.** The
AI only transcribes what is printed. Everything after that (signs, card
payments vs. refunds, categories, reconciliation, questions) is plain
Python.

1. `core/deduplication.py` skips a file whose name was already uploaded.
   Filename only, never transaction-level (that silently dropped real
   transactions when it was tried).
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
