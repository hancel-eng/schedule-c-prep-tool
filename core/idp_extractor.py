"""Multiclass Intelligent Document Processing (IDP): classifies an
unrecognized uploaded document -- bank_statement, check, invoice, receipt,
or unknown -- and extracts it accordingly, in one AI call.

This is NOT run on every upload. It only runs where core/pdf_parser.py's
rules engine (and any learned bank_profiles/*.json) already failed its own
reliability check -- see BankPDFParser._recover_unrecognized_bank. A
Regions/Capital One/Chase statement, or any bank whose format has already
been learned, is read for free in milliseconds and never reaches this
module; a document that genuinely isn't a bank statement at all (a check
copy, an invoice, a receipt) -- previously force-fit into the bank-
statement pipeline and silently misread -- now gets correctly identified
and extracted as what it actually is.

Two things this module is never allowed to do, same discipline as the rest
of this codebase:
- Never decide a tax category. A classified bank_statement's transactions
  still go through the ordinary rule-based TaxCategorizer, same as every
  other extraction strategy.
- Never be trusted on its own. Every numeric claim this produces is
  independently re-checked in Python against the document's own declared
  arithmetic (see verify_bank_statement_balance_chain below and
  BankPDFParser._extraction_looks_reliable) -- the model's own
  self-reported "quality_validation.mathematically_balanced" is logged for
  visibility, never relied on by itself.
"""
import base64
import json
import os
from typing import Any, Dict, List, Optional

LLM_MODEL = "gpt-6-astra"

SYSTEM_PROMPT = """You are an expert system in Multiclass Intelligent \
Document Processing (IDP) and Core Financial Auditing. Analyze the \
attached document (image or PDF), classify it with absolute precision, \
and extract its financial information into the structured format you \
have been given. You are a transcriber and classifier only, not an \
accountant: never categorize a transaction for tax purposes, never \
compute a figure the document doesn't itself state, never invent a row \
that isn't printed, and never skip a row that is.

1. CLASSIFICATION (router layer). Identify the document type from its \
visual layout and content. Only these five values are valid: \
"bank_statement", "check", "invoice", "receipt", "unknown" -- use \
"unknown" rather than guessing if the document doesn't clearly match any \
of the other four.

2. EXTRACTION RULES BY TYPE. Populate only the one structured_data field \
matching the detected type; leave the other two null.
- bank_statement: bank_name, client_name, period, opening_balance, \
total_deposits, total_withdrawals, closing_balance, and every transaction \
row (date, description, amount, type "credit"|"debit", and \
resulting_balance -- the running balance printed on that same row, if the \
statement prints one; null if it doesn't). Do not include section \
headers, column headers, page numbers, or summary/rollup lines as \
transactions.
- check: drawer (who wrote it), payee, issue_date, numerical_amount, \
text_amount (the written-out amount in words, if present), \
account_number, routing_number, check_number (from the printed check \
number or the bottom MICR line), signature_present.
- invoice or receipt (both use the same fields): issuer, client, date, \
document_number, subtotal, taxes, total, and every line item (item, \
quantity, unit_price, item_total).

3. NORMALIZATION. Dates as ISO YYYY-MM-DD. Amounts as plain floats, no \
currency symbols or thousands separators. A transaction's amount is \
always a positive number; direction is carried by "type" ("credit" or \
"debit"), never by a negative amount.

4. VALIDATION (do this arithmetic yourself before responding; it is not \
decorative). For a bank_statement: check whether \
opening_balance + total_deposits - total_withdrawals == closing_balance, \
and separately whether each row's resulting_balance is consistent with \
the previous row's (running balance + this row's signed amount). For a \
check: does numerical_amount plausibly match text_amount. For an invoice \
or receipt: does subtotal + taxes == total. Set \
mathematically_balanced accordingly, set requires_human_review to true \
if any check fails or any figure was illegible/ambiguous, and name the \
specific reason in alerts -- an empty alerts list if there's nothing to \
flag.
"""

_TRANSACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["date", "description", "amount", "type", "resulting_balance"],
    "properties": {
        "date": {"type": "string", "description": "ISO YYYY-MM-DD, as best determined from the statement."},
        "description": {"type": "string"},
        "amount": {"type": "number", "description": "Always positive; direction carried by type."},
        "type": {"type": "string", "enum": ["credit", "debit"]},
        "resulting_balance": {
            "type": ["number", "null"],
            "description": "The running balance printed on this same row, if the statement prints one.",
        },
    },
}

_BANK_STATEMENT_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["bank_name", "client_name", "period", "opening_balance", "total_deposits",
                 "total_withdrawals", "closing_balance", "transactions"],
    "properties": {
        "bank_name": {"type": ["string", "null"]},
        "client_name": {"type": ["string", "null"]},
        "period": {"type": ["string", "null"]},
        "opening_balance": {"type": ["number", "null"]},
        "total_deposits": {"type": ["number", "null"]},
        "total_withdrawals": {"type": ["number", "null"]},
        "closing_balance": {"type": ["number", "null"]},
        "transactions": {"type": "array", "items": _TRANSACTION_SCHEMA},
    },
}

_CHECK_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["drawer", "payee", "issue_date", "numerical_amount", "text_amount",
                 "account_number", "routing_number", "check_number", "signature_present"],
    "properties": {
        "drawer": {"type": ["string", "null"]},
        "payee": {"type": ["string", "null"]},
        "issue_date": {"type": ["string", "null"]},
        "numerical_amount": {"type": ["number", "null"]},
        "text_amount": {"type": ["string", "null"]},
        "account_number": {"type": ["string", "null"]},
        "routing_number": {"type": ["string", "null"]},
        "check_number": {"type": ["string", "null"], "description": "Digits only, as printed -- no leading '#'."},
        "signature_present": {"type": ["boolean", "null"]},
    },
}

_LINE_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item", "quantity", "unit_price", "item_total"],
    "properties": {
        "item": {"type": "string"},
        "quantity": {"type": ["number", "null"]},
        "unit_price": {"type": ["number", "null"]},
        "item_total": {"type": ["number", "null"]},
    },
}

_INVOICE_RECEIPT_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["issuer", "client", "date", "document_number", "subtotal", "taxes", "total", "line_items"],
    "properties": {
        "issuer": {"type": ["string", "null"]},
        "client": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"]},
        "document_number": {"type": ["string", "null"]},
        "subtotal": {"type": ["number", "null"]},
        "taxes": {"type": ["number", "null"]},
        "total": {"type": ["number", "null"]},
        "line_items": {"type": "array", "items": _LINE_ITEM_SCHEMA},
    },
}

RESPONSE_JSON_SCHEMA = {
    "type": "json_schema",
    "name": "classify_and_extract_document",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["processing_metadata", "structured_data", "quality_validation"],
        "properties": {
            "processing_metadata": {
                "type": "object",
                "additionalProperties": False,
                "required": ["detected_document_type", "issuing_entity", "classification_confidence"],
                "properties": {
                    "detected_document_type": {
                        "type": "string",
                        "enum": ["bank_statement", "check", "invoice", "receipt", "unknown"],
                    },
                    "issuing_entity": {"type": ["string", "null"]},
                    "classification_confidence": {"type": "number"},
                },
            },
            "structured_data": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bank_statement", "check", "invoice_or_receipt"],
                "properties": {
                    "bank_statement": _BANK_STATEMENT_SCHEMA,
                    "check": _CHECK_SCHEMA,
                    "invoice_or_receipt": _INVOICE_RECEIPT_SCHEMA,
                },
            },
            "quality_validation": {
                "type": "object",
                "additionalProperties": False,
                "required": ["mathematically_balanced", "requires_human_review", "alerts"],
                "properties": {
                    "mathematically_balanced": {"type": "boolean"},
                    "requires_human_review": {"type": "boolean"},
                    "alerts": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
    "strict": True,
}


def _get_api_key() -> Optional[str]:
    return os.environ.get("OPENAI_API_KEY")


def classify_and_extract(raw_bytes: bytes, filename: str) -> Dict[str, Any]:
    """Sends one document to OpenAI and returns the raw structured payload
    (processing_metadata + structured_data + quality_validation) exactly as
    produced -- unmapped, unverified. Raises RuntimeError on a missing key/
    package or an empty response; callers decide what to do with that, and
    must independently verify the numbers before trusting them (see
    verify_bank_statement_balance_chain)."""
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set -- add it to Streamlit secrets "
            "(see .streamlit/secrets.toml.example) to enable AI document processing."
        )
    try:
        import openai
    except ImportError as e:
        raise RuntimeError("The 'openai' package is not installed.") from e

    client = openai.OpenAI(api_key=api_key)
    b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")
    media_type = "application/pdf" if filename.lower().endswith(".pdf") else "image/png"

    response = client.responses.create(
        model=LLM_MODEL,
        instructions=SYSTEM_PROMPT,
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_file",
                    "filename": filename,
                    "file_data": f"data:{media_type};base64,{b64}",
                },
                {"type": "input_text", "text": f"Classify and extract this document (filename: {filename})."},
            ],
        }],
        text={"format": RESPONSE_JSON_SCHEMA},
    )

    if not response.output_text:
        raise RuntimeError("The model did not return a structured extraction.")

    return json.loads(response.output_text)


def verify_bank_statement_balance_chain(bank_statement: Dict[str, Any],
                                         tolerance: float = 0.01) -> Optional[bool]:
    """Independently re-checks the per-row balance chain in Python -- never
    trusts the model's own self-reported "mathematically_balanced" flag by
    itself. Walks opening_balance forward through every transaction's
    signed amount and compares the running total against that row's own
    resulting_balance, when the document printed one.

    Returns True if every row with a resulting_balance is consistent,
    False if any row disagrees, or None if this check isn't possible at
    all (no opening_balance, or no row reports a resulting_balance) --
    callers should fall back to the aggregate opening+deposits-withdrawals
    ==closing check in that case, not treat None as a pass.
    """
    transactions = bank_statement.get("transactions") or []
    opening = bank_statement.get("opening_balance")
    if opening is None or not any(t.get("resulting_balance") is not None for t in transactions):
        return None

    running = float(opening)
    for tx in transactions:
        amount = abs(float(tx.get("amount", 0.0)))
        signed = amount if tx.get("type") == "credit" else -amount
        running += signed
        resulting_balance = tx.get("resulting_balance")
        if resulting_balance is not None and abs(running - float(resulting_balance)) > tolerance:
            return False
    return True


def bank_statement_payload_to_transactions(bank_statement: Dict[str, Any], filename: str,
                                            categorizer) -> List[Dict[str, Any]]:
    """Maps a classify_and_extract() payload's structured_data.bank_statement
    into plain transaction dicts shaped like every other extraction
    strategy's output (date/payee/description/amount/is_deposit/category/
    confidence_state/confidence_score/source_file/original_category) --
    every transaction still goes through the ordinary rule-based
    TaxCategorizer, same as a rule-extracted one."""
    results = []
    for raw_tx in bank_statement.get("transactions") or []:
        is_deposit = raw_tx.get("type") == "credit"
        amount = abs(float(raw_tx.get("amount", 0.0)))
        signed_amount = amount if is_deposit else -amount
        payee = str(raw_tx.get("description", "")).strip()
        cat, conf_state, conf_score = categorizer.categorize_transaction(
            payee=payee, description=payee, amount=signed_amount, is_deposit=is_deposit
        )
        results.append({
            "date": str(raw_tx.get("date", "")),
            "payee": payee,
            "description": payee,
            "amount": signed_amount,
            "is_deposit": is_deposit,
            "category": cat,
            "confidence_state": conf_state,
            "confidence_score": conf_score,
            "source_file": filename,
            "original_category": "AI Document Processing (bank statement)",
        })
    return results
