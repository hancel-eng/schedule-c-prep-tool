"""LLM-based fallback extraction for a bank statement whose format the
rules engine in pdf_parser.py doesn't recognize at all -- no known header
phrase, no learned profile matches, and the extracted totals don't foot
against what the statement itself declares.

Used only as an escalation path -- see BankPDFParser._recover_unrecognized_bank.
Never used to decide tax categories: every transaction this returns still
goes through the exact same TaxCategorizer.categorize_transaction() call as
a rule-extracted one, so categorization stays 100% rule-based either way.
The only thing the model produces here is a literal transcription of what's
printed -- date, description, amount, direction -- plus the statement's own
declared summary figures, asked for separately so the result can be
self-checked the same way a rule-based extraction is
(BankPDFParser._extraction_looks_reliable): if what was "read" doesn't sum
to what the statement itself says it should, that surfaces as a
reconciliation problem, never a silently wrong number.

Requires the `anthropic` package and an ANTHROPIC_API_KEY in the
environment -- app.py copies it there from Streamlit secrets, the same way
the app's own access password is handled. See
.streamlit/secrets.toml.example.
"""
import base64
import os
from typing import List, Optional

from core.pdf_parser import FinancialDocumentData, TransactionItem
from core.tax_categorizer import TaxCategorizer

LLM_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are transcribing a bank or credit card statement PDF \
exactly as printed. You are not an accountant and must not categorize, \
compute, or infer anything -- only transcribe what is literally printed.

Rules:
- List every transaction line exactly as it appears: its date, its full \
description/payee text, its dollar amount, and whether it is money coming \
IN to the account (a deposit/credit/addition) or money going OUT (a \
withdrawal/debit/purchase/payment/check), based on which section of the \
statement it is printed under or how the statement itself labels it.
- Do not skip any transaction, and do not invent one that is not printed.
- Do not include section headers, column headers, page numbers, marketing \
text, or balance/summary rollup lines (e.g. "Total Deposits $X", "Beginning \
Balance $X") as transactions.
- Separately, report the statement's own declared summary figures exactly \
as printed, if present: beginning balance, ending balance, total deposits/ \
credits/additions, and total withdrawals/debits/charges. If the statement \
splits withdrawals across more than one summary line (e.g. a bank that \
reports "ATM & Debit Card Withdrawals", "Electronic Withdrawals", and \
"Checks Paid" as separate totals), add all of them together into a single \
total_withdrawals figure. Use null for any figure the statement does not \
print -- never estimate one.
- Amounts are always positive numbers; direction is carried separately by \
is_deposit, never by a negative amount.
"""

TOOL_SCHEMA = {
    "name": "record_statement_extraction",
    "description": "Records every transaction transcribed from the statement, plus its own declared summary totals.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["transactions", "declared"],
        "properties": {
            "transactions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["date", "payee", "amount", "is_deposit"],
                    "properties": {
                        "date": {"type": "string", "description": "As printed, e.g. '01/30' or '01/30/2023'."},
                        "payee": {"type": "string"},
                        "amount": {"type": "number", "description": "Always positive."},
                        "is_deposit": {"type": "boolean"},
                    },
                },
            },
            "declared": {
                "type": "object",
                "additionalProperties": False,
                "required": ["beginning_balance", "ending_balance", "total_deposits", "total_withdrawals"],
                "properties": {
                    "beginning_balance": {"type": ["number", "null"]},
                    "ending_balance": {"type": ["number", "null"]},
                    "total_deposits": {"type": ["number", "null"]},
                    "total_withdrawals": {"type": ["number", "null"]},
                },
            },
        },
    },
    "strict": True,
}


def _get_api_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY")


def build_financial_document_data(payload: dict, filename: str,
                                   categorizer: TaxCategorizer) -> FinancialDocumentData:
    """Maps the model's structured tool-call payload into the same
    FinancialDocumentData shape every rule-based strategy in pdf_parser.py
    returns, running each transcribed transaction through the ordinary
    rule-based categorizer -- separated from extract_transactions_llm() so
    it can be unit-tested against a fake payload with no API call at all."""
    transactions: List[TransactionItem] = []
    for raw_tx in payload.get("transactions", []):
        amount = abs(float(raw_tx["amount"]))
        is_deposit = bool(raw_tx["is_deposit"])
        signed_amount = amount if is_deposit else -amount
        payee = str(raw_tx["payee"]).strip()
        cat, conf_state, conf_score = categorizer.categorize_transaction(
            payee=payee, description=payee, amount=signed_amount, is_deposit=is_deposit
        )
        transactions.append(TransactionItem(
            date=str(raw_tx["date"]),
            payee=payee,
            description=payee,
            amount=signed_amount,
            is_deposit=is_deposit,
            category=cat,
            confidence_state=conf_state,
            confidence_score=conf_score,
            source_file=filename,
            original_category="LLM Fallback Extraction (unrecognized statement format)",
        ))

    declared = payload.get("declared") or {}
    return FinancialDocumentData(
        document_type="bank_or_receipt",
        bank_or_vendor=f"Unrecognized format (LLM fallback): {filename}",
        statement_type="bank_statement",
        bank_name="Unrecognized format (LLM fallback)",
        beginning_balance=declared.get("beginning_balance") or 0.0,
        ending_balance=declared.get("ending_balance") or 0.0,
        total_deposits=declared.get("total_deposits") or 0.0,
        total_withdrawals=declared.get("total_withdrawals") or 0.0,
        transactions=transactions,
    )


def extract_transactions_llm(raw_bytes: bytes, filename: str, year: int,
                              categorizer: TaxCategorizer) -> FinancialDocumentData:
    """Sends the PDF to Claude for literal transcription. Raises
    RuntimeError if the `anthropic` package or an API key aren't available,
    or if the model didn't return a structured extraction -- the caller
    (BankPDFParser._recover_unrecognized_bank) treats that the same as any
    other extraction failure: a diagnostics note, never a crash."""
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set -- add it to Streamlit secrets "
            "(see .streamlit/secrets.toml.example) to enable the LLM fallback."
        )
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("The 'anthropic' package is not installed.") from e

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")

    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "record_statement_extraction"},
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                },
                {"type": "text", "text": f"Transcribe this statement (filename: {filename})."},
            ],
        }],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("The model did not return a structured extraction.")

    return build_financial_document_data(tool_use.input, filename, categorizer)
