"""Proposes a reusable per-bank statement vocabulary (see
core/bank_profiles.py) after an AI extraction has already succeeded and
self-checked against the statement's own declared totals
(BankPDFParser._extraction_looks_reliable in core/pdf_parser.py).

This only ever produces DATA -- phrase lists -- never executable code. The
caller (BankPDFParser._recover_unrecognized_bank) re-runs the existing,
already-tested rules engine with the proposed profile against the SAME
document and only saves it if that reproduces the AI's own verified
numbers. A profile that fails that check is simply discarded; the bank
keeps using the paid AI path until a profile that passes exists.
"""
import json
import os
import re
from typing import Optional

from core.bank_profiles import BankProfile
from core.pdf_parser import FinancialDocumentData

# Same tier as core/llm_extractor.py, for the same reason: identifying
# header phrases in text already given to it is a pattern-matching task,
# not one that needs the flagship tier.
LLM_MODEL = "gpt-6-luna"

SYSTEM_PROMPT = """You analyze the raw extracted text of one bank statement \
and identify the exact section-header phrases it uses, so a simple \
rules-based parser can recognize this bank's statements in future without \
calling an AI model again.

You are given the statement's raw text and a list of transactions already \
confirmed correct for it. Find the literal header line that introduces each \
kind of section in THIS statement (e.g. "DEPOSITS AND ADDITIONS", \
"ELECTRONIC WITHDRAWALS", "DAILY ENDING BALANCE" -- use whatever this \
statement actually prints, verbatim, all lowercase, without any trailing \
"(continued)" suffix). A statement may have more than one withdrawal-type \
header (e.g. separate ATM/debit-card and electronic-withdrawal sections) -- \
list all of them. If a category has no header in this statement, return an \
empty list for it rather than guessing. Never include a "Total ..." rollup \
line as a header -- only the header that introduces a section's line items.
"""

RESPONSE_JSON_SCHEMA = {
    "type": "json_schema",
    "name": "record_bank_profile",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["bank_slug", "display_name", "deposit_headers", "withdrawal_headers",
                     "checks_headers", "daily_balance_skip_headers"],
        "properties": {
            "bank_slug": {
                "type": "string",
                "description": "Short lowercase-with-underscores identifier, e.g. 'chase' or 'wells_fargo'.",
            },
            "display_name": {"type": "string"},
            "deposit_headers": {"type": "array", "items": {"type": "string"}},
            "withdrawal_headers": {"type": "array", "items": {"type": "string"}},
            "checks_headers": {"type": "array", "items": {"type": "string"}},
            "daily_balance_skip_headers": {"type": "array", "items": {"type": "string"}},
        },
    },
    "strict": True,
}


def build_bank_profile(payload: dict) -> Optional[BankProfile]:
    """Maps the model's structured JSON payload into a BankProfile --
    separated from learn_bank_profile() so it can be unit-tested against a
    fake payload with no API call at all. Returns None if the payload has
    no usable bank_slug."""
    slug = re.sub(r"[^a-z0-9_]+", "_", str(payload.get("bank_slug", "")).strip().lower()).strip("_")
    if not slug:
        return None
    return BankProfile(
        bank_slug=slug,
        display_name=str(payload.get("display_name", "")),
        deposit_headers=[str(h).lower() for h in payload.get("deposit_headers", [])],
        withdrawal_headers=[str(h).lower() for h in payload.get("withdrawal_headers", [])],
        checks_headers=[str(h).lower() for h in payload.get("checks_headers", [])],
        daily_balance_skip_headers=[str(h).lower() for h in payload.get("daily_balance_skip_headers", [])],
    )


def learn_bank_profile(raw_text: str, verified_data: FinancialDocumentData,
                        filename: str) -> Optional[BankProfile]:
    """Returns a candidate BankProfile, or None if the `openai` package /
    API key aren't available, or the model's response didn't parse -- either
    way, learning simply doesn't happen this time. Never required for the
    extraction that already succeeded to be returned to the caller."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai
    except ImportError:
        return None

    client = openai.OpenAI(api_key=api_key)
    sample_transactions = "\n".join(
        f"- {t.date} | {t.payee} | ${t.amount:,.2f} | {'deposit' if t.is_deposit else 'withdrawal'}"
        for t in verified_data.transactions[:60]
    )

    try:
        response = client.responses.create(
            model=LLM_MODEL,
            instructions=SYSTEM_PROMPT,
            input=[{
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": (
                        f"Statement filename: {filename}\n\n"
                        f"Confirmed transactions:\n{sample_transactions}\n\n"
                        f"Raw statement text:\n{raw_text[:20000]}"
                    ),
                }],
            }],
            text={"format": RESPONSE_JSON_SCHEMA},
        )
    except Exception:
        return None

    if not response.output_text:
        return None

    try:
        payload = json.loads(response.output_text)
    except json.JSONDecodeError:
        return None

    return build_bank_profile(payload)
