"""The "Fix Statements" tab: correct by hand anything that doesn't add up,
without uploading anything again. All the logic lives in core/workpaper.py
and core/document_rules.py; this file only draws it."""
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from core import document_rules, workpaper

STATUS_ICONS = {"Reconciled": "✅", "Checked by Hand": "✋", "Discrepancy": "⚠️", "Not Reconcilable": "❔"}
STATUS_WORDS = {"Reconciled": "Adds up", "Checked by Hand": "Checked by hand",
                "Discrepancy": "Doesn't add up", "Not Reconcilable": "Nothing to check against"}


def _direction_labels(doc: Dict[str, Any]) -> Dict[str, str]:
    if doc["document_kind"] == "credit_card_statement":
        return {"credit": "Payment or credit", "debit": "Charge"}
    return {"credit": "Money in", "debit": "Money out"}


def _to_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _none_if_missing(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _rows_frame(doc: Dict[str, Any]) -> pd.DataFrame:
    labels = _direction_labels(doc)
    marks = document_rules.row_balance_marks(doc)
    records = []
    for row, mark in zip(doc.get("transactions") or [], marks):
        records.append({
            "row_id": row["row_id"],
            "date": _to_date(row.get("date")),
            "description": row.get("description") or "",
            "amount": row.get("amount"),
            "direction": labels[row.get("direction", "debit")],
            "running_balance": row.get("running_balance"),
            "should_be": mark["expected_balance"],
            "check": {True: "✓", False: "✗ here"}.get(mark["matches"], ""),
        })
    columns = ["row_id", "date", "description", "amount", "direction", "running_balance", "should_be", "check"]
    return pd.DataFrame(records, columns=columns)


def _totals_frame(doc: Dict[str, Any]) -> pd.DataFrame:
    labels = _direction_labels(doc)
    records = [{"label": t["label"], "amount": t["amount"], "effect": labels[t["effect"]]}
               for t in doc.get("summary_totals") or []]
    return pd.DataFrame(records, columns=["label", "amount", "effect"])


def _typed_rows(frame: pd.DataFrame, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    reverse = {v: k for k, v in _direction_labels(doc).items()}
    rows = []
    for record in frame.to_dict("records"):
        record = {k: _none_if_missing(v) for k, v in record.items()}
        rows.append({
            "row_id": record.get("row_id"),
            "date": record.get("date"),
            "description": record.get("description") or "",
            "amount": record.get("amount"),
            "direction": reverse.get(record.get("direction"), "debit"),
            "running_balance": record.get("running_balance"),
        })
    return rows


def _typed_totals(frame: pd.DataFrame, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    reverse = {v: k for k, v in _direction_labels(doc).items()}
    totals = []
    for record in frame.to_dict("records"):
        record = {k: _none_if_missing(v) for k, v in record.items()}
        totals.append({"label": record.get("label") or "", "amount": record.get("amount"),
                       "effect": reverse.get(record.get("effect"), "debit"), "item_count": None})
    return totals


def _show_result(result: Dict[str, Any], prefix: str = "") -> None:
    text = f"{prefix}{result['notes']}".replace("$", "\\$")
    if result["status"] == "Reconciled":
        st.success(text, icon="✅")
    elif result["status"] == "Checked by Hand":
        st.info(text, icon="✋")
    elif result["status"] == "Discrepancy":
        st.error(text, icon="⚠️")
    else:
        st.warning(text, icon="❔")


def render(client_state: Dict[str, Any], client_key: str) -> None:
    files = client_state["files"]
    edits = client_state["manual_edits"]
    statements = workpaper.statement_documents(files)

    st.caption(
        "Every statement is checked against the numbers printed on it. If one doesn't add up, "
        "open it below, compare it with the paper or PDF, and fix the numbers here. "
        "You'll see right away if your fix makes it add up. Nothing is saved until you click "
        "**Save my fixes**, and you can always go back to what the AI read."
    )
    if not statements:
        st.info("No statements to check yet.")
        return

    ordered = sorted(statements, key=lambda item: (
        document_rules.verify_statement(item[2])["status"] != "Discrepancy", item[0], item[1]))
    first_problem_open = False
    for filename, doc_index, doc in ordered:
        result = document_rules.verify_statement(doc)
        status = result["status"]
        expand = status == "Discrepancy" and not first_problem_open
        first_problem_open = first_problem_open or expand
        title = f"{STATUS_ICONS[status]} {STATUS_WORDS[status]} · {document_rules.document_label(doc)} · {filename}"
        with st.expander(title, expanded=expand):
            _render_one(files, edits, filename, doc_index, doc, result, client_key)

    if edits:
        st.markdown("**Every fix made by hand** (also in the Excel file, sheet *Manual Corrections*):")
        st.dataframe(pd.DataFrame(edits)[["when", "file", "document", "change", "before", "after"]],
                     width="stretch", hide_index=True)


def _render_one(files, edits, filename, doc_index, doc, result, client_key) -> None:
    _show_result(result)
    version = doc.get("edit_version", 0)
    key = f"fix_{client_key}_{filename}_{doc_index}_{version}"
    labels = list(_direction_labels(doc).values())

    st.markdown("**1. Numbers printed on the statement**")
    c1, c2 = st.columns(2)
    opening = c1.number_input("Starting balance", value=doc.get("opening_balance"), step=0.01,
                              format="%.2f", key=f"{key}_open")
    closing = c2.number_input("Ending balance", value=doc.get("closing_balance"), step=0.01,
                              format="%.2f", key=f"{key}_close")
    st.caption("Totals from the statement's summary box. Add a line with the + at the bottom, "
               "or delete one by selecting it and pressing the trash can.")
    totals = st.data_editor(
        _totals_frame(doc), key=f"{key}_totals", num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "label": st.column_config.TextColumn("What the line says", required=True),
            "amount": st.column_config.NumberColumn("Amount ($)", min_value=0.0, format="%.2f", required=True),
            "effect": st.column_config.SelectboxColumn("Type", options=labels, required=True),
        },
    )

    st.markdown("**2. Rows**")
    st.caption("“Should be” is the balance the rows so far add up to. A ✗ means it doesn't match the "
               "balance printed on that row, so the problem is on that row or just before it.")
    rows = st.data_editor(
        _rows_frame(doc), key=f"{key}_rows", num_rows="dynamic", hide_index=True, width="stretch",
        column_order=["date", "description", "amount", "direction", "running_balance", "should_be", "check"],
        disabled=["should_be", "check"],
        column_config={
            "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "description": st.column_config.TextColumn("Description", width="medium"),
            "amount": st.column_config.NumberColumn("Amount ($)", min_value=0.0, format="%.2f", required=True),
            "direction": st.column_config.SelectboxColumn("Type", options=labels, required=True),
            "running_balance": st.column_config.NumberColumn("Balance printed", format="%.2f"),
            "should_be": st.column_config.NumberColumn("Should be", format="%.2f"),
            "check": st.column_config.TextColumn("Match"),
        },
    )

    typed_rows = _typed_rows(rows, doc)
    typed_totals = _typed_totals(totals, doc)
    preview = {**doc, "opening_balance": opening, "closing_balance": closing,
               "summary_totals": [t for t in typed_totals if t["amount"] is not None and t["label"]],
               "transactions": [{**r, "amount": abs(r["amount"] or 0.0)} for r in typed_rows
                                if r["amount"] is not None or r["description"]]}
    preview.pop("checked_by_hand", None)
    preview_result = document_rules.verify_statement(preview)
    changed = _has_changes(doc, preview)
    if changed:
        st.markdown("**3. With your fixes**")
        _show_result(preview_result, "If you save: ")

    buttons = st.container(horizontal=True)
    if buttons.button("Save my fixes", type="primary", key=f"{key}_save", disabled=not changed):
        count = workpaper.save_corrections(
            files, edits, filename, doc_index, rows=typed_rows, summary_totals=typed_totals,
            opening_balance=opening, closing_balance=closing)
        doc = files[filename]["documents"][doc_index]
        doc["edit_version"] = version + 1
        st.toast(f"Saved {count} fix(es).")
        st.rerun()
    if doc.get("corrected_by_hand") and buttons.button("Go back to what the AI read", key=f"{key}_undo"):
        workpaper.undo_corrections(files, edits, filename, doc_index)
        files[filename]["documents"][doc_index]["edit_version"] = version + 1
        st.toast("Back to what the AI read.")
        st.rerun()

    if result["status"] in ("Discrepancy", "Not Reconcilable") and not changed:
        with buttons.popover("It's correct — I checked it by hand"):
            note = st.text_input("Why is it OK? (required)", key=f"{key}_note",
                                 placeholder="Example: compared every row with the PDF")
            if st.button("Mark as checked", key=f"{key}_mark", disabled=not note.strip()):
                workpaper.mark_checked_by_hand(files, edits, filename, doc_index, note)
                files[filename]["documents"][doc_index]["edit_version"] = version + 1
                st.rerun()


def _has_changes(doc: Dict[str, Any], preview: Dict[str, Any]) -> bool:
    def rows_key(rows):
        return [(r.get("row_id"), r.get("date"), (r.get("description") or "").strip(),
                 round(abs(r.get("amount") or 0.0), 2), r.get("direction"),
                 None if r.get("running_balance") is None else round(r["running_balance"], 2)) for r in rows]

    def totals_key(totals):
        return sorted((t["label"].strip(), round(t["amount"], 2), t["effect"]) for t in totals)

    def balance(value):
        return None if value is None else round(value, 2)

    original_rows = [{**r, "date": _to_date(r.get("date"))} for r in doc.get("transactions") or []]
    return (rows_key(original_rows) != rows_key(preview["transactions"])
            or totals_key(doc.get("summary_totals") or []) != totals_key(preview["summary_totals"])
            or balance(doc.get("opening_balance")) != balance(preview["opening_balance"])
            or balance(doc.get("closing_balance")) != balance(preview["closing_balance"]))
