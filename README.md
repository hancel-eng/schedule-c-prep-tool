# Schedule C Prep Tool

Turns messy small-business financial records — bank and credit card
statement PDFs, client spreadsheets, and year-end totals — into a
standardized, audit-ready IRS Schedule C workpaper, plus a ready-to-send
list of questions for the client. Built for tax preparers.

## Try it now

**Live app: [schedule-c-prep-tool.streamlit.app](https://schedule-c-prep-tool.streamlit.app/)**

No installation needed — open the link, upload a client's statements, and
try the workflow yourself. (The sample files in `sample_data/` are safe to
upload if you don't have real client files handy.)

---

## Who this is for

Built for **Tax Savers CPA**. Their preparers currently process a client's
Schedule C by manually reading 12 months of bank/credit-card statements
(or running them through a general chat assistant) and typing up the
result by hand — slow, and easy to miscount or miss something. This tool
automates that first pass.

Tax Savers' clients arrive in two tiers:

- **Level 2** — the client sends full bank/credit-card statements. **This
  is what this tool handles today.**
- **Level 1** — the client sends only their own messy, inconsistently
  formatted expense totals, no statements. Not built yet — a separate tool
  may be needed once real examples of this kind of client data are
  available.

## What it does, in plain terms

1. You upload a client's bank/credit-card statement PDFs (and/or
   spreadsheets) for a tax year.
2. The tool reads every transaction, figures out whether it's income or an
   expense, and sorts it into the right line of IRS Schedule C.
3. It automatically excludes things that aren't real business income or
   expense — transfers between the client's own accounts, credit card
   payments, loan money coming in or going out, the owner taking money out
   of the business.
4. Anything it can't confidently sort — a payment that might be personal, a
   purchase that might be a fixed asset, a vendor it doesn't recognize —
   becomes a short, plain-English question for the client, automatically
   grouped so you're not asking the same question 50 times about the same
   vendor.
5. You send those questions to the client however you normally would (the
   tool never sends anything itself). When you have the answers, you type
   them in and the whole workpaper — totals included — updates instantly.
6. You download a formatted Excel workbook, ready for the preparer.

**The tool's main job is speed: getting from "here are the client's
statements" to "here's the list of things I need to ask the client" as
fast as possible** — not perfection on the first pass. Getting the numbers
right matters, but the time saved on the question list is the actual point.

## Is it ready to use?

**Yes, for Level 2 clients (full bank/credit-card statements).** It has
been run against a real 34-statement, 12-month client engagement, with
every bug found along the way fixed and the final numbers checked by hand.
See [CHANGELOG.md](CHANGELOG.md) for the full history of what was found and
fixed.

**Two things worth knowing before relying on it for a new client:**

- It can't read a *scanned image* of a statement (a photo of a check, for
  example) — only PDFs with real, selectable text. There's no OCR yet.
- Its extraction rules were built and tested against two specific banks
  (Regions and Capital One). A statement from a different bank will still
  process, but may need a small rule adjustment the first time — check the
  **Reconciliation QC** section of the results before trusting a brand-new
  bank's numbers.

See [Known limitations & roadmap](#known-limitations--roadmap) below for
the complete list.

---

## Quick start (running it yourself, not the live link above)

**Option A — GitHub Codespaces, no local setup.** Open this repo in a
Codespace (it's already configured via `.devcontainer/`) — it installs
everything and starts the app automatically, with a live preview.

**Option B — on your own computer:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## How to use it

1. **Sidebar** — enter the client's name, the tax year, the fixed-asset
   ("de minimis") dollar threshold, and the client-question materiality
   threshold (a vendor whose total spend doesn't clear this amount gets no
   question at all — leave it at $0 to ask about everything).
2. **Upload** — the client's bank/credit-card statement PDFs and/or
   spreadsheets for the full tax year. Don't upload a P&L summary
   *alongside* the statements it summarizes — that counts the same money
   twice. Use one or the other.
3. **Review "Client Inquiry Questions"** (the first tab) — each open
   question has an **Answer** dropdown with a fixed set of choices, never a
   free-text box, so the tool is never guessing what you meant.
4. **Send the questions to the client**, however you normally do that.
5. **Enter the client's answers** in the same Answer column and click
   **Apply Client Answers & Recalculate Workpaper** — everything updates
   immediately, and an answered question won't come back.
6. **Download the Excel workpaper.**

Nothing is saved between sessions — uploading a new client's files starts
completely fresh. Nothing is written to a database; everything lives only
in your browser tab while you're working.

---

## What the Excel workpaper contains

| Sheet | What's on it |
|---|---|
| **Schedule C Summary** | Gross receipts, expenses by IRS line, total expenses, net profit/loss |
| **All Transactions** | Every transaction, with its final category and where it came from — the full audit trail |
| **Non-P&L & Transfers** | Everything excluded from the totals, and why |
| **Exceptions Queue** | Possible personal expenses and possible fixed assets, flagged for review |
| **Client Questions** | The question list, plus the client's answer once it's entered |
| **Reconciliation QC** | Whether each statement's numbers were captured completely and correctly |
| **Applied Client Answers** | A log of every change the workpaper made once a question was answered |

## A few design choices worth knowing

- **The tool never guesses.** Every transaction is labeled High Confidence,
  Needs Review, or Unresolved. Anything short of High Confidence goes to
  the exception/question list instead of being silently assigned a
  category.
- **No AI/LLM is used to read the statements.** Extraction is done with
  deterministic rules (pattern matching against real statement text), not
  a language model — cheaper, faster on large files, and every result can
  be traced back to exactly why the tool made that call.
- **One client, one session.** Nothing carries over between clients, and
  nothing is stored outside your browser tab while you're working.
- **Duplicate files are caught by filename, not by comparing individual
  transactions** — comparing transactions risked accidentally throwing away
  a real transaction that happened to look like a duplicate.

## Project layout

```
app.py                        The app itself (Streamlit)
theme.py                      Visual design (colors, fonts, layout)
.streamlit/config.toml        Streamlit's own theme settings

core/                         All the actual business logic, independent
                               of the app — see ARCHITECTURE.md for detail
  pdf_parser.py                 Reads bank/credit-card statement PDFs
  spreadsheet_parser.py         Reads client Excel/CSV files
  totals_parser.py              Reads client-provided year-end totals
  tax_categorizer.py            Decides which Schedule C line a transaction belongs on
  exception_analyzer.py         Flags what needs a second look
  vendor_grouping.py            Groups repeated charges into one question
  question_generator.py         Writes the client question list
  transaction_utils.py          Internal bookkeeping (traces a question back to its transaction)
  answer_applier.py             Applies a client's answer to the workpaper
  reconciliation.py             Checks extraction against each statement's own totals
  excel_exporter.py             Builds the final Excel file
  deduplication.py              Catches duplicate file uploads

tests/                        Automated tests (one file per module above)
sample_data/                  Made-up sample files, safe to upload/share
create_sample_data.py         Regenerates the sample files
```

For how the pieces above actually work together, see
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Known limitations & roadmap

- **No OCR.** A scanned image (not real text) yields zero transactions with
  no error message — it just looks empty.
- **The "Level 1" client tier (totals only, no statements) isn't built.**
  Waiting on real example files from Tax Savers before starting this.
- **Uploading a P&L summary alongside the statements it summarizes
  double-counts** the same income and expenses. The tool doesn't warn about
  this yet — just avoid doing it.
- **Transfers between a client's own accounts, across two different
  uploaded files,** are only caught when the transaction's own description
  makes it obvious. There's no cross-file matching by date and amount yet.
- **Extraction rules are proven against specific banks (Regions, Capital
  One), not universal** — a new bank may need a small adjustment the first
  time. See
  [ARCHITECTURE.md](ARCHITECTURE.md#extraction-is-pattern-based-not-universal).

See **[CHANGELOG.md](CHANGELOG.md)** for everything that's already been
found and fixed.

## Keeping client data safe

**Never commit real client financial documents to this repository.**
`.gitignore` already blocks loose `.pdf`/`.xlsx`/`.csv` files and generated
workpapers by default. `sample_data/` is made-up data and is safe to share.

## Questions about this project

Built and maintained by Hans (`hancel@gospectr.com`). The GitHub repo is
[hancel-eng/schedule-c-prep-tool](https://github.com/hancel-eng/schedule-c-prep-tool).
