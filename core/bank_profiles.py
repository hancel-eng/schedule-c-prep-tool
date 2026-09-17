"""Learned per-bank statement vocabulary.

pdf_parser.py's rules engine recognizes a bank by the exact section-header
phrases its statements print ("DEPOSITS AND ADDITIONS", "ELECTRONIC
WITHDRAWALS", ...). Those phrases were hardcoded for Regions/Capital One/
Chase because that's what real client data has actually shown so far -- a
new bank will use different wording and won't be recognized until someone
adds it.

A BankProfile stores that same kind of vocabulary as data instead of new
Python, so it can be *proposed* by core/bank_learner.py after an LLM
extraction succeeds on an unrecognized statement, self-verified against
that same statement, and reused for free afterward -- see
BankPDFParser._recover_unrecognized_bank in core/pdf_parser.py for the full
flow. Profiles are meant to be committed to the repo: they describe a bank's
statement format, never a client's own data.
"""
import json
import os
from typing import List

from pydantic import BaseModel, Field

PROFILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bank_profiles"
)


class BankProfile(BaseModel):
    bank_slug: str
    display_name: str = ""
    deposit_headers: List[str] = Field(default_factory=list)
    withdrawal_headers: List[str] = Field(default_factory=list)
    checks_headers: List[str] = Field(default_factory=list)
    daily_balance_skip_headers: List[str] = Field(default_factory=list)


def load_bank_profiles(profiles_dir: str = PROFILES_DIR) -> List[BankProfile]:
    """Loads every learned profile. Order doesn't matter -- callers try them
    one at a time until one reproduces a reliable extraction. A malformed
    profile file is skipped rather than raised, since a bad profile must
    never be able to break parsing for every other document."""
    if not os.path.isdir(profiles_dir):
        return []
    profiles = []
    for name in sorted(os.listdir(profiles_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(profiles_dir, name), "r") as f:
                profiles.append(BankProfile(**json.load(f)))
        except Exception:
            continue
    return profiles


def save_bank_profile(profile: BankProfile, profiles_dir: str = PROFILES_DIR) -> str:
    """Persists a learned profile as its own JSON file, named by bank_slug
    so re-learning the same bank overwrites rather than duplicates it."""
    os.makedirs(profiles_dir, exist_ok=True)
    path = os.path.join(profiles_dir, f"{profile.bank_slug}.json")
    with open(path, "w") as f:
        json.dump(profile.model_dump(), f, indent=2)
    return path
