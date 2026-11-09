# Schedule C Prep Tool

Turns messy small-business financial records — bank and credit card
statement PDFs, client spreadsheets, and year-end totals — into a
standardized, audit-ready IRS Schedule C workpaper, plus a ready-to-send
list of questions for the client. Built for tax preparers.

## Try it now

**Live app: [schedule-c-prep-tool.streamlit.app](https://schedule-c-prep-tool.streamlit.app/)**

The app is password-protected — ask Hans for the access password. Once
you're in, no installation is needed: upload a client's statements and try
the workflow yourself. (The sample files in `sample_data/` are safe to
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

**Since 2026-11-09 the AI reads every file** (see
[How files are read](#how-files-are-read)). This was verified on one real
statement so far; the full 12-month set has not been re-run yet.

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

**Set an access password.** The app is gated behind a single shared
password so a leaked link can't be used by a stranger. It's never stored
in code — copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml` (already gitignored) and set your own password
there:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# then edit .streamlit/secrets.toml and set app_password = "..."
```

For the **live Streamlit Cloud app**, don't upload that file — open the
app's dashboard, go to **Settings → Secrets**, and paste the same
`app_password = "..."` line there instead. If no password is set, the app
shows a warning on the login screen and nobody can get in.

## How to use it

1. **Sidebar → Clients** — pick which client you're working on, or click
   **+ New Client**. Every client's work is saved automatically — see
   [Saved work](#saved-work) below.
2. **Fill in that client's metadata** — name, the tax year, the fixed-asset
   ("de minimis") dollar threshold, and the client-question materiality
   threshold (a vendor whose total spend doesn't clear this amount gets no
   question at all — leave it at $0 to ask about everything).
3. **Upload** — the client's statements and other documents for the tax
   year, then click **Read**. You can add more files later; a file with
   the same name as one already uploaded is skipped. Don't upload a P&L
   summary *alongside* the statements it summarizes — that counts the same
   money twice. Use one or the other.
4. **Wait, or press Stop.** Files are read a few parts at a time and every
   part is saved as soon as it's read. **Stop** pauses; **Continue
   reading** picks up where it left off, even after closing the page.
5. **Fix Statements** (the first tab) — fix anything that doesn't add up.
6. **Review "Client Inquiry Questions"** — each open question has an
   **Answer** dropdown with a fixed set of choices, never a free-text box,
   so the tool is never guessing what you meant.
7. **Send the questions to the client**, however you normally do that.
8. **Enter the client's answers** in the same Answer column and click
   **Apply Client Answers & Recalculate Workpaper** — everything updates
   immediately, and an answered question won't come back. If several rows
   share the same answer (e.g. "these are all personal transfers"), check
   their boxes and use the bulk-answer control under the table instead of
   picking the same answer row by row.
9. **Download the Excel workpaper.**

## Saved work

Every client's work is saved in a private database (a Supabase Postgres
project) the moment it changes: the client's name and settings, what the AI
read, every hand fix, and every client answer. Close the tab, come back
tomorrow, pick the client in the sidebar, and everything is there — no
uploading again.

Uploaded files are saved only until they're read. Once a file is read
successfully its bytes are deleted; only what the AI read from it is kept.
A file the AI couldn't read keeps its bytes so **Try again** can re-read
just the parts that failed.

**Delete this client** (sidebar) permanently removes a client and all of
its saved work. It asks first and can't be undone.

Set it up once with `DATABASE_URL` in the app's secrets (see
`.streamlit/secrets.toml.example`). The app creates its own tables, in a
schema of their own (`schedule_c`) that Supabase doesn't expose through its
public web API, with row level security on. Without `DATABASE_URL` the app
still works but keeps work only until the tab closes, and the sidebar
warns about it.

Two limits worth knowing: reading runs only while the client's page is open
(switch to another client and that client's reading pauses until you come
back and press **Continue reading**), and two people editing the same client
at the same moment would overwrite each other's last change.

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
- **The AI reads; plain rules decide.** The AI (OpenAI) only transcribes
  what is printed. Every total, category, check, and question comes from
  rules you can trace. See [How files are read](#how-files-are-read).
- **Work is saved as it goes.** Every change is saved to a private
  database, and reading is saved part by part, so a stop, a closed tab, or a
  crash loses nothing. See [Saved work](#saved-work).
- **Duplicate files are caught by filename, not by comparing individual
  transactions** — comparing transactions risked accidentally throwing away
  a real transaction that happened to look like a duplicate.

## Project layout

```
app.py                        The app itself (Streamlit)
fix_statements_ui.py          The "Fix Statements" tab (hand corrections)
theme.py                      Visual design (colors, fonts, layout)
.streamlit/config.toml        Streamlit's own theme settings

core/                         All the actual business logic, independent
                               of the app — see ARCHITECTURE.md for detail
  ai_reader.py                  The AI reader: splits files, reads them, stitches them back
  ai_errors.py                  Turns every AI or file failure into a clear error code
  document_rules.py             Rules after reading: signs, checks, reconciliation, coverage
  workpaper.py                  Rebuilds a client's workpaper from AI reads + hand fixes + answers
  processing.py                 Reads files in small saved steps (Stop / Continue / Try again)
  storage.py                    Saves every client's work (Supabase/Postgres, or memory)
  check_matcher.py              Matches a check copy's payee to its statement line item
  totals_parser.py              Reads client-provided year-end totals
  tax_categorizer.py            Decides which Schedule C line a transaction belongs on
  exception_analyzer.py         Flags what needs a second look
  vendor_grouping.py            Groups repeated charges into one question
  question_generator.py         Writes the client question list
  transaction_utils.py          Internal bookkeeping (traces a question back to its transaction)
  answer_applier.py             Applies a client's answer to the workpaper
  excel_exporter.py             Builds the final Excel file

tests/                        Automated tests (one file per module above)
sample_data/                  Made-up sample files, safe to upload/share
create_sample_data.py         Regenerates the sample files
```

For how the pieces above actually work together, see
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

## How files are read

Every uploaded file is read by AI (OpenAI `gpt-6-astra`): PDFs (including
scanned ones), photos (JPG, PNG, WEBP, GIF), Excel and CSV files, and Word
(.docx) files. The AI only copies what is printed: each row, its amount,
whether it adds or subtracts, and the balances and totals in the summary
box. It never decides a category or adds anything up.

Long files are split into small parts (4 PDF pages, or 120 spreadsheet
lines, at a time) so no AI answer gets cut off. If one still is, that part
is split again and retried automatically; a cut-off answer is never used.

After reading, plain rules check each statement against its own printed
numbers (starting balance, ending balance, summary totals, and the balance
on each row) and explain in plain words anything that doesn't add up.

If a statement doesn't add up, open the **Fix Statements** tab: change
the printed balances or totals, edit, add, or delete rows, and see right
away whether it adds up before saving. Every fix is logged (also in the
Excel sheet *Manual Corrections*) and can be undone back to what the AI
read. A statement with nothing to check against can be marked "checked by
hand" with a required note.

If the AI can't read a file, the app shows a red box with what went wrong,
what to do, and an error code (for example `OUT_OF_CREDIT`,
`BAD_API_KEY`, `FILE_LOCKED`), and nothing from that file is counted.

The AI key is required, and the database is recommended:

```toml
# .streamlit/secrets.toml (locally) or the app's Settings -> Secrets (on
# Streamlit Community Cloud)
OPENAI_API_KEY = "sk-..."
DATABASE_URL = "postgresql://..."   # see Saved work above
```

Measured once, on a real 6-page bank statement: about 20,000 tokens in and
6,000 out, roughly $0.50 at the model's list price. Not yet measured on a
full year.

## Check copies

A bank statement's own cleared-checks listing only ever has a check number
and an amount — never a payee, because the statement itself doesn't print
one. Raised directly by a client: *"las copias de cheques son necesarias,
porque cuando el cliente paga con cheque hay que saber quién fue el
proveedor"* (check copies are needed to know who the vendor was).

Upload the check copy/report alongside the statements for the same client.
If the AI finds a check in it, its
payee is matched to the matching "Check #..." line item automatically:
check number first, amount only as a fallback and only when it uniquely
points to one still-unlabeled check — two checks for the same amount is
common enough (rent, a recurring vendor) that guessing would be worse than
leaving both unmatched. Anything that couldn't be matched with confidence
shows up in the **Supporting Documents** tab, flagged for manual review —
never silently attached to the wrong transaction.

## Known limitations & roadmap

- **The "Level 1" client tier (totals only, no statements) isn't built.**
  Waiting on real example files from Tax Savers before starting this.
- **Uploading a P&L summary alongside the statements it summarizes
  double-counts** the same income and expenses. The tool doesn't warn about
  this yet — just avoid doing it.
- **Transfers between a client's own accounts, across two different
  uploaded files,** are only caught when the transaction's own description
  makes it obvious. There's no cross-file matching by date and amount yet.
- **Not yet built:** the plain-language redesign, the last step of the
  AI-first rebuild.

See **[CHANGELOG.md](CHANGELOG.md)** for everything that's already been
found and fixed.

## Keeping client data safe

**Never commit real client financial documents to this repository.**
`.gitignore` already blocks loose `.pdf`/`.xlsx`/`.csv` files and generated
workpapers by default. `sample_data/` is made-up data and is safe to share.

## Questions about this project

Built and maintained by Hans (`hancel@gospectr.com`). The GitHub repo is
[hancel-eng/schedule-c-prep-tool](https://github.com/hancel-eng/schedule-c-prep-tool).
