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
from core.excel_exporter import ExcelWorkpaperExporter

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
    target_cat = st.selectbox("IRS Schedule C Category", list(SCHEDULE_C_CATEGORIES.keys()) + ["Non-P&L: Internal Transfer", "Non-P&L: Credit Card Payment", "Non-P&L: Owner Draw / Contribution"])
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
    
    # 1. Filename Deduplication Check
    dedup = FilenameDeduplicator()
    unique_files, duplicates_flagged = dedup.process_files(uploaded_files)

    if duplicates_flagged:
        for dup in duplicates_flagged:
            st.warning(f"⚠️ **Duplicate File Skipped**: `{dup['filename']}` — {dup['reason']}")

    st.success(f"✅ Processing **{len(unique_files)} unique file(s)**.")

    # 2. Parsing Documents
    pdf_parser = BankPDFParser(categorizer=categorizer)
    sheet_parser = SpreadsheetParser(categorizer=categorizer)
    totals_parser = TotalsParser()

    all_transactions: List[Dict[str, Any]] = []
    months_found: List[int] = []
    diagnostics_log = []

    for file_obj in unique_files:
        filename = file_obj.name
        
        if filename.lower().endswith('.pdf'):
            res = pdf_parser.parse_pdf(file_obj, filename)
            all_transactions.extend(res['transactions'])
            months_found.extend(res['months_found'])
            diagnostics_log.append({"file": filename, "type": "PDF", "count": len(res['transactions']), "details": res.get('diagnostics')})

        elif filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            res = sheet_parser.parse_spreadsheet(file_obj, filename)
            all_transactions.extend(res['transactions'])
            diagnostics_log.append({"file": filename, "type": "Spreadsheet", "count": len(res['transactions']), "details": res.get('diagnostics')})

    # Check 12-Month Coverage
    coverage_info = pdf_parser.check_12_month_coverage(months_found)
    
    if coverage_info['is_complete']:
        st.markdown(f'<div class="success-box"><b>12-Month Coverage Verified:</b> All 12 months present in bank statements.</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="alert-box"><b>12-Month Coverage Warning:</b> {coverage_info["status_message"]}</div>', unsafe_allow_html=True)

    # 3. Analyze Exceptions & QC
    analyzer = ExceptionAnalyzer()
    exceptions = analyzer.analyze_exceptions(all_transactions)

    # 4. Generate Client Questions
    q_gen = ClientQuestionGenerator()
    questions = q_gen.generate_question_list(exceptions, client_name=client_name)

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

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Gross Receipts (Line 1)", f"${gross_receipts:,.2f}")
    with col2:
        st.metric("Total Expenses (Line 28)", f"${total_expenses:,.2f}")
    with col3:
        st.metric("Net Profit / (Loss) (Line 31)", f"${net_profit:,.2f}")
    with col4:
        st.metric("Items Flagged for Review", exceptions['total_exception_count'], delta="Exceptions Queue", delta_color="inverse")

    # Tabs for Data Inspection & Interactive Navigation
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📊 Schedule C Summary",
        "📄 All Line Items",
        "🏬 Grouped by Vendor",
        "🚨 Exceptions Queue",
        "❓ Client Inquiry Questions",
        "🛑 Non-P&L Transfers",
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
        if questions:
            df_q = pd.DataFrame(questions)
            st.dataframe(df_q[['item_id', 'category', 'date', 'payee', 'amount', 'question']], use_container_width=True, height=450)

    with tab6:
        st.markdown("#### Excluded Non-P&L Transfers & Credit Card Payments")
        if exceptions['non_pnl_transfers']:
            st.dataframe(pd.DataFrame(exceptions['non_pnl_transfers'])[['date', 'payee', 'amount', 'category', 'source_file']], use_container_width=True, height=450)

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
        duplicates_info=duplicates_flagged
    )

    st.download_button(
        label="📥 Download Audit-Ready Excel Workpaper (.xlsx)",
        data=excel_buffer,
        file_name=f"Schedule_C_Workpaper_{client_name.replace(' ', '_')}_{tax_year}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

else:
    st.warning("👈 Please upload bank statement PDFs or spreadsheets to begin processing.")
