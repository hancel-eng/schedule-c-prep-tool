import streamlit as st
import pandas as pd
import io
import os
import subprocess
import time
from typing import List, Dict, Any

from core.ai_reader import SUPPORTED_EXTENSIONS
from core.ai_errors import missing_api_key
from core import processing, workpaper
from core.storage import MemoryStore, PostgresStore, StorageError, new_client_state
from core.tax_categorizer import TaxCategorizer, SCHEDULE_C_CATEGORIES
from core.exception_analyzer import ExceptionAnalyzer
from core.question_generator import ClientQuestionGenerator
from core.excel_exporter import ExcelWorkpaperExporter
from core.answer_applier import apply_client_answers, ANSWER_OPTIONS, uncategorized_expense_answer_options
from core.check_matcher import match_checks_to_transactions
from theme import CSS, render_progress_steps
import fix_statements_ui

# Bumped on every meaningful change to this file, so whoever is looking at the
# app can tell which version is running just by glancing at the sidebar --
# there is no separate deploy/build pipeline that would otherwise show that.
APP_VERSION = "v17"


def _running_commit() -> str:
    """Short git commit hash of the code actually running, so a stale
    Streamlit Cloud deploy (same APP_VERSION, old commit underneath) is
    visible in the sidebar instead of silently indistinguishable from a
    fresh one. Returns "unknown" if git isn't available at runtime."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5,
        )
        commit = result.stdout.strip()
        return commit if result.returncode == 0 and commit else "unknown"
    except Exception:
        return "unknown"

# Page Configuration
st.set_page_config(
    page_title="Schedule C Intake & Tax Prep Tool",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Visual theme only -- injected once, here, for the whole script run. See
# theme.py; nothing below this line should add another <style> block.
st.markdown(CSS, unsafe_allow_html=True)


def _get_app_password() -> str:
    """st.secrets.get() only guards a missing *key* -- when secrets.toml
    doesn't exist at all (e.g. a fresh checkout before anyone has set it up),
    st.secrets raises StreamlitSecretNotFoundError instead, which isn't a
    KeyError and isn't caught by .get()'s own default. Treat "no secrets
    file" the same as "no password configured" rather than crashing."""
    try:
        return st.secrets.get("app_password", "")
    except FileNotFoundError:
        return ""


def _get_openai_api_key() -> str:
    """Copied into os.environ below so core/ai_reader.py (no Streamlit
    import) reads it the ordinary way."""
    try:
        return st.secrets.get("OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    except FileNotFoundError:
        return os.environ.get("OPENAI_API_KEY", "")


_openai_key = _get_openai_api_key()
if _openai_key:
    os.environ["OPENAI_API_KEY"] = _openai_key

def check_password() -> bool:
    """Gates the whole app behind one shared password so a link leak doesn't
    let a stranger run client data through the tool. The password lives in
    Streamlit secrets (.streamlit/secrets.toml locally, the "Secrets" panel
    on Streamlit Community Cloud), never in code -- see
    .streamlit/secrets.toml.example for the exact key it reads."""
    if st.session_state.get("password_correct"):
        return True

    st.title("Schedule C Intake & Tax Prep Workpaper Tool")
    st.caption("This tool is private. Enter the access password to continue.")

    def _submit():
        expected = _get_app_password()
        entered = st.session_state.get("password_input", "")
        st.session_state.password_correct = bool(expected) and entered == expected
        del st.session_state["password_input"]

    st.text_input("Password", type="password", key="password_input", on_change=_submit)

    if st.session_state.get("password_correct") is False:
        st.error("Incorrect password.")
    if not _get_app_password():
        st.warning(
            "No app_password is configured in Streamlit secrets, so no password "
            "will ever be accepted. See .streamlit/secrets.toml.example."
        )
    return False


if not check_password():
    st.stop()

# Title & Description -- native title/caption so the theme's h1 styling and
# secondary-text color apply without a bespoke CSS class per element.
st.title("Schedule C Intake & Tax Prep Workpaper Tool")
st.caption("Turns bank/credit card statements into a client question list and an audit-ready Schedule C workpaper.")

# Short messages shown once on the next page draw. A toast shown right
# before st.rerun() would vanish with the rerun.
def _flash(message: str) -> None:
    st.session_state.setdefault("flash", []).append(message)


for _message in st.session_state.pop("flash", []):
    st.toast(_message)

# ----------------------------------------------------------------------------
# SAVED WORK -- every client's work is saved in a private database
# (core/storage.py) the moment it changes, so closing the tab, a restart, or
# switching clients never loses anything. Without DATABASE_URL the app still
# works, but only for this browser session, and the sidebar says so.
# ----------------------------------------------------------------------------
DEFAULT_TAX_YEAR = 2026


def _get_database_url() -> str:
    try:
        return st.secrets.get("DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    except FileNotFoundError:
        return os.environ.get("DATABASE_URL", "")


@st.cache_resource(show_spinner="Connecting to your saved work…")
def _database_store(url: str) -> PostgresStore:
    return PostgresStore(url)


def _get_store():
    url = _get_database_url()
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
st.sidebar.selectbox(
    "Active client", options=client_ids, format_func=lambda cid: client_labels.get(cid, cid),
    key="active_client_id",
    help="Switch any time. Every client's work is kept, so nothing needs to be uploaded again.",
)
with st.sidebar.popover("+ New Client", width="stretch"):
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
    st.sidebar.caption("✓ Work is saved automatically.")
else:
    st.sidebar.warning("Work is **not** being saved: closing this tab loses it. "
                       "To save it, add DATABASE_URL to the app's secrets (see README).")

st.sidebar.markdown("---")

# Each client's own fields (name, tax year, thresholds) have their own widget
# keys, filled from the saved client the first time they're shown and saved
# back the moment they change.
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


st.sidebar.header("Client & Tax Year Metadata")
client_name = st.sidebar.text_input(
    "Client Name / Business Name", key=_meta_key("client_name"), placeholder="e.g. Acme Consulting LLC",
    on_change=_on_meta_change, args=(active_client_id, "client_name"),
)
tax_year = st.sidebar.number_input("Tax Year", step=1, key=_meta_key("tax_year"),
                                   on_change=_on_meta_change, args=(active_client_id, "tax_year"))

st.sidebar.markdown("---")
st.sidebar.header("Minimum & Question Rules")
de_minimis_threshold = st.sidebar.number_input(
    "Fixed Asset Threshold ($)", step=100.0, key=_meta_key("de_minimis_threshold"),
    on_change=_on_meta_change, args=(active_client_id, "de_minimis_threshold"))
materiality_threshold = st.sidebar.number_input(
    "Client Question Materiality Threshold ($)", step=1.0, key=_meta_key("materiality_threshold"),
    on_change=_on_meta_change, args=(active_client_id, "materiality_threshold"),
    help="A vendor group whose total is at or below this amount gets no client "
         "question at all -- no one needs to be asked about a single $3 charge. "
         "Applies to the group's total, not each individual charge."
)

st.sidebar.markdown("---")
st.sidebar.header("AI Reader")
if _openai_key:
    st.sidebar.caption("✓ Ready. The AI reads every file; plain rules do the math and the categories.")
else:
    st.sidebar.error(missing_api_key().message + " " + missing_api_key().fix)

with st.sidebar.popover("Delete this client", width="stretch"):
    st.warning(f"This permanently deletes **{client_labels.get(active_client_id)}** and all of its saved "
               "work: files, hand fixes, and client answers. It cannot be undone.")
    st.button("Yes, delete this client", type="primary", on_click=_delete_client, args=(active_client_id,),
              key=f"delete_client_{active_client_id}")

# Custom payee rules a prior session may have saved (core/tax_categorizer.py's
# TaxCategorizer loads custom_rules.json automatically) still apply here even
# with no in-app editor for them right now.
categorizer = TaxCategorizer()

# ----------------------------------------------------------------------------
# 1. UPLOAD AND READ -- files are saved first, then read a few parts at a
# time. Every part's answer is saved the moment it arrives (core/processing.py),
# so Stop, a closed tab, or a crash never loses what was already read.
# ----------------------------------------------------------------------------
st.subheader("1. Upload Client Documents")
st.caption(
    "Add files any time, even after some are read. A file with the same name as one already uploaded "
    "for this client is skipped, so nothing is counted twice. Reading is saved as it goes: you can "
    "press **Stop**, close the page, and continue later."
)

_round_key = f"uploader_round_{active_client_id}"
uploaded_files = st.file_uploader(
    "Upload statements, checks, receipts, invoices, photos, spreadsheets, or Word files",
    accept_multiple_files=True,
    type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
    key=f"uploader_{active_client_id}_{st.session_state.get(_round_key, 0)}",
)
if uploaded_files:
    if st.button(f"Read {len(uploaded_files)} file(s)", type="primary", disabled=not _openai_key,
                 key=f"start_reading_{active_client_id}"):
        queued = processing.queue_files(store, active_client_id, client_state,
                                        [(f.name, f.getvalue()) for f in uploaded_files])
        save_client()
        st.session_state[_round_key] = st.session_state.get(_round_key, 0) + 1
        for dup in queued["duplicates"]:
            _flash(f"Skipped {dup['filename']}: a file with that name was already uploaded.")
        if queued["queued"]:
            st.session_state.reading[active_client_id] = True
        st.rerun()

reading_status = processing.progress(store, active_client_id)
is_reading = st.session_state.reading.get(active_client_id, False)

if reading_status["files_waiting"] and is_reading:
    with st.container(key="ts_progress_panel"):
        steps_slot = st.empty()
        bar_slot = st.empty()
        tick_slot = st.empty()
        stop_clicked = st.button(
            "Stop", key=f"stop_reading_{active_client_id}", icon=":material/stop_circle:",
            help="Stops after the parts being read right now. Everything read so far stays saved.")
    if stop_clicked:
        st.session_state.reading[active_client_id] = False
        _flash("Stopped. Everything read so far is saved.")
        st.rerun()

    total = reading_status["parts_total"] or 1
    done_before = reading_status["parts_done"]
    started_at = time.monotonic()

    def _draw(done_now: int, message: str = "") -> None:
        done = min(done_before + done_now, total)
        render_progress_steps(steps_slot, {"extract": "active"},
                              active_meta=f"{done} of {total} part(s) read and saved · "
                                          f"{reading_status['files_waiting']} file(s) waiting")
        bar_slot.progress(done / total)
        seconds = int(time.monotonic() - started_at)
        tick_slot.caption(f"Reading the next parts… {seconds}s" + (f" · {message}" if message else ""))

    _draw(0)
    started = processing.read_next_batch(store, active_client_id, int(tax_year), on_tick=_draw)
    processing.finish_ready_files(store, active_client_id, client_state, save_client)
    after = processing.progress(store, active_client_id)
    if not after["files_waiting"]:
        st.session_state.reading[active_client_id] = False
        _flash("Done reading.")
    elif not started:
        time.sleep(2)  # parts sent before a Stop are still on their way back
    st.rerun()

if reading_status["files_waiting"] and not is_reading:
    if reading_status["parts_done"] >= reading_status["parts_total"]:
        processing.finish_ready_files(store, active_client_id, client_state, save_client)
        st.rerun()
    st.info(
        f"**Reading is paused.** {reading_status['parts_done']} of {reading_status['parts_total']} part(s) "
        f"of {reading_status['files_waiting']} file(s) are read and saved. Nothing is lost; continue any time."
    )
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

# Files the AI could not read: what went wrong, what to do, and buttons to
# read it again (only the failed parts) or remove it.
waiting_names = set(reading_status["waiting_files"])
failed_files = {name: entry["error"] for name, entry in client_state["files"].items()
                if entry.get("error") and name not in waiting_names}
if failed_files:
    st.error(f"**The AI could not read {len(failed_files)} file(s).** Nothing from them is counted. "
             "Here is what went wrong:")
    for name, err in failed_files.items():
        where = f" (while reading {err['where']})" if err.get("where") else ""
        st.markdown(f"- **{name}**{where} — {err['message']}  \n"
                    f"  **What to do:** {err['fix']}  \n"
                    f"  `Error code: {err['code']}`")
        row = st.container(horizontal=True)
        if name in reading_status["failed_files"]:
            if row.button("Try again", key=f"retry_{active_client_id}_{name}", disabled=not _openai_key,
                          help="Reads only the parts that failed. Parts already read are kept."):
                processing.retry_file(store, active_client_id, name)
                st.session_state.reading[active_client_id] = True
                st.rerun()
        if row.button("Remove this file", key=f"remove_failed_{active_client_id}_{name}"):
            processing.remove_file(store, active_client_id, client_state, name)
            save_client()
            st.rerun()
        if err.get("detail"):
            with st.expander(f"Technical detail for {name}"):
                st.code(err["detail"], language=None)

if client_state["files"]:
    with st.expander(f"Files for this client ({len(client_state['files'])})"):
        file_rows = []
        for name, entry in client_state["files"].items():
            if entry.get("error"):
                status_text = "⚠️ Could not read"
            else:
                status_text = f"✅ Read · {len(entry.get('documents') or [])} document(s)"
            file_rows.append({"File": name, "Status": status_text, "Parts": entry.get("part_count", 0)})
        for name in reading_status["waiting_files"]:
            file_rows.append({"File": name, "Status": "⏳ Waiting to be read", "Parts": None})
        st.dataframe(pd.DataFrame(file_rows), hide_index=True, width="stretch")
        if client_state.get("duplicates_flagged"):
            st.caption("Skipped because a file with the same name was already uploaded: " +
                       ", ".join(sorted({d["filename"] for d in client_state["duplicates_flagged"]})))
        remove_choice = st.selectbox("Remove a file and everything read from it",
                                     options=[""] + list(client_state["files"]),
                                     key=f"remove_choice_{active_client_id}")
        if remove_choice and st.button(f"Remove {remove_choice}", key=f"remove_file_{active_client_id}"):
            processing.remove_file(store, active_client_id, client_state, remove_choice)
            save_client()
            st.rerun()

if client_state["files"]:
    # Rebuilt from the source of truth on every run (rules only, fast), so a
    # hand correction, a client answer, or a changed tax year is reflected
    # everywhere at once.
    client_state.update(workpaper.rebuild(
        client_state["files"], client_state["answered_questions"], int(tax_year), categorizer))
    all_transactions: List[Dict[str, Any]] = client_state["all_transactions"]
    diagnostics_log = client_state["diagnostics_log"]
    coverage_info = client_state["coverage_info"]
    reconciliation_summary = client_state["reconciliation_summary"]
    reconciliation_results = client_state["reconciliation_results"]
    duplicates_flagged = client_state["duplicates_flagged"]
    correction_log = client_state["correction_log"]
    check_records = client_state["check_records"]
    unmatched_check_records = client_state["unmatched_check_records"]
    supporting_documents = client_state["supporting_documents"]
    recon_status = reconciliation_summary['status']

    # Recomputed fresh every run, straight from the current (possibly
    # client-corrected) transaction list, so an applied answer's effect
    # shows up immediately and an answered question never regenerates.
    analyzer = ExceptionAnalyzer()
    exceptions = analyzer.analyze_exceptions(
        all_transactions, de_minimis_threshold=de_minimis_threshold
    )

    q_gen = ClientQuestionGenerator()
    questions = q_gen.generate_question_list(
        exceptions, client_name=client_name, materiality_threshold=materiality_threshold
    )
    # A 1099 answer is a compliance note, not a transaction
    # recategorization (see core/answer_applier.py), so nothing about the
    # underlying transaction changes to naturally drop the question the
    # way an answered personal-expense or uncategorized one does --
    # dropped here instead, once logged as applied.
    resolved_contractors = {
        entry["payee"] for entry in correction_log
        if entry.get("question_category") == "Form 1099 Verification" and entry.get("applied")
    }
    questions = [
        q for q in questions
        if not (q["category"] == "Form 1099 Verification" and q.get("contractor") in resolved_contractors)
    ]

    st.caption(f"{len(questions)} question(s) for the client")

    if not coverage_info['is_complete']:
        st.warning(f"**12-Month Coverage Warning:** {coverage_info['status_message']}")

    if recon_status == "Discrepancy":
        st.error(f"**Reconciliation Discrepancy:** {reconciliation_summary['message']} "
                 "Open the **Fix Statements** tab below to correct them.")
    elif recon_status not in ("Reconciled",):
        st.warning(f"**Reconciliation — {recon_status}:** {reconciliation_summary['message']}")

    # Robust Totals Calculation
    gross_receipts = sum(tx['amount'] for tx in all_transactions if tx.get('is_deposit') and not tx.get('category', '').startswith("Non-P&L:"))

    expense_totals = {}
    for tx in all_transactions:
        if not tx.get('is_deposit') and not tx.get('category', '').startswith("Non-P&L:"):
            cat = tx.get('category', 'Line 27a: Other expenses')
            amt = abs(tx.get('amount', 0.0))
            if "Line 24b:" in cat:
                amt = amt * 0.5
            expense_totals[cat] = expense_totals.get(cat, 0.0) + amt

    total_expenses = sum(expense_totals.values())
    net_profit = gross_receipts - total_expenses

    # ----------------------------------------------------
    # PREPARER DASHBOARD
    # ----------------------------------------------------
    st.markdown("---")
    st.subheader("2. Preparer Dashboard")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Gross Receipts (Line 1)", f"${gross_receipts:,.2f}", help="See \"How is Gross Receipts calculated?\" below.")
    with col2:
        st.metric("Total Expenses (Line 28)", f"${total_expenses:,.2f}", help="See \"How is Total Expenses calculated?\" below.")
    with col3:
        st.metric("Net Profit / (Loss) (Line 31)", f"${net_profit:,.2f}", help="See \"How is Net Profit / (Loss) calculated?\" below.")
    with col4:
        st.metric(
            "Client Questions", len(questions), delta=f"{exceptions['total_exception_count']} exceptions total", delta_color="off",
            help=(
                "Client Questions = open items in the 4 answerable categories below "
                "(Expense Verification, Asset Purchase, Uncategorized Expense, Form "
                "1099 Verification), minus anything already answered. Exceptions "
                "total is broader -- it also counts Non-P&L exclusions and large-"
                "transaction flags, which are informational and never turn into a "
                "client question on their own."
            ),
        )

    # "We need to show a formula or where it's getting that number from" --
    # raised directly in a client meeting after Gross Receipts showed a
    # figure that turned out to be wrong (see CHANGELOG.md, 2026-09-17), then
    # widened to every dashboard figure: "I want all calculations to show how
    # they were calculated, so anyone reading can understand what was done,
    # without having to invent anything, and without the AI or anyone
    # hallucinating." Every breakdown below is the literal transaction data
    # already computed above (grouped/sorted, never re-described or
    # summarized in prose by anything, AI included) -- the same numbers, just
    # shown broken apart instead of only as one final total, so nothing here
    # can say anything the underlying transactions don't already say.
    def render_transaction_breakdown(txs: List[Dict[str, Any]], noun: str, amount_key: str = "amount"):
        """Shared by every "how was this calculated" expander below: groups
        the exact same transactions that were summed into the metric above,
        by source file and by category, plus the largest individual amounts
        -- so a number can always be traced back to real rows, not a
        separately-written explanation of them."""
        if not txs:
            st.caption(f"No {noun} in this run.")
            return
        by_file: Dict[str, float] = {}
        by_category: Dict[str, float] = {}
        for tx in txs:
            by_file[tx.get("source_file", "Unknown")] = by_file.get(tx.get("source_file", "Unknown"), 0.0) + abs(tx[amount_key])
            by_category[tx.get("category", "Uncategorized")] = by_category.get(tx.get("category", "Uncategorized"), 0.0) + abs(tx[amount_key])

        def _table(items):
            return pd.DataFrame(
                sorted(items, key=lambda kv: -kv[1]), columns=["", "Amount ($)"]
            ).assign(**{"Amount ($)": lambda d: d["Amount ($)"].map(lambda v: f"${v:,.2f}")})

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**By source file:**")
            st.dataframe(_table(by_file.items()), use_container_width=True, hide_index=True)
        with col_b:
            st.markdown("**By category:**")
            st.dataframe(_table(by_category.items()), use_container_width=True, hide_index=True)

        st.markdown(f"**Largest individual {noun} (of {len(txs)} total):**")
        top = sorted(txs, key=lambda tx: -abs(tx[amount_key]))[:15]
        st.dataframe(
            pd.DataFrame(top)[["date", "payee", "amount", "category", "source_file"]],
            use_container_width=True, hide_index=True,
        )

    gross_receipt_txs = [
        tx for tx in all_transactions
        if tx.get("is_deposit") and not tx.get("category", "").startswith("Non-P&L:")
    ]
    expense_txs = [
        tx for tx in all_transactions
        if not tx.get("is_deposit") and not tx.get("category", "").startswith("Non-P&L:")
    ]

    with st.expander("How is Gross Receipts calculated?"):
        st.caption(
            "**Gross Receipts = every deposit across all uploaded statements, "
            "except anything categorized Non-P&L** (internal transfers, credit "
            "card payments, loan proceeds, owner contributions, tax refunds, "
            "returned deposits, vendor purchase credits, and any transaction a "
            "client's answer confirmed as personal). Excluded amounts don't "
            "count here even if they're the largest deposits on a statement."
        )
        render_transaction_breakdown(gross_receipt_txs, "deposits")

    with st.expander("How is Total Expenses calculated?"):
        st.caption(
            "**Total Expenses = every non-deposit transaction across all "
            "uploaded statements, except anything categorized Non-P&L** "
            "(same exclusions as Gross Receipts, above). One adjustment is "
            "applied after that: a **Line 24b (Meals)** transaction is "
            "counted at **50% of its actual amount**, per the IRS 50% meals "
            "deduction limit -- the amounts below are the actual transaction "
            "amounts before that 50% cut, so they won't match Total Expenses "
            "exactly for a client with meal expenses; the Schedule C Summary "
            "tab shows the post-50%-cut figure per line."
        )
        render_transaction_breakdown(expense_txs, "expenses")

    with st.expander("How is Net Profit / (Loss) calculated?"):
        st.caption(
            f"**Net Profit = Gross Receipts − Total Expenses.** "
            f"${gross_receipts:,.2f} − ${total_expenses:,.2f} = **${net_profit:,.2f}**. "
            "No other adjustment is applied -- see the two expanders above for "
            "what makes up each of those two figures."
        )

    # The 3 tabs that matter every single run. Everything else (line-item
    # search, per-statement reconciliation detail, the residual exception
    # lists that duplicate what Client Questions already makes actionable)
    # is real but not needed on every run -- tucked behind the audit
    # expander below instead of competing for attention up here.
    #
    # "Supporting Documents" only appears when there's actually something in
    # it (a check copy or invoice/receipt was uploaded this run) -- staying
    # true to the same declutter principle as the audit expander: nothing
    # competes for attention unless it's relevant to this client.
    has_supporting_docs = bool(check_records or unmatched_check_records or supporting_documents)
    problem_count = reconciliation_summary.get("discrepancy_count", 0)
    fix_label = f"Fix Statements ({problem_count} to fix)" if problem_count else "Fix Statements"
    tab_labels = [fix_label, "Client Inquiry Questions", "Schedule C Summary", "Non-P&L Transfers"]
    if has_supporting_docs:
        tab_labels.append("Supporting Documents")
    tabs = st.tabs(tab_labels)
    tab_fix, tab_questions, tab_summary, tab_nonpnl = tabs[0], tabs[1], tabs[2], tabs[3]
    tab_supporting = tabs[4] if has_supporting_docs else None

    with tab_fix:
        fix_statements_ui.render(client_state, active_client_id, save_client)

    with tab_questions:
        st.caption(
            "Send the questions below to the client however you normally do (this tool never "
            "sends anything itself). Once you have their answers, enter them in the **Answer** "
            "column for each item and click **Apply Client Answers & Recalculate Workpaper** — "
            "the dashboard, exception queues, and Excel export all update immediately, and an "
            "answered item does not come back as an open question on the next run."
        )

        editors: Dict[str, pd.DataFrame] = {}

        def _apply_answers_and_rerun(answered_questions: List[Dict[str, Any]], empty_message: str):
            """Shared by the per-category bulk-apply buttons and the main
            Apply button below -- both just build `answered_questions`
            differently (a bulk button assigns the same answer to every
            checked row; the main button reads whatever's in the Answer
            column of every editor) and hand off to the same apply/rerun
            logic, so there's only one place that talks to
            apply_client_answers and session_state."""
            _, new_log_entries = apply_client_answers(all_transactions, answered_questions)
            applied_count = sum(1 for e in new_log_entries if e["applied"])
            if applied_count:
                client_state["answered_questions"] = client_state["answered_questions"] + [
                    q for q in answered_questions if (q.get("answer") or "").strip()]

            # st.rerun() immediately abandons the rest of this script run, so
            # a message shown right before it would never actually be visible
            # -- only rerun when something changed and there's a fresh
            # dashboard worth seeing. When nothing changed, this message is
            # what's left on screen instead of flashing and disappearing.
            if applied_count:
                save_client()
                st.rerun()
            else:
                st.info(empty_message)

        def render_question_editor(section_title: str, question_category: str, answer_options: List[str]):
            subset = [q for q in questions if q["category"] == question_category]
            st.markdown(f"**{section_title} ({len(subset)} open)**")
            if not subset:
                st.caption("Nothing open in this category.")
                return
            df = pd.DataFrame(subset)[["item_id", "date", "payee", "count", "amount", "question", "client_response", "answer"]]
            # A leading checkbox column, not part of the underlying question
            # data -- purely a way to mark several rows at once for the bulk
            # answer control below (e.g. "these are all personal transfers").
            df.insert(0, "select", False)
            edited = st.data_editor(
                df,
                column_config={
                    "select": st.column_config.CheckboxColumn("", width="small", help="Check to include in the bulk answer below"),
                    "item_id": st.column_config.TextColumn("ID", disabled=True, width="small"),
                    "date": st.column_config.TextColumn("Date(s)", disabled=True, width="small"),
                    "payee": st.column_config.TextColumn("Payee", disabled=True),
                    "count": st.column_config.NumberColumn("# Txns", disabled=True, width="small"),
                    "amount": st.column_config.TextColumn("Total", disabled=True, width="small"),
                    "question": st.column_config.TextColumn("Question", disabled=True, width="large"),
                    "client_response": st.column_config.TextColumn("Notes from client (free text)"),
                    # The grid gives no persistent visual cue that a column is
                    # a dropdown (the caret only appears once a cell is opened
                    # for editing) -- there's no separate icon/indicator param
                    # on SelectboxColumn, so the affordance goes in the label
                    # text itself, the one part of the header that is real
                    # rendered content rather than styling.
                    "answer": st.column_config.SelectboxColumn("Answer ▾", options=answer_options, required=False, width="medium"),
                },
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                key=f"editor_{question_category}",
            )
            editors[question_category] = edited

            # Bulk answer: apply one answer to every checked row immediately,
            # rather than requiring the same value be picked by hand in each
            # row -- raised directly in a client meeting ("if Lindsay sees
            # these are all personal transfers, she can just bulk select and
            # change"). Deliberately its own immediate apply (not just a way
            # to pre-fill the Answer column) so it can reuse the exact same
            # apply/rerun path as the main button below, instead of trying to
            # programmatically rewrite the data_editor's own edit state --
            # Streamlit's data editor state is intentionally read-only.
            selected_ids = set(edited.loc[edited["select"] == True, "item_id"])  # noqa: E712
            bulk_col, button_col = st.columns([3, 1])
            with bulk_col:
                bulk_value = st.selectbox(
                    "Bulk answer for checked rows", options=[""] + answer_options,
                    key=f"bulk_value_{question_category}", label_visibility="collapsed",
                )
            with button_col:
                bulk_clicked = st.button(
                    f"Apply to {len(selected_ids)} checked" if selected_ids else "Apply to checked rows",
                    key=f"bulk_apply_{question_category}",
                    disabled=not (selected_ids and bulk_value),
                )
            if bulk_clicked:
                answered = []
                for q in subset:
                    if q["item_id"] not in selected_ids:
                        continue
                    q = dict(q)
                    q["answer"] = bulk_value
                    q["client_response"] = q.get("client_response", "")
                    answered.append(q)
                _apply_answers_and_rerun(
                    answered,
                    "No rows were checked -- check a row's box, then pick a bulk answer, before applying.",
                )

        render_question_editor("Expense Verification", "Expense Verification", ANSWER_OPTIONS["Expense Verification"])
        render_question_editor("Asset Purchase", "Asset Purchase", ANSWER_OPTIONS["Asset Purchase"])
        render_question_editor("Uncategorized Expense", "Uncategorized Expense", uncategorized_expense_answer_options())
        render_question_editor("Form 1099 Verification", "Form 1099 Verification", ANSWER_OPTIONS["Form 1099 Verification"])

        st.markdown("")
        if st.button("Apply Client Answers & Recalculate Workpaper", type="primary"):
            # Merge the edited Answer / Notes columns back into the full
            # question objects (which still carry transaction_keys/contractor,
            # stripped out of the editor view above to keep it readable).
            answered_questions = []
            for q in questions:
                edited_df = editors.get(q["category"])
                if edited_df is None:
                    continue
                match = edited_df[edited_df["item_id"] == q["item_id"]]
                if match.empty:
                    continue
                row = match.iloc[0]
                q = dict(q)
                q["answer"] = row.get("answer", "") or ""
                q["client_response"] = row.get("client_response", "") or ""
                answered_questions.append(q)

            _apply_answers_and_rerun(
                answered_questions,
                "No new answers to apply — fill in the Answer column above first.",
            )

        if correction_log:
            st.markdown("---")
            st.markdown(f"**Applied Client Answers This Session ({len(correction_log)}):**")
            df_log = pd.DataFrame(correction_log)
            st.dataframe(
                df_log[['item_id', 'question_category', 'payee', 'client_answer', 'old_category', 'new_category', 'applied', 'note']],
                use_container_width=True, height=300
            )

    with tab_summary:
        st.markdown("#### Schedule C Line Item Breakdown")
        st.caption(
            "Each line's amount is the sum of every transaction the categorizer "
            "placed in that IRS line, with Non-P&L exclusions already removed. "
            "Pick a line below to see exactly which transactions make it up --"
            " nothing here is summarized or described independently of the "
            "actual transaction list."
        )
        summary_rows = []
        summary_rows.append({"Line": "Line 1", "IRS Category": "Gross Receipts / Sales", "Amount ($)": f"${gross_receipts:,.2f}", "Status": "Cleaned of Transfers"})

        for cat in sorted(expense_totals.keys()):
            line_num = cat.split(":")[0] if ":" in cat else "Line 27a"
            desc = cat.split(":")[1].strip() if ":" in cat else cat
            amt = expense_totals[cat]
            note = "50% Meal Limit Applied" if "24b" in line_num else "Verified"
            summary_rows.append({"Line": line_num, "IRS Category": desc, "Amount ($)": f"${amt:,.2f}", "Status": note})

        summary_rows.append({"Line": "Line 28", "IRS Category": "TOTAL EXPENSES", "Amount ($)": f"${total_expenses:,.2f}", "Status": "Subtotal"})
        summary_rows.append({"Line": "Line 31", "IRS Category": "NET PROFIT / (LOSS)", "Amount ($)": f"${net_profit:,.2f}", "Status": "Final Schedule C Net"})

        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

        line_options = ["Line 1: Gross Receipts / Sales"] + sorted(expense_totals.keys())
        selected_line = st.selectbox("Inspect a line item's transactions", options=line_options, key="summary_line_inspect")
        if selected_line == line_options[0]:
            inspect_txs, inspect_note = gross_receipt_txs, None
        else:
            inspect_txs = [tx for tx in expense_txs if tx.get("category") == selected_line]
            inspect_note = (
                f"These {len(inspect_txs)} transaction(s) sum to "
                f"${sum(abs(t['amount']) for t in inspect_txs):,.2f} in full -- the Line "
                f"24b figure above is 50% of that, per the IRS meals deduction limit."
                if "24b" in selected_line else None
            )
        if inspect_note:
            st.caption(inspect_note)
        if inspect_txs:
            st.dataframe(
                pd.DataFrame(inspect_txs)[["date", "payee", "amount", "source_file"]],
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("No transactions in this line.")

    with tab_nonpnl:
        st.markdown("#### Excluded Non-P&L Transfers & Credit Card Payments")
        st.caption(
            "Every transaction here was removed from Gross Receipts/Total Expenses "
            "before those totals were computed above -- its **Category** column is "
            "the reason it doesn't count: an internal transfer between the client's "
            "own accounts, a credit card payment, loan proceeds/repayment, an owner "
            "draw/contribution, a tax refund, a returned deposit, a vendor purchase "
            "credit, or a transaction a client's answer confirmed as personal."
        )
        if exceptions['non_pnl_transfers']:
            st.dataframe(pd.DataFrame(exceptions['non_pnl_transfers'])[['date', 'payee', 'amount', 'category', 'source_file']], use_container_width=True, height=450)
        else:
            st.caption("Nothing excluded from this run.")

    if tab_supporting is not None:
        with tab_supporting:
            st.markdown("#### Check Copies, Invoices & Receipts")
            st.caption(
                "These were uploaded alongside the bank/credit-card statements but aren't "
                "statements themselves -- a standalone check-copy report, an invoice, or a "
                "receipt. None of this counts toward Gross Receipts or Total Expenses; a check "
                "copy's only job is to supply the payee name for its matching \"Check #...\" line "
                "item in the Schedule C Summary above, which a bank statement's own cleared-"
                "checks listing never prints."
            )

            if check_records or unmatched_check_records:
                st.markdown(f"**Check copies matched to a statement line item ({len(check_records) - len(unmatched_check_records)} of {len(check_records)}):**")
                if check_records:
                    st.dataframe(
                        pd.DataFrame(check_records)[
                            [c for c in ["check_number", "payee", "drawer", "numerical_amount", "issue_date", "source_file"]
                             if c in pd.DataFrame(check_records).columns]
                        ],
                        use_container_width=True, hide_index=True,
                    )
                if unmatched_check_records:
                    st.warning(
                        f"**{len(unmatched_check_records)} check copy(ies) couldn't be matched** to a "
                        "line item in any uploaded statement (no check with that number/amount was "
                        "found) -- the payee is known, but it isn't reflected anywhere in the "
                        "workpaper yet. Verify by hand."
                    )
                    st.dataframe(
                        pd.DataFrame(unmatched_check_records)[
                            [c for c in ["check_number", "payee", "numerical_amount", "issue_date", "source_file"]
                             if c in pd.DataFrame(unmatched_check_records).columns]
                        ],
                        use_container_width=True, hide_index=True,
                    )

            if supporting_documents:
                st.markdown(f"**Invoices & receipts ({len(supporting_documents)}):**")
                st.caption(
                    "Shown for reference only -- not matched against transactions automatically. "
                    "Use these to verify an expense's business purpose or amount by hand."
                )
                st.dataframe(
                    pd.DataFrame(supporting_documents)[
                        [c for c in ["type", "issuer", "document_number", "date", "total", "source_file"]
                         if c in pd.DataFrame(supporting_documents).columns]
                    ],
                    use_container_width=True, hide_index=True,
                )

    # ----------------------------------------------------
    # AUDIT DETAIL -- everything true, just not needed every run. Collapsed
    # by default so it never competes with the 3 tabs above for attention.
    # ----------------------------------------------------
    with st.expander("View audit detail (line items, per-file reconciliation, large transactions)"):
        audit_tab1, audit_tab2, audit_tab3, audit_tab4 = st.tabs([
            "All Line Items", "Reconciliation QC", "Unusual / Large Transactions", "Processing Log"
        ])

        with audit_tab1:
            if all_transactions:
                df_tx = pd.DataFrame(all_transactions)
                fc1, fc2, fc3 = st.columns([2, 2, 2])
                with fc1:
                    search_query = st.text_input("Search Payee / Description", key="tx_search")
                with fc2:
                    cat_filter = st.selectbox("Filter by Category", ["All Categories"] + sorted(list(df_tx['category'].unique())), key="cat_filter")
                with fc3:
                    conf_filter = st.selectbox("Filter by Confidence", ["All Confidence States"] + sorted(list(df_tx['confidence_state'].unique())), key="conf_filter")

                filtered_df = df_tx.copy()
                if search_query:
                    filtered_df = filtered_df[filtered_df['payee'].str.contains(search_query, case=False, na=False) | filtered_df['description'].str.contains(search_query, case=False, na=False)]
                if cat_filter != "All Categories":
                    filtered_df = filtered_df[filtered_df['category'] == cat_filter]
                if conf_filter != "All Confidence States":
                    filtered_df = filtered_df[filtered_df['confidence_state'] == conf_filter]

                st.caption(f"Showing **{len(filtered_df):,}** of **{len(df_tx):,}** total line items:")
                st.dataframe(
                    filtered_df[['date', 'payee', 'amount', 'is_deposit', 'category', 'confidence_state', 'original_category', 'source_file']],
                    use_container_width=True,
                    height=550
                )

        with audit_tab2:
            st.caption(
                "Each statement is checked twice: that its own balances foot, and that "
                "the transactions extracted from it add up to the totals it declares. "
                "A document with no declared balances is reported as Not Reconcilable "
                "— never as a pass."
            )
            if reconciliation_results:
                df_recon = pd.DataFrame(reconciliation_results)
                st.dataframe(
                    df_recon[[
                        'source_file', 'status', 'transaction_count',
                        'declared_deposits', 'extracted_deposits',
                        'declared_withdrawals', 'extracted_withdrawals', 'notes'
                    ]],
                    use_container_width=True
                )
            else:
                st.info("No PDF statements were uploaded, so there is nothing to reconcile.")

        with audit_tab3:
            large_txs = exceptions.get('unusual_large_txs', [])
            st.write(f"**Unusual / Large Transactions (≥ $5,000) ({len(large_txs)}):**")
            if large_txs:
                st.dataframe(pd.DataFrame(large_txs)[['date', 'payee', 'amount', 'reason']], use_container_width=True)
            else:
                st.caption("None this run.")

        with audit_tab4:
            st.caption(
                "Exactly what each uploaded file was parsed as and why -- including "
                "whether AI document processing ran, and the real error text if it "
                "failed. This was previously computed but never shown anywhere."
            )
            if diagnostics_log:
                for entry in diagnostics_log:
                    details = entry.get("details") or []
                    flagged = any(
                        kw in str(line).lower()
                        for line in details
                        for kw in ("failed", "not available", "could not", "review by hand", "did not pass")
                    )
                    with st.expander(
                        f"{'⚠️ ' if flagged else ''}{entry['file']} — {entry['type']} "
                        f"({entry['count']} item(s))",
                        expanded=flagged,
                    ):
                        for line in details:
                            st.write(f"- {line}")
            else:
                st.info("No files processed yet this run.")

    # ----------------------------------------------------
    # EXPORT SECTION
    # ----------------------------------------------------
    st.markdown("---")
    st.subheader("3. Export Workpaper")

    exporter = ExcelWorkpaperExporter()
    excel_buffer = exporter.generate_workpaper(
        client_name=client_name,
        tax_year=int(tax_year),
        transactions=all_transactions,
        exceptions_dict=exceptions,
        questions=questions,
        coverage_info=coverage_info,
        duplicates_info=duplicates_flagged,
        reconciliation_summary=reconciliation_summary,
        correction_log=correction_log,
        manual_edits=client_state["manual_edits"],
    )

    st.download_button(
        label="Download Audit-Ready Excel Workpaper (.xlsx)",
        data=excel_buffer,
        file_name=f"Schedule_C_Workpaper_{client_name.replace(' ', '_')}_{tax_year}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

elif not reading_status["files_waiting"] and not uploaded_files:
    st.info("Upload this client's statements and other documents above, then click **Read**.")

st.sidebar.markdown("---")
st.sidebar.caption(f"App version: {APP_VERSION} (commit {_running_commit()})")
