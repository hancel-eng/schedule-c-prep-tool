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
    ],
    "Non-P&L: Tax Refund / Reimbursement": [
        r"\brefund\b",
        r"\breimbursement\b",
        r"\breimb\b",
        r"\b(?:irs|state)\s+(?:\w+\s+){0,2}refund\b",
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

        for cat, keywords in SCHEDULE_C_CATEGORIES.items():
            for kw in keywords:
                if kw in clean_text or kw in raw_text:
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
