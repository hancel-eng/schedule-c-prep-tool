# Schedule C Prep Tool

Turns messy small-business financial records — bank and credit card statement PDFs,
client spreadsheets, and year-end totals — into a standardized, audit-ready IRS
Schedule C workpaper for a tax preparer.

Built for **Tax Savers CPA**, replacing a manual process of running 12 months of
statements per client through a chat assistant by hand.

**Extraction runs entirely offline.** Parsing and categorization are rule-based
(pdfplumber + regex + an IRS keyword dictionary). No external LLM API key is
required or called.

---

## Pipeline

```
messy client documents
        ↓  filename deduplication
        ↓  extraction        (PDF / spreadsheet / year-end totals)
        ↓  normalization     (dates, payees, amounts, deposit vs. expense)
        ↓  tax categorization (IRS Schedule C line items + Non-P&L exclusions)
        ↓  exception review  (queues + auto-generated client questions)
   Excel workpaper → tax preparer
```

## What it produces

Every run outputs a formatted `.xlsx` workbook with six sheets:

| Sheet | Contents |
|---|---|
| **Schedule C Summary** | Client/year header, gross receipts (Line 1), expense breakdown by IRS line, total expenses (Line 28), net profit/loss (Line 31) — with live formulas |
| **All Transactions** | Full audit trail: date, payee, description, amount, deposit flag, final category, original client category, confidence state, source file |
| **Non-P&L & Transfers** | Everything excluded from the P&L, with the reason |
| **Exceptions Queue** | Potential personal expenses and potential fixed assets, each with an audit note |
| **Client Questions** | Auto-generated inquiry checklist with a blank response column |
| **Reconciliation QC** | Per-statement reconciliation, 12-month coverage, duplicate files skipped |

A Streamlit dashboard shows the same data interactively before export
(searchable line items, vendor grouping, exception queues, parsing diagnostics).

### Confidence labels

Every transaction carries one of three states, and the categorizer never guesses
silently:

- **High Confidence** — matched a Non-P&L pattern, a custom rule, or a keyword.
- **Needs Review** — no match, but the description names a vendor a preparer can
  recognize and resolve.
- **Unresolved** — the description carries no vendor identity at all
  (`POS DEBIT 4412`), so there is nothing to resolve it from.

Anything that is not High Confidence lands in the exception queue.

### Reconciliation

Each statement is checked twice: that its own declared balances foot
(`beginning + deposits − withdrawals == ending`), and that the transactions
actually extracted from it add up to the totals it declares. The second check is
what catches a parser silently dropping rows.

A document that declares no balances is reported as **Not Reconcilable**, never
as a pass — an unverifiable statement is a finding the preparer needs to see.

### Excluded from income and expense totals

Internal transfers, credit card payments, loan proceeds and repayments, owner
draws and contributions, and refunds/reimbursements are detected, labeled
`Non-P&L: …`, excluded from the P&L, and listed on their own sheet.

## Key design decisions

- **One client = one session.** All of a client's files are uploaded together,
  processed, and the workpaper is downloaded. No state persists between runs.
- **Deduplication is filename-based, never transaction-based.** Comparing
  individual transactions by date/amount/vendor was tried and rejected: it
  risked silently dropping legitimate transactions that happened to share those
  fields. If the same filename is uploaded twice in a session, the second is
  blocked with a warning. If filenames are distinct, every transaction in every
  file is kept.
- **Rule-based extraction over LLM extraction.** Deterministic, auditable,
  no API cost, no rate limits, no truncated responses on large statements.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
streamlit run app.py
```

Then in the browser: enter the client name and tax year in the sidebar, upload
the client's PDFs and spreadsheets, review the dashboard tabs, and download the
Excel workpaper.

To regenerate the synthetic files in `sample_data/`:

```bash
python create_sample_data.py
```

## Tests

```bash
pytest
```

## Project layout

```
app.py                        Streamlit UI and end-to-end orchestration
core/
  pdf_parser.py               PDF extraction — 3 strategies (P&L report,
                              credit card, general bank/receipt), routed by filename
  spreadsheet_parser.py       Excel/CSV normalization across varied client formats
  totals_parser.py            Year-end totals → Schedule C lines + special-rule flags
  tax_categorizer.py          IRS keyword dictionary, Non-P&L patterns, custom rules
  exception_analyzer.py       Exception review queues
  question_generator.py       Client question checklist
  excel_exporter.py           Formatted multi-sheet workpaper
  deduplication.py            Filename-based duplicate file detection
tests/                        pytest suite
sample_data/                  Synthetic sample documents (no real client data)
create_sample_data.py         Regenerates sample_data/
```

## Custom payee rules

The sidebar rule manager saves client-specific vendor → category mappings to
`custom_rules.json` in the working directory. Custom rules take precedence over
the built-in keyword dictionary (but not over Non-P&L detection). This file is
gitignored — it is local, per-machine state.

## Known gaps

See [ROADMAP.md](ROADMAP.md) for the current gap analysis against the full
requirements, including scanned-PDF OCR, cross-account transfer matching, and
statement balance reconciliation.

## Data handling

Do not commit real client financial documents. `.gitignore` excludes loose
`.pdf`/`.xlsx`/`.csv` files and generated workpapers by default; `sample_data/`
is synthetic and safe to commit.
