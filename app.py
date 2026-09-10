import streamlit as st
import pandas as pd
import io
import os
from typing import List, Dict, Any

from core.deduplication import FilenameDeduplicator
from core.pdf_parser import BankPDFParser
from core.spreadsheet_parser import SpreadsheetParser
from core.totals_parser import TotalsParser
from core.tax_categorizer import TaxCategorizer, SCHEDULE_C_CATEGORIES
from core.exception_analyzer import ExceptionAnalyzer
from core.question_generator import ClientQuestionGenerator
from core.reconciliation import ReconciliationChecker
from core.excel_exporter import ExcelWorkpaperExporter
from core.answer_applier import apply_client_answers, ANSWER_OPTIONS, uncategorized_expense_answer_options

# Bumped on every meaningful change to this file, so whoever is looking at the
# app can tell which version is running just by glancing at the sidebar --
# there is no separate deploy/build pipeline that would otherwise show that.
APP_VERSION = "v1"

# Page Configuration
st.set_page_config(
    page_title="Schedule C Intake & Tax Prep Tool",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
    <style>
    .main-header { font-size: 2.2rem; color: #1F4E78; font-weight: 700; margin-bottom: 0.2rem; }
    .sub-header { font-size: 1.1rem; color: #595959; margin-bottom: 1.5rem; }
    .alert-box { background-color: #FCE4D6; padding: 1rem; border-radius: 8px; border-left: 4px solid #C00000; }
    .success-box { background-color: #E2EFDA; padding: 1rem; border-radius: 8px; border-left: 4px solid #385723; }
    </style>
""", unsafe_allow_html=True)

# Title & Description
st.markdown('<div class="main-header">Schedule C Intake & Tax Prep Workpaper Tool</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Turns bank/credit card statements into a client question list and an audit-ready Schedule C workpaper.</div>', unsafe_allow_html=True)

# Sidebar Configuration
st.sidebar.header("📋 Client & Tax Year Metadata")
client_name = st.sidebar.text_input("Client Name / Business Name", value="Acme Consulting LLC")
tax_year = st.sidebar.number_input("Tax Year", value=2026, step=1)

st.sidebar.markdown("---")
st.sidebar.header("⚙️ De Minimis & Question Rules")
de_minimis_threshold = st.sidebar.number_input("Fixed Asset Threshold ($)", value=2500.0, step=100.0)
materiality_threshold = st.sidebar.number_input(
    "Client Question Materiality Threshold ($)", value=0.0, step=1.0,
    help="A vendor group whose total is at or below this amount gets no client "
         "question at all -- no one needs to be asked about a single $3 charge. "
         "Applies to the group's total, not each individual charge."
)

# Custom payee rules a prior session may have saved (core/tax_categorizer.py's
# TaxCategorizer loads custom_rules.json automatically) still apply here even
# with no in-app editor for them right now.
categorizer = TaxCategorizer()

# Main Processing Workflow
st.subheader("1. Upload Client Documents")
st.caption("If a file with an identical name was already processed, it's flagged as a duplicate and skipped so nothing is counted twice.")

uploaded_files = st.file_uploader(
    "Upload Bank PDFs, Spreadsheets (.xlsx/.csv), or Summary Totals",
    accept_multiple_files=True,
    type=["pdf", "xlsx", "xls", "csv", "json"]
)

if uploaded_files:
    # The app is single-session (see README: "one client = one session"), but
    # a client's answers now need to survive a Streamlit rerun -- every
    # widget interaction reruns this whole script top to bottom. Re-parsing
    # every file on every rerun would silently discard any correction applied
    # a moment earlier, so parsing only happens once per distinct set of
    # uploaded files; re-running with the same files reuses the (possibly
    # corrected) working transaction list already in session_state.
    upload_signature = tuple(sorted((f.name, f.size) for f in uploaded_files))
    needs_reparse = st.session_state.get("upload_signature") != upload_signature

    # A visible, step-by-step account of what the app is doing, so a 34-file
    # upload doesn't look like a frozen page -- collapses to a one-line
    # summary once done, but stays on screen (unlike a spinner, which
    # vanishes) so there's a record of what just happened.
    with st.status("Procesando documentos…", expanded=True) as status:
        if needs_reparse:
            dedup = FilenameDeduplicator()
            unique_files, duplicates_flagged = dedup.process_files(uploaded_files)

            pdf_parser = BankPDFParser(categorizer=categorizer)
            sheet_parser = SpreadsheetParser(categorizer=categorizer)
            totals_parser = TotalsParser()

            parsed_transactions: List[Dict[str, Any]] = []
            months_found: List[int] = []
            diagnostics_log = []
            reconciler = ReconciliationChecker()
            reconciliation_results: List[Dict[str, Any]] = []

            st.write(f"📄 Extrayendo y categorizando {len(unique_files)} archivo(s)…")
            progress_bar = st.progress(0.0)
            for i, file_obj in enumerate(unique_files):
                filename = file_obj.name
                progress_bar.progress(
                    i / len(unique_files),
                    text=f"Procesando {i + 1} de {len(unique_files)} — {filename}"
                )

                if filename.lower().endswith('.pdf'):
                    res = pdf_parser.parse_pdf(file_obj, filename)
                    parsed_transactions.extend(res['transactions'])
                    months_found.extend(res['months_found'])
                    reconciliation_results.append(reconciler.check_document(
                        filename, res['statement_summary'], res['transactions']
                    ))
                    diagnostics_log.append({"file": filename, "type": "PDF", "count": len(res['transactions']), "details": res.get('diagnostics')})

                elif filename.lower().endswith(('.xlsx', '.xls', '.csv')):
                    res = sheet_parser.parse_spreadsheet(file_obj, filename)
                    parsed_transactions.extend(res['transactions'])
                    diagnostics_log.append({"file": filename, "type": "Spreadsheet", "count": len(res['transactions']), "details": res.get('diagnostics')})

            progress_bar.progress(1.0, text="Extracción completa")
            progress_bar.empty()
            st.write(f"✅ {len(parsed_transactions):,} transacciones extraídas de {len(unique_files)} archivo(s)")

            coverage_info = pdf_parser.check_12_month_coverage(months_found)
            st.write(f"✅ Cobertura verificada — {coverage_info['status_message']}")

            reconciliation_summary = reconciler.summarize(reconciliation_results)
            st.write(f"✅ Reconciliación: {reconciliation_summary['status']}")

            # A genuinely new set of files starts a fresh client -- any prior
            # corrections belonged to the previous upload and don't carry over.
            st.session_state.upload_signature = upload_signature
            st.session_state.all_transactions = parsed_transactions
            st.session_state.correction_log = []
            st.session_state.diagnostics_log = diagnostics_log
            st.session_state.coverage_info = coverage_info
            st.session_state.reconciliation_summary = reconciliation_summary
            st.session_state.reconciliation_results = reconciliation_results
            st.session_state.duplicates_flagged = duplicates_flagged
            st.session_state.unique_file_count = len(unique_files)
        else:
            st.write(f"✅ Usando los {st.session_state.unique_file_count} archivo(s) ya procesados en esta sesión")

        # Every rerun (fresh parse or not) reads from session_state, so an
        # applied client answer is what the rest of the page actually sees.
        all_transactions: List[Dict[str, Any]] = st.session_state.all_transactions
        diagnostics_log = st.session_state.diagnostics_log
        coverage_info = st.session_state.coverage_info
        reconciliation_summary = st.session_state.reconciliation_summary
        reconciliation_results = st.session_state.reconciliation_results
        duplicates_flagged = st.session_state.duplicates_flagged
        correction_log = st.session_state.correction_log
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
        st.write(f"✅ {len(questions)} pregunta(s) para el cliente")

        status.update(
            label=f"Listo — {len(questions)} pregunta(s) para el cliente",
            state="complete", expanded=False
        )

    if duplicates_flagged:
        for dup in duplicates_flagged:
            st.warning(f"⚠️ **Duplicate File Skipped**: `{dup['filename']}` — {dup['reason']}")

    if not coverage_info['is_complete']:
        st.markdown(f'<div class="alert-box"><b>12-Month Coverage Warning:</b> {coverage_info["status_message"]}</div>', unsafe_allow_html=True)

    if recon_status == "Discrepancy":
        st.markdown(
            f'<div class="alert-box"><b>Reconciliation Discrepancy:</b> {reconciliation_summary["message"]}</div>',
            unsafe_allow_html=True)
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
        st.metric("Gross Receipts (Line 1)", f"${gross_receipts:,.2f}")
    with col2:
        st.metric("Total Expenses (Line 28)", f"${total_expenses:,.2f}")
    with col3:
        st.metric("Net Profit / (Loss) (Line 31)", f"${net_profit:,.2f}")
    with col4:
        st.metric("Client Questions", len(questions), delta=f"{exceptions['total_exception_count']} exceptions total", delta_color="off")

    # The 3 tabs that matter every single run. Everything else (line-item
    # search, per-statement reconciliation detail, the residual exception
    # lists that duplicate what Client Questions already makes actionable)
    # is real but not needed on every run -- tucked behind the audit
    # expander below instead of competing for attention up here.
    tab_questions, tab_summary, tab_nonpnl = st.tabs([
        "❓ Client Inquiry Questions",
        "📊 Schedule C Summary",
        "🛑 Non-P&L Transfers",
    ])

    with tab_questions:
        st.caption(
            "Send the questions below to the client however you normally do (this tool never "
            "sends anything itself). Once you have their answers, enter them in the **Answer** "
            "column for each item and click **Apply Client Answers & Recalculate Workpaper** — "
            "the dashboard, exception queues, and Excel export all update immediately, and an "
            "answered item does not come back as an open question on the next run."
        )

        editors: Dict[str, pd.DataFrame] = {}

        def render_question_editor(section_title: str, question_category: str, answer_options: List[str]):
            subset = [q for q in questions if q["category"] == question_category]
            st.markdown(f"**{section_title} ({len(subset)} open)**")
            if not subset:
                st.caption("Nothing open in this category.")
                return
            df = pd.DataFrame(subset)[["item_id", "date", "payee", "count", "amount", "question", "client_response", "answer"]]
            edited = st.data_editor(
                df,
                column_config={
                    "item_id": st.column_config.TextColumn("ID", disabled=True, width="small"),
                    "date": st.column_config.TextColumn("Date(s)", disabled=True, width="small"),
                    "payee": st.column_config.TextColumn("Payee", disabled=True),
                    "count": st.column_config.NumberColumn("# Txns", disabled=True, width="small"),
                    "amount": st.column_config.TextColumn("Total", disabled=True, width="small"),
                    "question": st.column_config.TextColumn("Question", disabled=True, width="large"),
                    "client_response": st.column_config.TextColumn("Notes from client (free text)"),
                    "answer": st.column_config.SelectboxColumn("Answer", options=answer_options, required=False, width="medium"),
                },
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                key=f"editor_{question_category}",
            )
            editors[question_category] = edited

        render_question_editor("Expense Verification", "Expense Verification", ANSWER_OPTIONS["Expense Verification"])
        render_question_editor("Asset Purchase", "Asset Purchase", ANSWER_OPTIONS["Asset Purchase"])
        render_question_editor("Uncategorized Expense", "Uncategorized Expense", uncategorized_expense_answer_options())
        render_question_editor("Form 1099 Verification", "Form 1099 Verification", ANSWER_OPTIONS["Form 1099 Verification"])

        st.markdown("")
        if st.button("✅ Apply Client Answers & Recalculate Workpaper", type="primary"):
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

            updated_transactions, new_log_entries = apply_client_answers(all_transactions, answered_questions)
            applied_count = sum(1 for e in new_log_entries if e["applied"])

            st.session_state.all_transactions = updated_transactions
            st.session_state.correction_log = st.session_state.correction_log + new_log_entries

            # st.rerun() immediately abandons the rest of this script run, so
            # a message shown right before it would never actually be visible
            # -- only rerun when something changed and there's a fresh
            # dashboard worth seeing. When nothing was entered, this message
            # is what's left on screen instead of flashing and disappearing.
            if applied_count:
                st.rerun()
            else:
                st.info("No new answers to apply — fill in the Answer column above first.")

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

    with tab_nonpnl:
        st.markdown("#### Excluded Non-P&L Transfers & Credit Card Payments")
        if exceptions['non_pnl_transfers']:
            st.dataframe(pd.DataFrame(exceptions['non_pnl_transfers'])[['date', 'payee', 'amount', 'category', 'source_file']], use_container_width=True, height=450)
        else:
            st.caption("Nothing excluded from this run.")

    # ----------------------------------------------------
    # AUDIT DETAIL -- everything true, just not needed every run. Collapsed
    # by default so it never competes with the 3 tabs above for attention.
    # ----------------------------------------------------
    with st.expander("🔍 Ver detalle de auditoría (line items, reconciliación por archivo, transacciones grandes)"):
        audit_tab1, audit_tab2, audit_tab3 = st.tabs([
            "All Line Items", "Reconciliation QC", "Unusual / Large Transactions"
        ])

        with audit_tab1:
            if all_transactions:
                df_tx = pd.DataFrame(all_transactions)
                fc1, fc2, fc3 = st.columns([2, 2, 2])
                with fc1:
                    search_query = st.text_input("🔍 Search Payee / Description", key="tx_search")
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
        correction_log=correction_log
    )

    st.download_button(
        label="📥 Download Audit-Ready Excel Workpaper (.xlsx)",
        data=excel_buffer,
        file_name=f"Schedule_C_Workpaper_{client_name.replace(' ', '_')}_{tax_year}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

else:
    st.warning("👈 Please upload bank statement PDFs or spreadsheets to begin processing.")

st.sidebar.markdown("---")
st.sidebar.caption(f"App version: {APP_VERSION}")
