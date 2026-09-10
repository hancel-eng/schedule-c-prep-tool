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

# Page Configuration
st.set_page_config(
    page_title="Schedule C Intake & Tax Prep Tool (Offline)",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
    <style>
    .main-header { font-size: 2.2rem; color: #1F4E78; font-weight: 700; margin-bottom: 0.2rem; }
    .sub-header { font-size: 1.1rem; color: #595959; margin-bottom: 1.5rem; }
    .offline-badge { background-color: #E2EFDA; color: #385723; font-weight: 600; padding: 0.4rem 0.8rem; border-radius: 6px; border: 1px solid #C5E0B4; display: inline-block; margin-bottom: 1rem; }
    .metric-box { background-color: #F2F4F8; padding: 1rem; border-radius: 8px; border-left: 4px solid #1F4E78; }
    .alert-box { background-color: #FCE4D6; padding: 1rem; border-radius: 8px; border-left: 4px solid #C00000; }
    .success-box { background-color: #E2EFDA; padding: 1rem; border-radius: 8px; border-left: 4px solid #385723; }
    </style>
""", unsafe_allow_html=True)

# Title & Description
st.markdown('<div class="main-header">Schedule C Intake & Tax Prep Workpaper Tool</div>', unsafe_allow_html=True)
st.markdown('<div class="offline-badge">🟢 100% Offline Local Engine (No Cloud AI Key Required)</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Automated extraction, strict filename deduplication, IRS tax mapping & audit-ready workpapers for Tax Savers.</div>', unsafe_allow_html=True)

# Sidebar Configuration
st.sidebar.header("📋 Client & Tax Year Metadata")
client_name = st.sidebar.text_input("Client Name / Business Name", value="Acme Consulting LLC")
tax_year = st.sidebar.number_input("Tax Year", value=2026, step=1)

st.sidebar.markdown("---")
st.sidebar.header("⚙️ De Minimis & Tax Rules")
de_minimis_threshold = st.sidebar.number_input("Fixed Asset Threshold ($)", value=2500.0, step=100.0)

# Local Payee Override Rule Manager
st.sidebar.markdown("---")
st.sidebar.header("🛠️ Local Payee Rules Manager")
st.sidebar.caption("Add local rules for specific client vendors (saves automatically to disk).")

categorizer = TaxCategorizer()

with st.sidebar.expander("➕ Add Custom Payee Rule"):
    new_payee = st.text_input("Vendor / Payee Keyword (e.g. 'Smith Hardware')", key="new_payee_kw")
    target_cat = st.selectbox("IRS Schedule C Category", list(SCHEDULE_C_CATEGORIES.keys()) + [
        "Non-P&L: Internal Transfer",
        "Non-P&L: Credit Card Payment",
        "Non-P&L: Owner Draw / Contribution",
        "Non-P&L: Loan Proceeds / Repayment",
        "Non-P&L: Tax Refund / Reimbursement",
        "Non-P&L: Returned/Reversed Deposit",
    ])
    if st.button("Save Local Rule"):
        if new_payee:
            categorizer.save_custom_rule(new_payee, target_cat)
            st.success(f"Saved local rule: '{new_payee}' -> {target_cat}")
            st.rerun()

if categorizer.custom_rules:
    st.sidebar.write(f"**Active Custom Rules ({len(categorizer.custom_rules)}):**")
    for kw, cat_val in list(categorizer.custom_rules.items())[:5]:
        st.sidebar.text(f"• '{kw}' -> {cat_val}")

# Main Processing Workflow
st.subheader("1. Upload Client Documents")
st.info("💡 **Filename Deduplication Active**: The app checks file names. If a file with an identical name has already been processed, it will be flagged as a duplicate. All unique files retain 100% of their line items.")

uploaded_files = st.file_uploader(
    "Upload Bank PDFs, Spreadsheets (.xlsx/.csv), or Summary Totals",
    accept_multiple_files=True,
    type=["pdf", "xlsx", "xls", "csv", "json"]
)

if uploaded_files:
    st.markdown("---")
    st.subheader("2. File Ingestion & Coverage Verification")

    # The app is single-session (see README: "one client = one session"), but
    # a client's answers now need to survive a Streamlit rerun -- every
    # widget interaction reruns this whole script top to bottom. Re-parsing
    # every file on every rerun would silently discard any correction applied
    # a moment earlier, so parsing only happens once per distinct set of
    # uploaded files; re-running with the same files reuses the (possibly
    # corrected) working transaction list already in session_state.
    upload_signature = tuple(sorted((f.name, f.size) for f in uploaded_files))
    needs_reparse = st.session_state.get("upload_signature") != upload_signature

    if needs_reparse:
        # 1. Filename Deduplication Check
        dedup = FilenameDeduplicator()
        unique_files, duplicates_flagged = dedup.process_files(uploaded_files)

        # 2. Parsing Documents
        pdf_parser = BankPDFParser(categorizer=categorizer)
        sheet_parser = SpreadsheetParser(categorizer=categorizer)
        totals_parser = TotalsParser()

        parsed_transactions: List[Dict[str, Any]] = []
        months_found: List[int] = []
        diagnostics_log = []
        reconciler = ReconciliationChecker()
        reconciliation_results: List[Dict[str, Any]] = []

        for file_obj in unique_files:
            filename = file_obj.name

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

        coverage_info = pdf_parser.check_12_month_coverage(months_found)
        reconciliation_summary = reconciler.summarize(reconciliation_results)

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

    # Every rerun (fresh parse or not) reads from session_state, so an
    # applied client answer is what the rest of the page actually sees.
    all_transactions: List[Dict[str, Any]] = st.session_state.all_transactions
    diagnostics_log = st.session_state.diagnostics_log
    coverage_info = st.session_state.coverage_info
    reconciliation_summary = st.session_state.reconciliation_summary
    reconciliation_results = st.session_state.reconciliation_results
    duplicates_flagged = st.session_state.duplicates_flagged
    correction_log = st.session_state.correction_log

    if duplicates_flagged:
        for dup in duplicates_flagged:
            st.warning(f"⚠️ **Duplicate File Skipped**: `{dup['filename']}` — {dup['reason']}")

    if needs_reparse:
        st.success(f"✅ Processing **{st.session_state.unique_file_count} unique file(s)**.")
    else:
        st.success(
            f"✅ Using the **{st.session_state.unique_file_count} unique file(s)** already processed "
            f"this session ({len(correction_log)} client answer(s) applied). Upload a different set "
            f"of files to start a new client."
        )

    if coverage_info['is_complete']:
        st.markdown(f'<div class="success-box"><b>12-Month Coverage Verified:</b> All 12 months present in bank statements.</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="alert-box"><b>12-Month Coverage Warning:</b> {coverage_info["status_message"]}</div>', unsafe_allow_html=True)

    # Reconciliation QC: do the extracted transactions agree with what the
    # statements themselves declare?
    recon_status = reconciliation_summary['status']

    if recon_status == "Reconciled":
        st.markdown(
            f'<div class="success-box"><b>Reconciliation:</b> {reconciliation_summary["message"]}</div>',
            unsafe_allow_html=True)
    elif recon_status == "Discrepancy":
        st.markdown(
            f'<div class="alert-box"><b>Reconciliation Discrepancy:</b> {reconciliation_summary["message"]}</div>',
            unsafe_allow_html=True)
    else:
        st.warning(f"**Reconciliation — {recon_status}:** {reconciliation_summary['message']}")

    # 3. Analyze Exceptions & QC -- recomputed fresh every run, straight from
    # the current (possibly client-corrected) transaction list, so an applied
    # answer's effect on the exception queues shows up immediately.
    analyzer = ExceptionAnalyzer()
    exceptions = analyzer.analyze_exceptions(
        all_transactions, de_minimis_threshold=de_minimis_threshold
    )

    # 4. Generate Client Questions, then drop any 1099 question already
    # answered in a prior round -- a 1099 answer is a compliance note, not a
    # transaction recategorization (see core/answer_applier.py), so nothing
    # about the underlying transaction changes to naturally exclude it the
    # way an answered personal-expense or uncategorized question does.
    q_gen = ClientQuestionGenerator()
    questions = q_gen.generate_question_list(exceptions, client_name=client_name)
    resolved_contractors = {
        entry["payee"] for entry in correction_log
        if entry.get("question_category") == "Form 1099 Verification" and entry.get("applied")
    }
    questions = [
        q for q in questions
        if not (q["category"] == "Form 1099 Verification" and q.get("contractor") in resolved_contractors)
    ]

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
    st.subheader("3. Preparer Dashboard & Financial Summary")

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Gross Receipts (Line 1)", f"${gross_receipts:,.2f}")
    with col2:
        st.metric("Total Expenses (Line 28)", f"${total_expenses:,.2f}")
    with col3:
        st.metric("Net Profit / (Loss) (Line 31)", f"${net_profit:,.2f}")
    with col4:
        st.metric("Items Flagged for Review", exceptions['total_exception_count'], delta="Exceptions Queue", delta_color="inverse")
    with col5:
        st.metric("Reconciliation", recon_status, delta=f"{reconciliation_summary['reconciled_count']}/{reconciliation_summary['total_documents']} docs", delta_color="off")

    # Tabs for Data Inspection & Interactive Navigation
    tab1, tab2, tab3, tab4, tab5, tab6, tab8, tab7 = st.tabs([
        "📊 Schedule C Summary",
        "📄 All Line Items",
        "🏬 Grouped by Vendor",
        "🚨 Exceptions Queue",
        "❓ Client Inquiry Questions",
        "🛑 Non-P&L Transfers",
        "⚖️ Reconciliation QC",
        "🛠️ Debug & Diagnostics"
    ])

    with tab1:
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

    with tab2:
        st.markdown("#### Interactive Line-Item Navigation & Search")
        if all_transactions:
            df_tx = pd.DataFrame(all_transactions)

            # Interactive Search & Filtering Controls
            fc1, fc2, fc3 = st.columns([2, 2, 2])
            with fc1:
                search_query = st.text_input("🔍 Search Payee / Description", key="tx_search")
            with fc2:
                cat_filter = st.selectbox("Filter by Category", ["All Categories"] + sorted(list(df_tx['category'].unique())), key="cat_filter")
            with fc3:
                conf_filter = st.selectbox("Filter by Confidence", ["All Confidence States"] + sorted(list(df_tx['confidence_state'].unique())), key="conf_filter")

            # Apply Filters
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

    with tab3:
        st.markdown("#### Vendor / Payee Aggregation & Grouping")
        if all_transactions:
            df_tx = pd.DataFrame(all_transactions)
            vendor_summary = df_tx.groupby('payee').agg(
                Total_Amount=('amount', 'sum'),
                Abs_Total=('amount', lambda x: x.abs().sum()),
                Tx_Count=('amount', 'count'),
                Primary_Category=('category', lambda x: x.mode()[0] if not x.empty else 'Other')
            ).reset_index().sort_values(by='Abs_Total', ascending=False)

            st.dataframe(vendor_summary[['payee', 'Tx_Count', 'Total_Amount', 'Primary_Category']], use_container_width=True, height=500)

    with tab4:
        st.markdown("#### Exception Review Queues")
        st.write(f"**Potential Personal Expenses ({len(exceptions['potential_personal'])}):**")
        if exceptions['potential_personal']:
            st.dataframe(pd.DataFrame(exceptions['potential_personal'])[['date', 'payee', 'amount', 'reason']], use_container_width=True)

        st.write(f"**Potential Fixed Assets (> ${de_minimis_threshold:,.2f}) ({len(exceptions['potential_fixed_assets'])}):**")
        if exceptions['potential_fixed_assets']:
            st.dataframe(pd.DataFrame(exceptions['potential_fixed_assets'])[['date', 'payee', 'amount', 'reason']], use_container_width=True)

        st.write(f"**Contract Labor 1099 Review (≥ $600) ({len(exceptions['contract_labor_1099'])}):**")
        if exceptions['contract_labor_1099']:
            st.dataframe(pd.DataFrame(exceptions['contract_labor_1099']), use_container_width=True)

        st.write(f"**Unusual / Large Transactions (≥ $5,000) ({len(exceptions.get('unusual_large_txs', []))}):**")
        if exceptions.get('unusual_large_txs'):
            st.dataframe(pd.DataFrame(exceptions['unusual_large_txs'])[['date', 'payee', 'amount', 'reason']], use_container_width=True)

    with tab5:
        st.markdown("#### Auto-Generated Client Question Checklist")
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
            df = pd.DataFrame(subset)[["item_id", "date", "payee", "amount", "question", "client_response", "answer"]]
            edited = st.data_editor(
                df,
                column_config={
                    "item_id": st.column_config.TextColumn("ID", disabled=True, width="small"),
                    "date": st.column_config.TextColumn("Date", disabled=True, width="small"),
                    "payee": st.column_config.TextColumn("Payee", disabled=True),
                    "amount": st.column_config.TextColumn("Amount", disabled=True, width="small"),
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
            # question objects (which still carry transaction_key/contractor,
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

    with tab6:
        st.markdown("#### Excluded Non-P&L Transfers & Credit Card Payments")
        if exceptions['non_pnl_transfers']:
            st.dataframe(pd.DataFrame(exceptions['non_pnl_transfers'])[['date', 'payee', 'amount', 'category', 'source_file']], use_container_width=True, height=450)

    with tab8:
        st.markdown("#### Reconciliation Against Statement-Declared Totals")
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

    with tab7:
        st.markdown("#### File Ingestion & Parsing Diagnostics")
        st.json(diagnostics_log)
        if all_transactions:
            st.write("**Extracted Raw Sample (First 5 Rows):**")
            st.json(all_transactions[:5])

    # ----------------------------------------------------
    # EXPORT SECTION
    # ----------------------------------------------------
    st.markdown("---")
    st.subheader("4. Export Formatted Workpaper for UltraTax")
    
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
