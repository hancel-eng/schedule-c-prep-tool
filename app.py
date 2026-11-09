"""Schedule C Helper -- the Streamlit app.

Laid out as four plain steps, one on screen at a time: add files, check the
numbers, client questions, results & download. A "What to do next" card at
the top always says what needs attention. All business logic lives in
core/; this file only draws it and saves changes.
"""
import os
import subprocess
import time
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from core import processing, workpaper
from core.ai_errors import missing_api_key
from core.ai_reader import SUPPORTED_EXTENSIONS
from core.answer_applier import ANSWER_OPTIONS, apply_client_answers, uncategorized_expense_answer_options
from core.excel_exporter import ExcelWorkpaperExporter
from core.exception_analyzer import ExceptionAnalyzer
from core.question_generator import ClientQuestionGenerator
from core.storage import MemoryStore, PostgresStore, StorageError, new_client_state
from core.tax_categorizer import TaxCategorizer
from theme import CSS, cards_html, next_step_html, reading_html
import fix_statements_ui

# Bumped on every meaningful change to this file, so whoever is looking at the
# app can tell which version is running just by glancing at the sidebar.
APP_VERSION = "v18"


def _running_commit() -> str:
    """Short git commit hash of the running code ("unknown" without git), so
    a stale Streamlit Cloud deploy is visible in the sidebar."""
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=os.path.dirname(os.path.abspath(__file__)),
                                capture_output=True, text=True, timeout=5)
        commit = result.stdout.strip()
        return commit if result.returncode == 0 and commit else "unknown"
    except Exception:
        return "unknown"


st.set_page_config(page_title="Schedule C Helper", page_icon="📒", layout="wide",
                   initial_sidebar_state="auto")
st.markdown(CSS, unsafe_allow_html=True)


def _secret(name: str) -> str:
    """st.secrets raises (not KeyError) when secrets.toml doesn't exist at
    all; treat that the same as "not set"."""
    try:
        return st.secrets.get(name, "") or os.environ.get(name, "")
    except FileNotFoundError:
        return os.environ.get(name, "")


_openai_key = _secret("OPENAI_API_KEY")
if _openai_key:
    os.environ["OPENAI_API_KEY"] = _openai_key  # core/ai_reader.py reads it from the environment


def check_password() -> bool:
    """One shared password (app_password in Streamlit secrets) so a leaked
    link can't be used by a stranger."""
    if st.session_state.get("password_correct"):
        return True
    st.title("Schedule C Helper")
    st.caption("This tool is private. Type the password to come in.")

    def _submit():
        expected = _secret("app_password")
        st.session_state.password_correct = bool(expected) and st.session_state.get("password_input", "") == expected
        del st.session_state["password_input"]

    st.text_input("Password", type="password", key="password_input", on_change=_submit)
    if st.session_state.get("password_correct") is False:
        st.error("That password isn't right. Try again.")
    if not _secret("app_password"):
        st.warning("No app_password is set in the app's secrets, so no password will work. "
                   "See .streamlit/secrets.toml.example.")
    return False


if not check_password():
    st.stop()

st.title("Schedule C Helper")
st.caption("Turn a client's statements into a Schedule C workpaper and a short list of questions for the client.")


# Short messages shown once on the next page draw. A toast shown right
# before st.rerun() would vanish with the rerun.
def _flash(message: str) -> None:
    st.session_state.setdefault("flash", []).append(message)


for _message in st.session_state.pop("flash", []):
    st.toast(_message)

# ----------------------------------------------------------------------------
# SAVED WORK (core/storage.py): every change is saved the moment it happens.
# Without DATABASE_URL the app still works for this browser session only,
# and the sidebar says so.
# ----------------------------------------------------------------------------
DEFAULT_TAX_YEAR = 2026


@st.cache_resource(show_spinner="Opening your saved work…")
def _database_store(url: str) -> PostgresStore:
    return PostgresStore(url)


def _get_store():
    url = _secret("DATABASE_URL")
    if url:
        return _database_store(url)
    if "memory_store" not in st.session_state:
        st.session_state.memory_store = MemoryStore()
    return st.session_state.memory_store


def _show_storage_error(err: StorageError) -> None:
    st.error(f"**{err.message}**  \n**What to do:** {err.fix}  \n`Error code: {err.code}`")
    if err.detail:
        with st.expander("Technical detail"):
            st.code(err.detail, language=None)


try:
    store = _get_store()
    clients = store.list_clients()
    if not clients:
        store.create_client("", DEFAULT_TAX_YEAR)
        clients = store.list_clients()
except StorageError as err:
    _show_storage_error(err)
    st.stop()

if "client_cache" not in st.session_state:
    st.session_state.client_cache = {}   # client_id -> loaded client state
    st.session_state.reading = {}        # client_id -> True while reading runs

client_ids = [c["client_id"] for c in clients]
client_labels = {c["client_id"]: f"{(c['client_name'] or '').strip() or 'Untitled client'} · {c['tax_year']}"
                 for c in clients}
if st.session_state.get("active_client_id") not in client_ids:
    st.session_state.active_client_id = client_ids[0]


def _create_client() -> None:
    client_id = store.create_client(st.session_state.new_client_name.strip(), int(st.session_state.new_client_year))
    st.session_state.active_client_id = client_id
    st.session_state.new_client_name = ""


def _delete_client(client_id: str) -> None:
    store.delete_client(client_id)
    st.session_state.client_cache.pop(client_id, None)
    st.session_state.reading.pop(client_id, None)
    st.session_state.pop("active_client_id", None)


st.sidebar.header("Clients")
st.sidebar.selectbox("Which client are you working on?", options=client_ids,
                     format_func=lambda cid: client_labels.get(cid, cid), key="active_client_id",
                     help="Switch any time. Every client's work is kept.")
with st.sidebar.popover("+ New client", width="stretch"):
    st.text_input("Client or business name", key="new_client_name", placeholder="e.g. Acme Consulting LLC")
    st.number_input("Tax year", value=DEFAULT_TAX_YEAR, step=1, key="new_client_year")
    st.button("Create client", type="primary", on_click=_create_client,
              disabled=not st.session_state.get("new_client_name", "").strip())

active_client_id = st.session_state.active_client_id
_cache = st.session_state.client_cache
if active_client_id not in _cache:
    try:
        _cache[active_client_id] = store.load_client(active_client_id) or new_client_state("", DEFAULT_TAX_YEAR)
    except StorageError as err:
        _show_storage_error(err)
        st.stop()
client_state = _cache[active_client_id]


def save_client() -> None:
    """Writes this client's work to the database. Called after every change."""
    store.save_client(active_client_id, client_state)


if store.is_saved:
    st.sidebar.caption("✓ Your work is saved automatically.")
else:
    st.sidebar.warning("Your work is **not** being saved: closing this tab loses it. "
                       "To save it, add DATABASE_URL to the app's secrets (see README).")

# Each client's own fields have their own widget keys, filled from the saved
# client the first time they're shown and saved back the moment they change.
_META_FIELDS = ("client_name", "tax_year", "de_minimis_threshold", "materiality_threshold")


def _meta_key(field: str) -> str:
    return f"{field}__{active_client_id}"


for _field in _META_FIELDS:
    if _meta_key(_field) not in st.session_state:
        st.session_state[_meta_key(_field)] = client_state[_field]


def _on_meta_change(client_id: str, field: str) -> None:
    state = st.session_state.client_cache[client_id]
    state[field] = st.session_state[f"{field}__{client_id}"]
    store.save_client(client_id, state)


st.sidebar.markdown("---")
st.sidebar.header("About this client")
client_name = st.sidebar.text_input("Name", key=_meta_key("client_name"), placeholder="e.g. Acme Consulting LLC",
                                    on_change=_on_meta_change, args=(active_client_id, "client_name"))
tax_year = st.sidebar.number_input("Tax year", step=1, key=_meta_key("tax_year"),
                                   on_change=_on_meta_change, args=(active_client_id, "tax_year"))
with st.sidebar.expander("More settings"):
    de_minimis_threshold = st.number_input(
        "Ask \"Is this equipment?\" for a single purchase over ($)", step=100.0,
        key=_meta_key("de_minimis_threshold"), on_change=_on_meta_change,
        args=(active_client_id, "de_minimis_threshold"),
        help="The de minimis safe-harbor amount, usually $2,500. A purchase above it may need to be "
             "depreciated instead of deducted, so the client is asked about it.")
    materiality_threshold = st.number_input(
        "Don't ask about a vendor whose yearly total is at most ($)", step=1.0,
        key=_meta_key("materiality_threshold"), on_change=_on_meta_change,
        args=(active_client_id, "materiality_threshold"),
        help="Leave at 0 to ask about everything. Example: 25 skips a vendor that only adds up to $25 for the year.")

st.sidebar.markdown("---")
if _openai_key:
    st.sidebar.caption("✓ The AI reader is ready. It only copies what's printed; plain rules do the math.")
else:
    st.sidebar.error(missing_api_key().message + " " + missing_api_key().fix)

with st.sidebar.popover("Delete this client", width="stretch"):
    st.warning(f"This deletes **{client_labels.get(active_client_id)}** and all of its saved work "
               "(files, fixes, and answers) for good. It can't be undone.")
    st.button("Yes, delete this client", type="primary", on_click=_delete_client, args=(active_client_id,),
              key=f"delete_client_{active_client_id}")
st.sidebar.caption(f"Version {APP_VERSION} (commit {_running_commit()})")

categorizer = TaxCategorizer()  # also loads any saved custom payee rules (custom_rules.json)
step_key = f"step__{active_client_id}"

# ----------------------------------------------------------------------------
# READING -- while files are being read, the page shows only the reading
# panel. Each part is saved as it arrives (core/processing.py), so Stop, a
# closed tab, or a crash never loses what was already read.
# ----------------------------------------------------------------------------
reading_status = processing.progress(store, active_client_id)
is_reading = st.session_state.reading.get(active_client_id, False)

if reading_status["files_waiting"] and is_reading:
    with st.container(key="ts_progress_panel"):
        head_slot = st.empty()
        bar_slot = st.empty()
        tick_slot = st.empty()
        stop_clicked = st.button("Stop for now", key=f"stop_reading_{active_client_id}",
                                 icon=":material/pause_circle:",
                                 help="Stops after the parts being read right now. Everything read so far stays saved.")
    if stop_clicked:
        st.session_state.reading[active_client_id] = False
        st.session_state.setdefault("reading_total", {}).pop(active_client_id, None)
        _flash("Stopped. Everything read so far is saved.")
        st.rerun()

    # Counted over the whole reading session, not per file, so the numbers
    # only ever go up while files finish one by one.
    remaining = reading_status["parts_total"] - reading_status["parts_done"]
    totals = st.session_state.setdefault("reading_total", {})
    total = totals[active_client_id] = max(totals.get(active_client_id, 0), remaining, 1)
    done_before = total - remaining
    started_at = time.monotonic()

    def _draw(done_now: int, message: str = "") -> None:
        done = min(done_before + done_now, total)
        head_slot.markdown(reading_html(done, total), unsafe_allow_html=True)
        bar_slot.progress(done / total)
        tick_slot.caption(f"Working… {int(time.monotonic() - started_at)}s" + (f" · {message}" if message else ""))

    _draw(0)
    started = processing.read_next_batch(store, active_client_id, int(tax_year), on_tick=_draw)
    processing.finish_ready_files(store, active_client_id, client_state, save_client)
    if not processing.progress(store, active_client_id)["files_waiting"]:
        st.session_state.reading[active_client_id] = False
        st.session_state.setdefault("reading_total", {}).pop(active_client_id, None)
        st.session_state.pop(step_key, None)  # open whichever step needs attention now
        _flash("All files are read.")
    elif not started:
        time.sleep(2)  # parts sent before a Stop are still on their way back
    st.rerun()

if reading_status["files_waiting"] and reading_status["parts_done"] >= reading_status["parts_total"]:
    processing.finish_ready_files(store, active_client_id, client_state, save_client)
    st.rerun()

# ----------------------------------------------------------------------------
# EVERYTHING ELSE IS REBUILT from the saved source of truth on every run
# (rules only, fast), so a fix, an answer, or a changed tax year shows
# everywhere at once.
# ----------------------------------------------------------------------------
has_files = bool(client_state["files"])
waiting_names = set(reading_status["waiting_files"])
failed_files = {name: entry["error"] for name, entry in client_state["files"].items()
                if entry.get("error") and name not in waiting_names}

questions: List[Dict[str, Any]] = []
if has_files:
    client_state.update(workpaper.rebuild(
        client_state["files"], client_state["answered_questions"], int(tax_year), categorizer))
    all_transactions: List[Dict[str, Any]] = client_state["all_transactions"]
    coverage_info = client_state["coverage_info"]
    reconciliation_summary = client_state["reconciliation_summary"]
    correction_log = client_state["correction_log"]

    exceptions = ExceptionAnalyzer().analyze_exceptions(all_transactions, de_minimis_threshold=de_minimis_threshold)
    questions = ClientQuestionGenerator().generate_question_list(
        exceptions, client_name=client_name, materiality_threshold=materiality_threshold)
    # A 1099 answer is a compliance note, not a recategorization, so nothing
    # about the transaction changes to drop its question; dropped here once
    # logged as applied.
    resolved_contractors = {e["payee"] for e in correction_log
                            if e.get("question_category") == "Form 1099 Verification" and e.get("applied")}
    questions = [q for q in questions if not (q["category"] == "Form 1099 Verification"
                                              and q.get("contractor") in resolved_contractors)]

    def _counts(tx):
        return not tx.get("category", "").startswith("Non-P&L:")

    gross_receipt_txs = [tx for tx in all_transactions if tx.get("is_deposit") and _counts(tx)]
    expense_txs = [tx for tx in all_transactions if not tx.get("is_deposit") and _counts(tx)]
    gross_receipts = sum(tx["amount"] for tx in gross_receipt_txs)
    expense_totals: Dict[str, float] = {}
    for tx in expense_txs:
        cat = tx.get("category", "Line 27a: Other expenses")
        amount = abs(tx.get("amount", 0.0)) * (0.5 if "Line 24b:" in cat else 1.0)  # IRS 50% meals limit
        expense_totals[cat] = expense_totals.get(cat, 0.0) + amount
    total_expenses = sum(expense_totals.values())
    net_profit = gross_receipts - total_expenses
    problem_count = reconciliation_summary.get("discrepancy_count", 0)
    _month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    missing_months = [_month_names[m - 1] for m in coverage_info.get("missing_months") or []]
else:
    problem_count, missing_months = 0, []

# ----------------------------------------------------------------------------
# WHAT TO DO NEXT + the four steps
# ----------------------------------------------------------------------------
STEPS = ["files", "numbers", "questions", "results"]
paused = bool(reading_status["files_waiting"])

if not has_files and not paused:
    suggested, tone, title, text = ("files", "attention", "Add this client's files.",
                                    "Start with the bank and credit card statements for the whole year.")
elif paused:
    suggested, tone, title, text = ("files", "attention", "Reading is paused.",
                                    "Press Continue reading. Nothing read so far is lost.")
elif failed_files:
    suggested, tone, title, text = ("files", "problem", f"{len(failed_files)} file(s) couldn't be read.",
                                    "See what went wrong under Add files, then press Try again.")
elif problem_count:
    suggested, tone, title, text = ("numbers", "problem", f"{problem_count} statement(s) don't add up.",
                                    "Open Check numbers and compare them with the statement.")
elif questions:
    suggested, tone, title, text = ("questions", "attention",
                                    f"Send {len(questions)} question(s) to the client.",
                                    "When they answer, pick their answers under Questions.")
else:
    suggested, tone, title, text = ("results", "done", "Everything is ready.",
                                    "Look over the results and download the Excel workpaper.")
if has_files and missing_months and suggested != "files":
    text += f" Also: statements for {len(missing_months)} month(s) are missing ({', '.join(missing_months)})."

if st.session_state.get(step_key) not in STEPS:
    st.session_state[step_key] = suggested


def _step_label(step: str) -> str:
    if step == "files":
        mark = " ⚠" if (failed_files or paused) else (" ✓" if has_files else "")
        return f"1 · Add files{mark}"
    if step == "numbers":
        mark = f" · {problem_count} to fix" if problem_count else (" ✓" if has_files else "")
        return f"2 · Check numbers{mark}"
    if step == "questions":
        mark = f" · {len(questions)} open" if questions else (" ✓" if has_files else "")
        return f"3 · Questions{mark}"
    return "4 · Results & download"


def _go_to(step: str) -> None:
    st.session_state[step_key] = step


st.markdown(next_step_html(tone, title, text), unsafe_allow_html=True)
if st.session_state[step_key] != suggested:
    st.button(f"Go to: {_step_label(suggested)}", on_click=_go_to, args=(suggested,), key=f"go_{active_client_id}")
st.write("")
with st.container(key="ts_steps"):
    st.segmented_control("Steps", options=STEPS, format_func=_step_label, key=step_key, required=True,
                         label_visibility="collapsed", width="stretch")
step = st.session_state.get(step_key) or suggested
st.write("")

# ----------------------------------------------------------------------------
# STEP 1 -- ADD FILES
# ----------------------------------------------------------------------------
if step == "files":
    st.subheader("Add this client's files")
    st.caption("Bank and credit card statements, check copies, receipts, and invoices, as PDF, photo, "
               "Excel, CSV, or Word. You can add more any time. A file with the same name as one already "
               "added is skipped, so nothing is counted twice.")

    _round_key = f"uploader_round_{active_client_id}"
    uploaded_files = st.file_uploader(
        "Drop files here, or click Browse files", accept_multiple_files=True,
        type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
        key=f"uploader_{active_client_id}_{st.session_state.get(_round_key, 0)}")
    if uploaded_files and st.button(f"Read {len(uploaded_files)} file(s)", type="primary",
                                    disabled=not _openai_key, key=f"start_reading_{active_client_id}"):
        queued = processing.queue_files(store, active_client_id, client_state,
                                        [(f.name, f.getvalue()) for f in uploaded_files])
        save_client()
        st.session_state[_round_key] = st.session_state.get(_round_key, 0) + 1
        for dup in queued["duplicates"]:
            _flash(f"Skipped {dup['filename']}: a file with that name was already added.")
        if queued["queued"]:
            st.session_state.reading[active_client_id] = True
        st.rerun()

    if paused:
        st.info(f"**Reading is paused.** {reading_status['parts_done']} of {reading_status['parts_total']} "
                f"part(s) of {reading_status['files_waiting']} file(s) are read and saved.")
        paused_buttons = st.container(horizontal=True)
        if paused_buttons.button("Continue reading", type="primary", disabled=not _openai_key,
                                 key=f"continue_reading_{active_client_id}"):
            st.session_state.reading[active_client_id] = True
            st.rerun()
        with paused_buttons.popover("Remove the files not read yet"):
            st.caption("These files will be removed: " + ", ".join(reading_status["waiting_files"]) +
                       ". Files already read stay.")
            if st.button("Yes, remove them", key=f"remove_waiting_{active_client_id}"):
                processing.stop_waiting_files(store, active_client_id)
                st.rerun()

    if failed_files:
        st.error(f"**{len(failed_files)} file(s) couldn't be read.** Nothing from them is counted yet.")
        for name, err in failed_files.items():
            where = f" (at {err['where']})" if err.get("where") else ""
            with st.container(border=True):
                st.markdown(f"**{name}**{where}  \n{err['message']}  \n**What to do:** {err['fix']}")
                row = st.container(horizontal=True)
                if name in reading_status["failed_files"]:
                    if row.button("Try again", key=f"retry_{active_client_id}_{name}", type="primary",
                                  disabled=not _openai_key,
                                  help="Reads only the parts that failed. Parts already read are kept."):
                        processing.retry_file(store, active_client_id, name)
                        st.session_state.reading[active_client_id] = True
                        st.rerun()
                if row.button("Remove this file", key=f"remove_failed_{active_client_id}_{name}"):
                    processing.remove_file(store, active_client_id, client_state, name)
                    save_client()
                    st.rerun()
                st.caption(f"Error code: {err['code']}")
                if err.get("detail"):
                    with st.expander("Technical detail"):
                        st.code(err["detail"], language=None)

    if has_files and missing_months:
        st.warning(f"**Some months are missing:** {', '.join(missing_months)}. "
                   "Ask the client for these statements so the year is complete.")

    if has_files:
        with st.expander(f"Files for this client ({len(client_state['files'])})"):
            file_rows = []
            for name, entry in client_state["files"].items():
                status_text = ("⚠️ Couldn't read" if entry.get("error")
                               else f"✅ Read · {len(entry.get('documents') or [])} document(s)")
                file_rows.append({"File": name, "Status": status_text, "Parts": entry.get("part_count", 0)})
            for name in reading_status["waiting_files"]:
                file_rows.append({"File": name, "Status": "⏳ Waiting to be read", "Parts": None})
            st.dataframe(pd.DataFrame(file_rows), hide_index=True, width="stretch")
            if client_state.get("duplicates_flagged"):
                st.caption("Skipped because a file with the same name was already added: " +
                           ", ".join(sorted({d["filename"] for d in client_state["duplicates_flagged"]})))
            remove_choice = st.selectbox("Remove a file and everything read from it",
                                         options=[""] + list(client_state["files"]),
                                         key=f"remove_choice_{active_client_id}")
            if remove_choice and st.button(f"Remove {remove_choice}", key=f"remove_file_{active_client_id}"):
                processing.remove_file(store, active_client_id, client_state, remove_choice)
                save_client()
                st.rerun()
    st.caption("Tip: don't add a profit & loss summary together with the statements it comes from; "
               "the same money would be counted twice.")

# ----------------------------------------------------------------------------
# STEP 2 -- CHECK THE NUMBERS
# ----------------------------------------------------------------------------
elif step == "numbers":
    st.subheader("Check the numbers")
    if has_files:
        fix_statements_ui.render(client_state, active_client_id, save_client)
    else:
        st.info("Nothing to check yet. Add this client's files first.")

# ----------------------------------------------------------------------------
# STEP 3 -- CLIENT QUESTIONS
# ----------------------------------------------------------------------------
elif step == "questions":
    st.subheader("Questions for the client")
    if not has_files:
        st.info("No questions yet. Add this client's files first.")
    else:
        st.markdown(
            "1. Send these questions to the client, the way you usually do. This tool never sends anything.  \n"
            "2. When they answer, pick their answer in the **Answer** column.  \n"
            "3. Press **Save answers**. The numbers update right away, and an answered question doesn't come back."
        )
        editors: Dict[str, pd.DataFrame] = {}

        def _apply_answers_and_rerun(answered: List[Dict[str, Any]], empty_message: str):
            """Shared by the "same answer for all checked rows" buttons and the
            main Save button: applies, saves, and reruns only when something
            changed (a message right before st.rerun() would never show)."""
            _, new_log_entries = apply_client_answers(all_transactions, answered)
            applied_count = sum(1 for e in new_log_entries if e["applied"])
            if applied_count:
                client_state["answered_questions"] = client_state["answered_questions"] + [
                    q for q in answered if (q.get("answer") or "").strip()]
                save_client()
                _flash(f"Saved {applied_count} answer(s).")
                st.rerun()
            else:
                st.info(empty_message)

        def render_question_editor(title: str, plain: str, category: str, options: List[str]):
            subset = [q for q in questions if q["category"] == category]
            st.markdown(f"#### {title} ({len(subset)})")
            st.caption(plain)
            if not subset:
                st.caption("Nothing to ask here.")
                return
            df = pd.DataFrame(subset)[["item_id", "date", "payee", "count", "amount", "question",
                                       "client_response", "answer"]]
            df.insert(0, "select", False)
            edited = st.data_editor(
                df, hide_index=True, width="stretch", num_rows="fixed", key=f"editor_{category}",
                # The answer sits right after who and how much, so it's on screen
                # without scrolling sideways.
                column_order=["select", "payee", "amount", "answer", "question", "date", "count",
                              "client_response", "item_id"],
                column_config={
                    "select": st.column_config.CheckboxColumn("", width="small",
                                                              help="Check rows to give them all the same answer below"),
                    "item_id": st.column_config.TextColumn("#", disabled=True, width="small"),
                    "date": st.column_config.TextColumn("Date(s)", disabled=True, width="small"),
                    "payee": st.column_config.TextColumn("Paid to", disabled=True),
                    "count": st.column_config.NumberColumn("How many", disabled=True, width="small"),
                    "amount": st.column_config.TextColumn("Total", disabled=True, width="small"),
                    "question": st.column_config.TextColumn("Question", disabled=True, width="large"),
                    "client_response": st.column_config.TextColumn("Client's notes"),
                    # The grid shows no dropdown arrow until a cell is opened, so
                    # the hint is in the column name itself.
                    "answer": st.column_config.SelectboxColumn("Answer (pick one) ▾", options=options,
                                                               required=False, width="medium"),
                })
            editors[category] = edited
            selected_ids = set(edited.loc[edited["select"] == True, "item_id"])  # noqa: E712
            bulk_col, button_col = st.columns([3, 2])
            with bulk_col:
                bulk_value = st.selectbox("Same answer for all checked rows", options=[""] + options,
                                          key=f"bulk_value_{category}")
            with button_col:
                st.write("")
                bulk_clicked = st.button(
                    f"Give it to {len(selected_ids)} checked row(s)" if selected_ids else "Check rows first",
                    key=f"bulk_apply_{category}", disabled=not (selected_ids and bulk_value))
            if bulk_clicked:
                answered = [{**q, "answer": bulk_value, "client_response": q.get("client_response", "")}
                            for q in subset if q["item_id"] in selected_ids]
                _apply_answers_and_rerun(answered, "Check a row's box and pick an answer first.")

        render_question_editor("Is this a business expense?",
                               "Charges that might be personal. Answer Business, Personal, or Owner Draw.",
                               "Expense Verification", ANSWER_OPTIONS["Expense Verification"])
        render_question_editor("Is this equipment the business keeps?",
                               "Large purchases that may need to be depreciated instead of deducted.",
                               "Asset Purchase", ANSWER_OPTIONS["Asset Purchase"])
        render_question_editor("What was this for?",
                               "Payments the rules couldn't place. Pick the tax form line they belong on.",
                               "Uncategorized Expense", uncategorized_expense_answer_options())
        render_question_editor("Was a 1099 form sent?",
                               "People or companies paid enough that a Form 1099 may be needed.",
                               "Form 1099 Verification", ANSWER_OPTIONS["Form 1099 Verification"])

        st.write("")
        if st.button("Save answers", type="primary", key="save_answers"):
            answered = []
            for q in questions:
                edited_df = editors.get(q["category"])
                if edited_df is None:
                    continue
                match = edited_df[edited_df["item_id"] == q["item_id"]]
                if match.empty:
                    continue
                row = match.iloc[0]
                answered.append({**q, "answer": row.get("answer", "") or "",
                                 "client_response": row.get("client_response", "") or ""})
            _apply_answers_and_rerun(answered, "No new answers yet. Pick an answer in the Answer column first.")

        if correction_log:
            with st.expander(f"Answers already saved ({len(correction_log)})"):
                st.dataframe(pd.DataFrame(correction_log)[["item_id", "question_category", "payee", "client_answer",
                                                           "old_category", "new_category", "applied", "note"]],
                             width="stretch", hide_index=True)

# ----------------------------------------------------------------------------
# STEP 4 -- RESULTS & DOWNLOAD
# ----------------------------------------------------------------------------
else:
    st.subheader("Results")
    if not has_files:
        st.info("No results yet. Add this client's files first.")
    else:
        if problem_count:
            st.error(f"**{problem_count} statement(s) don't add up yet,** so these totals may be wrong. "
                     "Fix them under Check numbers first.")
        st.markdown(cards_html([
            ("Business income (Line 1)", f"${gross_receipts:,.2f}", "Money in, minus transfers and loans", False),
            ("Business costs (Line 28)", f"${total_expenses:,.2f}", "Meals counted at 50%", False),
            ("Profit or loss (Line 31)", f"${net_profit:,.2f}", "Income minus costs", True),
            ("Questions still open", f"{len(questions)}", "Answer them under Questions", False),
        ]), unsafe_allow_html=True)

        def render_breakdown(txs: List[Dict[str, Any]], noun: str):
            """The same transactions that were added up, grouped by file and by
            line, plus the largest ones, so every number traces back to rows."""
            if not txs:
                st.caption(f"No {noun}.")
                return
            by_file: Dict[str, float] = {}
            by_category: Dict[str, float] = {}
            for tx in txs:
                by_file[tx.get("source_file", "Unknown")] = by_file.get(tx.get("source_file", "Unknown"), 0.0) + abs(tx["amount"])
                by_category[tx.get("category", "Uncategorized")] = by_category.get(tx.get("category", "Uncategorized"), 0.0) + abs(tx["amount"])

            def _table(items):
                return pd.DataFrame(sorted(items, key=lambda kv: -kv[1]), columns=["", "Amount ($)"]).assign(
                    **{"Amount ($)": lambda d: d["Amount ($)"].map(lambda v: f"${v:,.2f}")})

            col_a, col_b = st.columns(2)
            col_a.markdown("**By file**")
            col_a.dataframe(_table(by_file.items()), width="stretch", hide_index=True)
            col_b.markdown("**By tax form line**")
            col_b.dataframe(_table(by_category.items()), width="stretch", hide_index=True)
            st.markdown(f"**Largest {noun}** (up to 15 of {len(txs)})")
            top = sorted(txs, key=lambda tx: -abs(tx["amount"]))[:15]
            st.dataframe(pd.DataFrame(top)[["date", "payee", "amount", "category", "source_file"]],
                         width="stretch", hide_index=True)

        with st.expander("How these numbers were worked out"):
            st.markdown("**Business income** is every deposit on the statements, except money that isn't "
                        "income: transfers between the client's own accounts, credit card payments, loans, "
                        "owner contributions, tax refunds, returned deposits, vendor credits, and anything the "
                        "client said was personal.")
            render_breakdown(gross_receipt_txs, "deposits")
            st.markdown("**Business costs** are every payment on the statements, with the same exceptions. "
                        "Meals (Line 24b) count at 50%, the IRS limit; the amounts listed are before that cut.")
            render_breakdown(expense_txs, "payments")
            st.markdown(f"**Profit or loss** = income − costs = ${gross_receipts:,.2f} − ${total_expenses:,.2f} "
                        f"= **${net_profit:,.2f}**.".replace("$", "\\$"))

        st.markdown("#### By tax form line")
        summary_rows = [{"Line": "Line 1", "What it is": "Gross receipts (business income)",
                         "Amount": f"${gross_receipts:,.2f}"}]
        for cat in sorted(expense_totals):
            line_num = cat.split(":")[0] if ":" in cat else "Line 27a"
            desc = cat.split(":")[1].strip() if ":" in cat else cat
            summary_rows.append({"Line": line_num, "What it is": desc + (" (50% of meals)" if "24b" in line_num else ""),
                                 "Amount": f"${expense_totals[cat]:,.2f}"})
        summary_rows.append({"Line": "Line 28", "What it is": "Total costs", "Amount": f"${total_expenses:,.2f}"})
        summary_rows.append({"Line": "Line 31", "What it is": "Profit or loss", "Amount": f"${net_profit:,.2f}"})
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)

        line_options = ["Line 1: Gross Receipts / Sales"] + sorted(expense_totals)
        selected_line = st.selectbox("See the transactions in one line", options=line_options,
                                     key="summary_line_inspect")
        inspect_txs = (gross_receipt_txs if selected_line == line_options[0]
                       else [tx for tx in expense_txs if tx.get("category") == selected_line])
        if "24b" in selected_line:
            st.caption(f"These add up to ${sum(abs(t['amount']) for t in inspect_txs):,.2f} in full; "
                       "the line above counts 50% of that.")
        if inspect_txs:
            st.dataframe(pd.DataFrame(inspect_txs)[["date", "payee", "amount", "source_file"]],
                         width="stretch", hide_index=True)

        non_pnl = exceptions["non_pnl_transfers"]
        with st.expander(f"Not counted: transfers, card payments, and personal items ({len(non_pnl)})"):
            st.caption("These were left out of income and costs. The Category column says why.")
            if non_pnl:
                st.dataframe(pd.DataFrame(non_pnl)[["date", "payee", "amount", "category", "source_file"]],
                             width="stretch", hide_index=True)

        check_records = client_state["check_records"]
        unmatched_check_records = client_state["unmatched_check_records"]
        supporting_documents = client_state["supporting_documents"]
        if check_records or supporting_documents:
            with st.expander("Check copies, receipts, and invoices"):
                st.caption("These aren't added to the totals. A check copy only gives the name of who was paid "
                           "for the matching \"Check #\" line on a statement.")
                if check_records:
                    st.markdown(f"**Check copies matched to a statement line:** "
                                f"{len(check_records) - len(unmatched_check_records)} of {len(check_records)}")
                    columns = ["check_number", "payee", "drawer", "numerical_amount", "issue_date", "source_file"]
                    frame = pd.DataFrame(check_records)
                    st.dataframe(frame[[c for c in columns if c in frame.columns]], width="stretch", hide_index=True)
                if unmatched_check_records:
                    st.warning(f"**{len(unmatched_check_records)} check copy(ies) didn't match** any check on the "
                               "statements (no check with that number or amount). Check them by hand.")
                if supporting_documents:
                    st.markdown(f"**Receipts and invoices ({len(supporting_documents)})**, for reference only:")
                    columns = ["type", "issuer", "document_number", "date", "total", "source_file"]
                    frame = pd.DataFrame(supporting_documents)
                    st.dataframe(frame[[c for c in columns if c in frame.columns]], width="stretch", hide_index=True)

        with st.expander("Details for experts"):
            tab_lines, tab_recon, tab_large, tab_log = st.tabs(
                ["All transactions", "Statement checks", "Large amounts", "What the AI read"])
            with tab_lines:
                if all_transactions:
                    df_tx = pd.DataFrame(all_transactions)
                    fc1, fc2 = st.columns(2)
                    search_query = fc1.text_input("Search by name", key="tx_search")
                    cat_filter = fc2.selectbox("Tax form line", ["All"] + sorted(df_tx["category"].unique()),
                                               key="cat_filter")
                    shown = df_tx
                    if search_query:
                        shown = shown[shown["payee"].str.contains(search_query, case=False, na=False)
                                      | shown["description"].str.contains(search_query, case=False, na=False)]
                    if cat_filter != "All":
                        shown = shown[shown["category"] == cat_filter]
                    st.caption(f"Showing {len(shown):,} of {len(df_tx):,}.")
                    st.dataframe(shown[["date", "payee", "amount", "is_deposit", "category", "confidence_state",
                                        "original_category", "source_file"]], width="stretch", height=500)
            with tab_recon:
                st.caption("Each statement is checked against its own printed numbers. One with nothing to "
                           "check against is marked so, never as a pass.")
                results = client_state["reconciliation_results"]
                if results:
                    st.dataframe(pd.DataFrame(results)[["source_file", "status", "transaction_count",
                                                        "declared_deposits", "extracted_deposits",
                                                        "declared_withdrawals", "extracted_withdrawals", "notes"]],
                                 width="stretch")
            with tab_large:
                large_txs = exceptions.get("unusual_large_txs", [])
                st.caption(f"Single amounts of $5,000 or more ({len(large_txs)}).")
                if large_txs:
                    st.dataframe(pd.DataFrame(large_txs)[["date", "payee", "amount", "reason"]], width="stretch")
            with tab_log:
                for entry in client_state["diagnostics_log"]:
                    with st.expander(f"{'⚠️ ' if entry.get('ai_error') else ''}{entry['file']} — "
                                     f"{entry['type']} ({entry['count']} item(s))"):
                        for line in entry.get("details") or []:
                            st.write(f"- {line}")

        st.markdown("#### Download")
        excel_buffer = ExcelWorkpaperExporter().generate_workpaper(
            client_name=client_name, tax_year=int(tax_year), transactions=all_transactions,
            exceptions_dict=exceptions, questions=questions, coverage_info=coverage_info,
            duplicates_info=client_state["duplicates_flagged"], reconciliation_summary=reconciliation_summary,
            correction_log=correction_log, manual_edits=client_state["manual_edits"])
        st.download_button("Download the Excel workpaper", data=excel_buffer, icon=":material/download:",
                           file_name=f"Schedule_C_Workpaper_{client_name.replace(' ', '_')}_{tax_year}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
