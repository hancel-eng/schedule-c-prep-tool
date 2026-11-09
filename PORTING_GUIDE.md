# Porting guide: the AI-first rebuild

Everything changed in the rebuild of 2026-11-09 (commits `df0ccf7` to
`2beac3f`), written so it can be copied into another Streamlit + OpenAI
project. Each item says what changed, why, which files hold it, and what
to watch out for when porting. Dated history is in
[CHANGELOG.md](CHANGELOG.md); how it all fits together is in
[ARCHITECTURE.md](ARCHITECTURE.md).

The core idea: **the AI only copies what is printed; plain Python rules do
everything else** (math, categories, checks, questions). Rules are
testable and never change on their own; the AI is good at reading messy
files but not at being consistently right about arithmetic.

---

## 1. Clear error codes for every AI failure

**Why:** AI failures used to be bare exception strings buried in a log, and
a cut-off answer was not detected at all (it was parsed whenever it
happened to be valid JSON).

**What:**
- `core/ai_errors.py` defines `AIExtractionError(code, message, fix, detail)`:
  a stable code, a plain-English message, what to do, and the technical
  detail.
- Every OpenAI SDK exception is mapped to a code: `NO_API_KEY`,
  `BAD_API_KEY`, `NO_PERMISSION`, `OUT_OF_CREDIT`, `TOO_MANY_REQUESTS`,
  `MODEL_NOT_FOUND`, `FILE_REJECTED`, `TIMEOUT`, `NO_CONNECTION`,
  `OPENAI_SERVER_ERROR`, `UNKNOWN_AI_ERROR`.
- The response itself is inspected before use, each case with its own
  code: `ANSWER_CUT_OFF` (`status == "incomplete"`, max tokens),
  `CONTENT_BLOCKED` (incomplete because of the content filter), `AI_FAILED`
  (`status == "failed"`), `AI_REFUSED`, `EMPTY_ANSWER`,
  `UNREADABLE_ANSWER` (invalid JSON). **A partial answer is never used.**
- The UI shows one red box per failed file: message, fix, code, and an
  expandable technical detail.

**Porting notes:** the response-status checks are the important part;
most projects only catch exceptions and silently accept truncated JSON.

## 2. AI as the only file reader

**Why:** banks change layouts and files arrive in many formats; per-bank
regex parsers and "learned bank profiles" kept breaking.

**What:**
- `core/ai_reader.py` reads every file type: PDF (including scans; the
  page images double as OCR), JPG/PNG/WEBP/GIF, XLSX/XLSM/XLS, CSV, DOCX.
- One call per part to the OpenAI Responses API with a **strict JSON schema** (structured outputs).
  The schema asks the AI to transcribe, not compute:
  - each summary-box line as printed, with whether it adds or subtracts
    (instead of one "total withdrawals" the AI had to add up);
  - each row's section heading and check number;
  - `what_it_is` (what the document is) separate from `could_not_read`
    (only things it truly couldn't read);
  - summary totals only from the account summary box; `$0` lines ignored.
- Up to 4 calls run in parallel across files.
- Parts are stitched back into whole documents
  (`continues_from_previous_part`).
- Unreadable inputs get clear codes too: empty, damaged, password-locked,
  unsupported type.
- `core/document_rules.py` holds every rule after extraction: signs, card
  payment vs. refund, check-row labels for check matching, categorization,
  reconciliation, plain-English explanations naming the row that breaks,
  invoice/receipt arithmetic, and month coverage for the tax year.
- Reconciliation has three checks, and all must pass:
  - balance equation (start + money in − money out = end);
  - summary totals match the rows;
  - running balance row by row.
  A "Total" line equal to its parts is not counted twice.
- A file with any failed part contributes nothing and shows its error.
- Removed: the regex parsers, bank profiles, the GitHub profile sync, the
  AI on/off toggle, and `GITHUB_TOKEN`.

**Porting notes:** keep the AI's job narrow (transcribe) and its output
schema strict; put all judgment in testable Python. The first real run
caught two prompt problems that only a real file shows (a "$0.00" fee line
copied as a total, general info dumped in the notes field), so always do
one real run before trusting a new prompt.

## 3. Nothing gets truncated

**Why:** long statements were being cut off silently.

**What:**
- Every file is split into small parts before reading:
  - PDFs: 4 pages per part, `detail: "high"`;
  - Excel/CSV/Word: 120 lines per part, with the headings repeated so
    each part has context;
  - images: whole.
- If an answer is cut off, the part is split in half and retried
  automatically (recursively, down to a single page or line). If even that is cut off, the part fails with `ANSWER_CUT_OFF`.
- An audit removed every data slice outside the AI. The only slices left
  are display limits that say so ("largest 15 of N").
- Found while testing: CSV sniffing guessed the letter "i" as a separator.
  CSV is now read with the standard `csv` module, separators are limited to
  `, ; tab |`, and Windows encoding is supported.

## 4. Fix numbers by hand inside the app

**Why:** when a statement doesn't add up, the preparer must be able to fix
it in the app without uploading another file.

**What:**
- `core/workpaper.py`: the client's source of truth is just three things:
  - the AI's documents, plus an untouched copy;
  - hand corrections;
  - client answers.

  Everything else is rebuilt by rules on every page load, so a correction
  and an answer can never overwrite each other.
- `fix_statements_ui.py` (the "Check numbers" step):
  - edit the printed starting and ending balances and the summary totals;
  - edit, add and delete rows in a grid;
  - see a "should be" balance next to each printed running balance, which
    marks the row where the math breaks;
  - a live "if you save" check before saving;
  - Save, Undo back to the AI's version, and "checked by hand", which
    requires a note.
- Every hand fix is logged with its before and after values, shown in the
  app, and exported as an Excel sheet named "Manual Corrections".
- Found while testing: Streamlit markdown reads `$...$` as math, so dollar
  amounts in alerts lost their signs. Dollar signs are now escaped (`\$`).

**Porting notes:** the "source of truth + rebuild everything" pattern is
what makes edits safe; storing derived results and editing those leads to
answers and fixes overwriting each other.

## 5. Saved work, reading in steps, and a Stop button

**Why:** progress must never be lost, long jobs must be split into steps,
and the user must be able to stop.

**What:**
- `core/storage.py`:
  - `MemoryStore` (no database: the app still works and says work isn't
    being saved) and `PostgresStore` (Supabase/Postgres).
  - Tables live in their own schema (`schedule_c`), with row level
    security on and no policies, so only the server connection can read
    them:
    - `clients`: id, name, tax year, state as jsonb, timestamps;
    - `pending_files`: the uploaded bytes until the file is read, its part
      count, and a failed flag;
    - `part_results`: one AI answer per part, cascading from
      `pending_files`.
  - Everything saved goes through `to_json_safe`: numpy/pandas values
    become Python types, NaN becomes null, dates become ISO strings, and
    `allow_nan=False`.
  - Connection errors become `DATABASE_UNREACHABLE`, with the URL and
    password scrubbed from the detail.
- `core/processing.py`:
  - `queue_files` saves uploads and skips a filename already added (also
    against earlier uploads);
  - `read_next_batch` reads up to 4 parts at once, and each worker thread
    saves its own result the moment it arrives;
  - `finish_ready_files` merges a fully read file into the client, then
    deletes its bytes;
  - `retry_file` re-reads only the failed parts; `remove_file` and
    `stop_waiting_files` drop files.
- Stop and Continue:
  - The main thread waits with `wait(timeout=1)` and calls an `on_tick`
    callback. That is where Streamlit can interrupt the run when **Stop**
    is clicked, so it pauses within about a second.
  - The thread pool is shut down with
    `shutdown(wait=False, cancel_futures=True)`; parts already in flight
    still save.
  - A module-level `_in_flight` set stops a quick Continue from re-sending
    parts that are still being read.
  - **Continue** reads only the missing parts, even after closing the page.
- UI additions: a sidebar client list from the database, "+ New client",
  "Delete this client" (asks first), Try again and Remove for failed files,
  a files list, and a status line saying whether work is being saved.

**Porting notes (Supabase):**
- Use the **session pooler** URL, and connect with psycopg 3 plus
  psycopg-pool: `ConnectionPool` with `autocommit=True`,
  `prepare_threshold=None` (needed behind the pooler),
  `connect_timeout=15`, and `check=ConnectionPool.check_connection`.
- Use a password with **letters and numbers only**: special characters
  broke URL parsing, and an error message once printed part of the
  password.
- `DATABASE_URL` lives only in `.streamlit/secrets.toml` (gitignored) and
  in Streamlit Cloud Secrets.
- Test against a throwaway schema (`PostgresStore(url, schema="..._test")`)
  and drop it afterwards; never the real one.
- Requirements: `psycopg[binary]>=3.2` and `psycopg-pool>=3.2`.

## 6. Plain-language visual redesign

**Why:** it must be usable by someone who doesn't want to learn software
("as if for a child or an elderly person").

**What (app.py, theme.py, .streamlit/config.toml):**
- **One step on screen at a time**, picked with
  `st.segmented_control(required=True)`. Each label shows its status:
  - "1 · Add files ✓"
  - "2 · Check numbers · 2 to fix"
  - "3 · Questions · 3 open"
  - "4 · Results & download"
- **"What to do next" card** at the top. It always names the single most
  important thing to do, and its color shows the state: green = done,
  amber = needs you, red = problem. It checks, in order:
  1. no files;
  2. reading paused;
  3. failed files;
  4. numbers that don't add up;
  5. open questions;
  6. done.

  Missing months are added as a note. If the selected step isn't the
  suggested one, a "Go to: …" button appears.
- **Plain words:** "Business income (Line 1)", "Is this a business
  expense?", "Save answers", "Stop for now". The IRS line numbers stay,
  because preparers use them.
- **Number cards in HTML** (`theme.cards_html`) instead of `st.metric`,
  which cut long amounts off ("$6,…").
- **Reading panel:** while files are read, only a dark panel shows, with a
  pulsing dot, a progress bar and Stop. This is the only animation, and it
  respects reduced motion. Streamlit's greyed-out leftovers are hidden with
  `[data-stale="true"]` CSS so Stop never looks disabled. The counter
  totals the whole session instead of restarting per file.
- **Questions grid:** `column_order` puts the Answer column right after the
  payee and amount, so it's never off screen. There is also "Same answer
  for all checked rows".
- **Results:** cards, "How these numbers were worked out", a by-line table
  that lets you inspect the transactions in each line, "Not counted", and
  a "Details for experts" box holding the technical tabs.
- **Type and color:**
  - Atkinson Hyperlegible Next (made for low-vision readers), loaded
    through `[[theme.fontFaces]]` in config.toml.
  - Pine palette: sidebar `#12352E`, primary `#1D6F52`, ground `#F3F6F3`;
    color is used only for states.
- **Size (updated by `2beac3f`):** 15px text, 26/21/18px headings, 38px
  buttons, compact cards and padding. The first version used 18px and big
  buttons, which made screens look crowded.
- **Phone:** `initial_sidebar_state="auto"`, so the sidebar starts closed
  on small screens.
- **Streamlit patterns worth copying:**
  - `st.container(key="x")` creates a `.st-key-x` CSS class, so one block
    can be styled.
  - A "flash" list in session state shows messages as toasts after
    `st.rerun()`.
  - Per-client widget keys (`field__<client_id>`) with `on_change`
    callbacks that save.
  - `format_func` on the client selectbox ("Name · year").
- **CSS gotchas:**
  - Streamlit sets heading sizes with stronger selectors, so use
    `.stApp h1 { font-size: … !important }`; a plain `h1 { … }` is
    ignored.
  - Keep `.block-container` padding-top around 56px, or the title slides
    under Streamlit's top bar.

## Smaller items

- **Password eye tooltip** (`58a1ac1`): CSS-only "Show/Hide password"
  hover text, keyed off the eye button's `aria-label`.
- **Version label:** `APP_VERSION` in app.py, shown at the bottom of the
  sidebar, goes up on every meaningful change (currently `v19`). It is
  the quickest way to tell whether Streamlit Cloud has deployed the new
  code.
- **Missing months:** they are listed by name; listing them as numbers
  crashed the app.

## Order to port them in

Easiest first, which is also the order that keeps the app working at each
step:
1. error codes;
2. AI-only reading and rules;
3. no truncation;
4. hand fixes;
5. database, stepwise reading and Stop;
6. redesign.

Steps 4 and 5 depend on the "source of truth + rebuild" model from step 4;
step 6 is presentation only and can be ported on its own.
