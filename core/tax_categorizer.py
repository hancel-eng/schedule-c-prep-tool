import re
import json
import os
from typing import Dict, Any, Tuple

# Comprehensive Offline IRS Schedule C Keyword & Payee Dictionary
SCHEDULE_C_CATEGORIES = {
    "Line 8: Advertising": [
        "google ads", "google adw", "facebook ads", "meta ads", "instagram ads", "linkedin ads", 
        "bing ads", "yelp", "mailchimp", "canva", "godaddy", "namecheap", "wix", "squarespace", 
        "vistaprint", "hubspot", "copy.ai", "billboard", "marketing", "flyers", "domain", "promotional", "advertising"
    ],
    "Line 9: Car and truck expenses": [
        "chevron", "shell", "exxon", "mobil", "bp", "texaco", "speedway", "sunoco", "arco", "valero", 
        "autozone", "o'reilly", "pep boys", "jiffy lube", "car wash", "tolls", "parking", "ezpass", 
        "fastrak", "oil change", "auto repair", "gas station", "fuel", "citgo", "7-eleven", "7 eleven", "circle k", "racetrac", "speedway"
    ],
    "Line 10: Commissions and fees": [
        "stripe fee", "stripe", "paypal fee", "paypal", "square fee", "square inc", "merchant fee", 
        "clover", "authorize.net", "bank fee", "wire fee", "service charge", "monthly maintenance", "commission",
        "annual fee", "late fee", "membership fee", "card fee"
    ],
    "Line 11: Contract labor": [
        "subcontractor", "contractor", "freelance", "upwork", "fiverr", "toptal", "1099", "consulting", 
        "developer", "designer", "contract labor"
    ],
    "Line 13: Depreciation and section 179": [
        "apple store", "best buy", "dell", "hp inc", "lenovo", "b&h photo", "cdw", "micro center", 
        "equipment purchase", "machinery", "computer hardware", "macbook", "server", "generator"
    ],
    "Line 15: Insurance (other than health)": [
        "hiscox", "geico business", "progressive commercial", "hartford", "travelers", "nationwide", 
        "workers comp", "business liability", "commercial insurance", "liability insurance"
    ],
    "Line 16b: Other interest": [
        "interest charge", "finance charge", "card interest", "loan interest", "credit card interest", "business loan int", "interest expense"
    ],
    "Line 17: Legal and professional services": [
        "cpa", "attorney", "lawyer", "legalzoom", "rocket lawyer", "accounting", "bookkeeper", 
        "quickbooks", "intuit", "xero", "freshbooks", "software", "microsoft 365", "google workspace", 
        "gsuite", "aws", "amazon web services", "azure", "digitalocean", "github", "cloudflare"
    ],
    "Line 18: Office expense": [
        "staples", "office depot", "officemax", "paper", "ink", "toner", "docusign", "dropbox", 
        "zoom", "slack", "notion", "asana", "monday.com", "trello", "office supplies", "amazon"
    ],
    "Line 20b: Rent or lease (other business property)": [
        "rent", "lease", "storage unit", "public storage", "extra space", "wework", "regus", 
        "commercial rent", "office rent", "real estate rent"
    ],
    "Line 21: Repairs and maintenance": [
        "plumbing", "electrician", "hvac repair", "handyman", "janitorial", "cleaning service", 
        "building repair", "facilities maintenance"
    ],
    "Line 22: Supplies": [
        "uline", "ups store", "fedex", "usps", "stamps.com", "packaging", "shipping boxes", 
        "raw materials", "supplies", "freight", "home depot", "lowe's", "lowes"
    ],
    "Line 23: Taxes and licenses": [
        "secretary of state", "sec of state", "state tax board", "business license", "annual report fee", 
        "property tax", "permit", "licenses"
    ],
    "Line 24a: Travel": [
        "delta", "american air", "united air", "southwest air", "jetblue", "hotel", "marriott", 
        "hilton", "hyatt", "airbnb", "hertz", "enterprise rent", "avis", "flight", "taxi", "uber trip", "lyft trip"
    ],
    "Line 24b: Deductible meals (50%)": [
        "starbucks", "dunkin", "doordash", "ubereats", "grubhub", "restaurant", "cafe", "coffee", 
        "diner", "bistro", "catering", "bakery", "mcdonalds", "chipotle", "panera"
    ],
    "Line 25: Utilities": [
        "at&t", "verizon", "t-mobile", "comcast", "xfinity", "spectrum", "centurylink", "electric", 
        "power", "water utility", "gas utility", "trash", "waste management", "internet"
    ],
    "Line 26: Wages": [
        "payroll", "gusto", "adp", "paychex", "rippling", "salaries", "employee wages"
    ],
    "Line 27a: Other expenses": [
        "education", "course", "udemy", "coursera", "subscription", "dues", "chamber of commerce", 
        "association", "training", "books"
    ]
}

# Non-P&L patterns. These are the tool's main safeguard against counting money
# movement as income or expense, so they are written as token-gap regexes rather
# than fixed substrings: real statement descriptions insert words between the
# tokens (e.g. "CREDIT CARD AUTOMATIC PAYMENT"). Matched before anything else.
NON_PNL_PATTERNS = {
    "Non-P&L: Credit Card Payment": [
        r"\bcredit\s+card\s+(?:\w+\s+){0,3}(?:payment|pymt|pmt|pay)\b",
        r"\bcard\s+(?:\w+\s+){0,2}(?:payment|pymt|pmt)\b",
        r"\b(?:payment|pymt|pmt)\b[\W_]*(?:\w+[\W_]+){0,2}thank\s*you\b",
        r"\bmobile\s+(?:payment|pymt|pmt)\b",
        r"\bauto\s*pay\b",
        r"\bautopay\b",
        r"\bepay\b",
        r"\b(?:capital\s+one|chase|amex|american\s+express|citi|discover|"
        r"synchrony|barclay|wells\s+fargo|bank\s+of\s+america)\s+"
        r"(?:\w+\s+){0,3}(?:payment|pymt|pmt)\b",
    ],
    "Non-P&L: Internal Transfer": [
        r"\btransfer\s+(?:to|from)\b",
        r"\b(?:online|internal|book|funds?|acct|account)\s+(?:\w+\s+){0,2}"
        r"(?:transfer|xfer|trnsfr|tsfr)\b",
        r"\b(?:transfer|xfer|trnsfr|tsfr)\s+(?:\w+\s+){0,2}"
        r"(?:to|from)\s+(?:checking|savings|acct|account)\b",
        r"\bzelle\s+(?:\w+\s+){0,2}transfer\b",
        r"\bbetween\s+accounts\b",
    ],
    "Non-P&L: Owner Draw / Contribution": [
        r"\bowner'?s?\s+(?:draw|equity|contribution|withdrawal)\b",
        r"\bpersonal\s+draw\b",
        r"\bmember\s+(?:draw|distribution|contribution)\b",
        r"\bpartner\s+(?:draw|distribution|contribution)\b",
        r"\bshareholder\s+distribution\b",
        r"\bcapital\s+contribution\b",
        r"\bdistribution\s+to\s+(?:owner|member|partner)\b",
    ],
    "Non-P&L: Loan Proceeds / Repayment": [
        r"\bsba\s+loan\b",
        r"\bloan\s+(?:\w+\s+){0,2}"
        r"(?:proceeds|payment|pymt|repayment|disbursement|advance|deposit)\b",
        r"\b(?:principal|note)\s+payment\b",
        r"\bnote\s+payable\b",
        r"\bline\s+of\s+credit\b",
        r"\bloc\s+(?:draw|advance)\b",
        r"\bmerchant\s+cash\s+advance\b",
        # Named online small-business lenders. Confirmed on a real statement:
        # a $37,500 loan draw from "Headwaycapital" was counted as Gross
        # Receipts, and its 9 biweekly repayment debits ("Hwcrcvbls" -- the
        # lender's own ACH descriptor code) totaling $44,512.32 sat in
        # Uncategorized as if they were a deductible expense. Both a loan
        # draw and its repayment belong off the P&L entirely -- only the
        # interest portion of a repayment is deductible, and a bank statement
        # never breaks that out per-payment, so this can only ever exclude
        # the whole payment and flag it, never estimate the interest split.
        # This list is not exhaustive; other clients will surface other
        # lenders the same way "google ads" and "o'reilly" surfaced other
        # keyword gaps -- add the exact name/descriptor as it's confirmed.
        r"\bheadway\s*capital\b",
        r"\bhwcrcvbls\b",
        r"\bkabbage\b",
        r"\bondeck\b",
        r"\bbluevine\b",
        r"\bfundbox\b",
        r"\bcredibly\b",
        r"\brapid\s+finance\b",
        r"\bnational\s+funding\b",
        r"\bforward\s+financing\b",
    ],
    "Non-P&L: Tax Refund / Reimbursement": [
        r"\brefund\b",
        r"\breimbursement\b",
        r"\breimb\b",
        r"\b(?:irs|state)\s+(?:\w+\s+){0,2}refund\b",
        # The standard ACH descriptor for a federal tax refund abbreviates
        # "refund" to "REF" -- "IRS TREAS 310 TAX REF" is the literal code the
        # IRS uses on every direct-deposit refund, confirmed on a real
        # statement. "\brefund\b" alone never matches this.
        r"\birs\s+treas\s+\d+\s+tax\s+ref\b",
        r"\btax\s+ref\b",
    ],
    # A vendor credit on a card/account -- e.g. "Card Credit The Home Depot"
    # on a real statement -- is money coming back for an earlier purchase,
    # not a tax refund and not new business revenue. Kept as its own category
    # rather than folded into "Tax Refund / Reimbursement": on a real
    # engagement 15 of 16 items in that bucket turned out to be vendor
    # credits and only 1 an actual IRS refund, which reads as if there were
    # 15 tax refunds to a preparer skimming the sheet.
    "Non-P&L: Vendor Purchase Credit": [
        r"\bcard\s+credit\b",
        r"\bpurchase\s+return\b",
        r"\bmerchandise\s+credit\b",
    ],
    # A deposit the bank later reversed (a bounced check, most commonly) is
    # not new revenue -- it is the undoing of a deposit that never actually
    # cleared. Its own separate category rather than folded into "refund"
    # since a preparer needs to know a check bounced, specifically, to follow
    # up with the client about it.
    "Non-P&L: Returned/Reversed Deposit": [
        r"\breturned\s+deposit\s+item\b",
        r"\bdeposit\s+(?:return|reversal|reversed|correction)\b",
        r"\bunauthorized\s+return\b",
        r"\bnsf\s+return\b",
    ],
}

# Tokens that appear in statement descriptions but identify no vendor. A
# description made up of only these (plus stripped digits) is Unresolved, not
# merely low confidence -- there is nothing for a preparer to recognize.
GENERIC_DESCRIPTION_TOKENS = {
    "purchase", "debit", "credit", "card", "pos", "ach", "eft", "trans",
    "transaction", "ref", "id", "no", "num", "date", "auth", "seq", "item",
    "misc", "other", "charge", "chg", "xx", "xxx", "xxxx", "na", "nan", "none",
    "unknown", "check", "chk", "draft", "withdrawal", "payment", "pmt", "pymt",
}

CUSTOM_RULES_FILE = "custom_rules.json"

# Compiled once at import time, not per transaction: a naive `kw in text`
# substring check false-positives on short/generic keywords -- confirmed on a
# real statement where "mobil" (the gas brand, a Line 9 keyword) matched
# inside "mobile" in "CAPITAL ONE MOBILE PYMT", miscategorizing a $2,250 card
# payment as a car/truck expense. Word-boundary regex matching closes that
# whole class of false positive (also guards "bp" inside unrelated words,
# "rent" inside "current"/"different", "ink" inside "drink", etc.) without
# having to hand-audit every keyword for length.
def _keyword_pattern(kw: str) -> re.Pattern:
    # Mirrors clean_payee_text's own apostrophe -> space substitution, so a
    # dictionary entry spelled "o'reilly" still matches a statement that
    # prints "O Reilly" (banks generally can't print an apostrophe at all).
    normalized = re.escape(kw.replace("'", " "))
    return re.compile(r'\b' + normalized + r'\b')


_CATEGORY_PATTERNS = {
    cat: [(kw, _keyword_pattern(kw)) for kw in keywords]
    for cat, keywords in SCHEDULE_C_CATEGORIES.items()
}

class TaxCategorizer:
    """
    Robust 100% Offline Tax Categorization Engine.
    Combines rule dictionaries, regex pattern matching, payee cleaning, and local custom rules.
    """

    def __init__(self, custom_rules: Dict[str, str] = None):
        self.custom_rules = custom_rules or self._load_local_custom_rules()

    def _load_local_custom_rules(self) -> Dict[str, str]:
        if os.path.exists(CUSTOM_RULES_FILE):
            try:
                with open(CUSTOM_RULES_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_custom_rule(self, pattern: str, category: str):
        """Saves a user custom rule locally to disk."""
        self.custom_rules[pattern.lower().strip()] = category
        try:
            with open(CUSTOM_RULES_FILE, "w") as f:
                json.dump(self.custom_rules, f, indent=2)
        except Exception as e:
            print(f"Error saving custom rules: {e}")

    def clean_payee_text(self, text: str) -> str:
        """Strips transaction numbers, dates, store IDs to isolate vendor name."""
        t = str(text).lower()
        t = re.sub(r'#\d+', '', t)
        t = re.sub(r'\b\d{4,}\b', '', t) # Remove long numbers/store IDs
        # Card-network sub-merchant descriptors separate the merchant name from
        # its billing descriptor with "*" instead of a space (e.g. Google's own
        # statement text reads "Google *Ads9829", not "Google Ads"). Left as a
        # literal asterisk, "google *ads" never matches the "google ads"
        # keyword -- confirmed on a real statement, where this alone hid
        # $9,921.87 of Advertising inside Uncategorized. Apostrophes become a
        # space rather than being dropped: a bank statement generally can't
        # print one at all and substitutes a space instead ("O Reilly", not
        # "O'Reilly" or "OReilly"), so this matches the dictionary's own
        # apostrophed spellings, which get the identical treatment when the
        # keyword patterns are compiled below.
        t = t.replace("'", " ")
        t = re.sub(r'[*/_]+', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    def categorize_transaction(self, payee: str, description: str, amount: float, is_deposit: bool) -> Tuple[str, str, float]:
        """
        Offline categorization. Returns (category, confidence_state, confidence_score).
        Confidence states: 'High Confidence', 'Needs Review', 'Unresolved'
        """
        raw_text = f"{payee} {description}".lower().strip()
        clean_text = self.clean_payee_text(raw_text)

        # 1. ALWAYS Check Non-P&L Patterns FIRST (Credit Card Payments, Transfers, Draws)
        for non_pnl_cat, patterns in NON_PNL_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, clean_text) or re.search(pat, raw_text):
                    return non_pnl_cat, "High Confidence", 0.98

        # 2. Check Custom User Rules (Saved locally)
        for pattern, cat in self.custom_rules.items():
            if pattern.lower() in clean_text or pattern.lower() in raw_text:
                return cat, "High Confidence", 0.99

        # 3. Deposit / Revenue Check
        if is_deposit:
            return "Income: Gross Receipts", "High Confidence", 0.95

        # 4. Check Offline Keyword Dictionary for Expenses
        matched_cat = None
        matched_score = 0.0

        for cat, patterns in _CATEGORY_PATTERNS.items():
            for kw, kw_pattern in patterns:
                if kw_pattern.search(clean_text) or kw_pattern.search(raw_text):
                    matched_cat = cat
                    matched_score = 0.92 if len(kw) > 4 else 0.80
                    break
            if matched_cat:
                break

        if matched_cat:
            state = "High Confidence" if matched_score >= 0.85 else "Needs Review"
            return matched_cat, state, matched_score

        # 5. No keyword matched. Never guess a category -- distinguish an item a
        # preparer can plausibly resolve from the payee text ("Needs Review")
        # from one where the source gives nothing to go on ("Unresolved").
        if self._is_opaque_payee(clean_text):
            return "Line 27a: Other expenses (Uncategorized)", "Unresolved", 0.0

        return "Line 27a: Other expenses (Uncategorized)", "Needs Review", 0.40

    def _is_opaque_payee(self, clean_text: str) -> bool:
        """True when the description carries no usable vendor identity.

        clean_payee_text has already stripped store IDs and long digit runs, so
        what remains is either a readable vendor name or noise.
        """
        alpha_tokens = [t for t in re.findall(r"[a-z]{2,}", clean_text)
                        if t not in GENERIC_DESCRIPTION_TOKENS
                        and not re.fullmatch(r"x+", t)]
        return not alpha_tokens
