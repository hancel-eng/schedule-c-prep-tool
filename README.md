# Schedule C Prep Tool

Turns messy small-business financial records — bank and credit card statement
PDFs, client spreadsheets, and year-end totals — into a standardized,
audit-ready IRS Schedule C workpaper and a ready-to-send client question
list for a tax preparer.

Built for **Tax Savers CPA**, replacing a manual process of running 12
months of statements per client through a chat assistant by hand.

**Extraction runs entirely offline.** Parsing and categorization are
rule-based (`pdfplumber` + regex + an IRS keyword dictionary). No external
LLM API key is required or called — this was a deliberate choice over the
API-based prototype it replaced: deterministic, auditable, no API cost, no
rate limits, no truncated output on large statements.

**Primary goal:** the tool exists to save a preparer the time of manually
hunting through statements for ambiguous transactions and drafting client
questions by hand. Speed from "upload the files" to "have a sendable
question list" is the metric everything else is weighed against — not
exhaustiveness. Keep this in mind before adding anything to the UI or the
default workflow.

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how the code is organized, the
  pipeline in detail, and technical notes worth knowing before changing
  anything (Streamlit's session-state pattern, the visual theme's
  constraints, design decisions and why).
- **[CHANGELOG.md](CHANGELOG.md)** — dated history of what changed and why,
  including bugs found and fixed against real client data.

---

## Quick start

**Option A — GitHub Codespaces, no local setup.** Open this repo in a
Codespace (`.devcontainer/devcontainer.json` is already configured) — it
installs `requirements.txt` and starts `streamlit run app.py` automatically,
with the app's port forwarded and previewed for you.

**Option B — locally:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

In the browser: enter the client name and tax year in the sidebar, upload
the client's statement PDFs and/or spreadsheets, review the **Client
Inquiry Questions** tab, and download the Excel workpaper. See
[Usage](#usage) below for the full workflow.

To regenerate the synthetic files in `sample_data/`:

```bash
python create_sample_data.py
```

To run the test suite:

```bash
pytest
```

---

## What it does

```
messy client documents
        │  filename deduplication (skip an exact-name duplicate upload)
        ▼
   extraction        PDF (bank / credit card / P&L report) or spreadsheet
        ▼
   normalization     dates, payees, amounts, deposit vs. expense, sign convention
        ▼
   tax categorization   IRS Schedule C line items, or excluded as Non‑P&L
        ▼
   exception review   review queues + an auto-generated, groupable client
        │              question list, with a materiality threshold
        ▼
   [ preparer answers the questions in-app, or via the client ]
        ▼
   recalculation      answers are applied, totals and the workpaper update
        ▼
   Excel workpaper  →  tax preparer
```

## What it produces

Every run outputs a formatted `.xlsx` workbook with seven sheets:

| Sheet | Contents |
|---|---|
| **Schedule C Summary** | Client/year header, gross receipts (Line 1), expense breakdown by IRS line, total expenses (Line 28), net profit/loss (Line 31) — with live formulas |
| **All Transactions** | Full audit trail: date, payee, description, amount, deposit flag, final category, original client category, confidence state, source file |
| **Non-P&L & Transfers** | Everything excluded from the P&L, with the reason |
| **Exceptions Queue** | Potential personal expenses and potential fixed assets, each with an audit note |
| **Client Questions** | Auto-generated inquiry checklist, plus the answer once resolved in-app |
| **Reconciliation QC** | Per-statement reconciliation, 12-month coverage, duplicate files skipped |
| **Applied Client Answers** | Audit trail of every correction the workpaper picked up from an answered question — old category, new category, and why |

A Streamlit dashboard shows the same data interactively before export. Three
tabs are visible by default — **Client Inquiry Questions** (first, editable,
the actual deliverable), **Schedule C Summary**, and **Non-P&L Transfers** —
with line-item search, per-statement reconciliation detail, and
large-transaction review tucked behind a collapsed **"audit detail"**
expander: real, but not needed on every run. See
[ARCHITECTURE.md](ARCHITECTURE.md) for how the dashboard and its progress
panel are built.

## Usage

1. **Sidebar** — enter the client name, tax year, the fixed-asset ("de
   minimis") threshold, and the client-question materiality threshold (a
   vendor group whose total doesn't clear this amount gets no question at
   all — leave at $0 to ask about everything).
2. **Upload** — bank/credit-card statement PDFs and/or client spreadsheets
   for the full tax year. Uploading a P&L summary *alongside* the statements
   it summarizes double-counts the same activity — use one source or the
   other, not both (see [Known limitations](#known-limitations--roadmap)).
3. **Review "Client Inquiry Questions"** — each open question (a possible
   personal expense, a possible fixed asset, an uncategorized transaction, a
   contractor near the 1099 threshold) has an editable **Answer** dropdown
   constrained to a fixed set of choices, never free text. Send the
   questions to the client however you normally do — the tool never sends
   anything itself.
4. **Apply the client's answers** — once you have them, enter them in the
   **Answer** column and click **Apply Client Answers & Recalculate
   Workpaper**. The dashboard, exception queues, and Excel export all update
   immediately; an answered question does not reappear on the next
   recompute.
5. **Download the Excel workpaper.**

Everything above lives only inside the current browser session — "one
client = one session," nothing is written to a database. Uploading a
different set of files starts a new client with a clean slate.

## Key design decisions

- **One client = one session.** All of a client's files are uploaded
  together, processed, and the workpaper is downloaded. No state persists
  between runs or between clients.
- **Deduplication is filename-based, never transaction-based.** Comparing
  individual transactions by date/amount/vendor was tried and rejected: it
  risked silently dropping legitimate transactions that happened to share
  those fields. If the same filename is uploaded twice in a session, the
  second is blocked with a warning. If filenames are distinct, every
  transaction in every file is kept.
- **Rule-based extraction over LLM extraction** (see above).
- **Confidence labels, never a guess.** Every transaction is tagged
  **High Confidence**, **Needs Review**, or **Unresolved**. Anything short
  of High Confidence lands in the exception queue instead of being silently
  assigned a category.

## Project layout

```
app.py                        Streamlit UI and end-to-end orchestration
theme.py                      Visual design tokens + CSS injected into app.py
.streamlit/config.toml        Streamlit's own theme engine (native widgets)

core/
  pdf_parser.py                PDF extraction — 3 strategies (P&L report,
                                credit card, general bank/receipt), routed by filename
  spreadsheet_parser.py        Excel/CSV normalization across varied client formats
  totals_parser.py             Year-end totals → Schedule C lines + special-rule flags
  tax_categorizer.py           IRS keyword dictionary, Non-P&L patterns, custom rules
  exception_analyzer.py        Exception review queues
  vendor_grouping.py           Groups repeated vendor/recipient charges into one question
  question_generator.py        Client question checklist
  transaction_utils.py         Stable transaction identity, used to trace a question
                                back to the exact transaction(s) it concerns
  answer_applier.py            Applies an answered client question to the workpaper
  reconciliation.py            Per-statement reconciliation against declared balances
  excel_exporter.py            Formatted multi-sheet workpaper
  deduplication.py             Filename-based duplicate file detection

tests/                        pytest suite (one file per core/ module, plus
                               tests/test_real_world_regressions.py for bugs
                               found against real client data — synthetic
                               fixtures only, never real client files)
sample_data/                  Synthetic sample documents (no real client data)
create_sample_data.py         Regenerates sample_data/
```

## Custom payee rules

`core/tax_categorizer.py` loads client-specific vendor → category mappings
from `custom_rules.json` in the working directory, if that file exists.
Custom rules take precedence over the built-in keyword dictionary (but not
over Non-P&L detection). There is currently no in-app editor for this file
— add or edit entries directly. It's gitignored: local, per-machine state,
never committed.

## Known limitations & roadmap

- **No OCR.** A scanned-image PDF (e.g. a photo of a check) yields zero
  transactions with no visible error, since `pdfplumber` can't extract text
  from an image-only page.
- **Year-end totals (client-provided summary numbers, no statements)** has a
  complete parsing module (`core/totals_parser.py`) but isn't wired into the
  UI yet — the `.json` upload option is accepted but not routed anywhere.
  This is Tax Savers' "Level 1" client tier; a separate tool may end up
  covering it once real client examples are available.
- **Uploading a P&L alongside the statements it summarizes double-counts**
  the same activity. The workflow currently relies on the preparer not doing
  that; the app doesn't warn.
- **Cross-account transfer matching** (the same transfer appearing as a
  debit in one uploaded account and a credit in another) is only caught when
  the description text itself is informative — there's no cross-file pairing
  by date/amount yet.
- **Bank statement parsing patterns are derived from the specific banks
  tested against** (Regions, Capital One). The extraction architecture is
  general-purpose, but a new bank's statement wording or layout may need new
  patterns the first time it's tried — see
  [ARCHITECTURE.md](ARCHITECTURE.md#extraction-is-pattern-based-not-universal).

See [CHANGELOG.md](CHANGELOG.md) for what's already been fixed and verified
against real client statements.

## Data handling

Do not commit real client financial documents. `.gitignore` excludes loose
`.pdf`/`.xlsx`/`.csv` files and generated workpapers by default; `sample_data/`
is synthetic and safe to commit.
