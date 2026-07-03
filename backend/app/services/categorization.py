"""Transaction categorization.

Two-tier strategy, exactly as described in the proposal:
  1. Rule-based pass — fast, deterministic keyword matching on the merchant text.
  2. ML fallback — a lightweight Naive Bayes classifier trained on whatever
     labelled history exists (rule hits + user corrections). Only consulted when
     the rules don't fire, so the system is useful from the very first upload and
     gets smarter as the user corrects categories.
"""

from __future__ import annotations

import re

# Default category taxonomy seeded into the categories table.
DEFAULT_CATEGORIES = [
    "Groceries",
    "Dining",
    "Transport",
    "Shopping",
    "Entertainment",
    "Subscriptions",
    "Utilities",
    "Health",
    "Travel",
    "Education",
    "Cash",
    "Income",
    "Transfers",
    "Fees",
    "Uncategorized",
]

# Keyword -> category. Matched case-insensitively as whole-ish substrings against
# the merchant + description text. Ordering doesn't matter; first match wins.
# Tuned India-first (Swiggy, Jio, UPI, ...) while keeping common global merchants.
RULES: dict[str, list[str]] = {
    # Checked FIRST: a cash/ATM withdrawal is its own thing. This must win before
    # any location keyword in the narration — an ATM located at "XYZ University"
    # is a place, not a fee or a tuition payment.
    "Cash": [
        "atm wdl",
        "atm cash",
        "atm withdrawal",
        "cash wdl",
        "cash withdrawal",
        "self wdl",
        "self withdrawal",
        "cash withdrawl",
        "atw",
        "nwd",
    ],
    "Groceries": [
        "bigbasket",
        "blinkit",
        "zepto",
        "dmart",
        "d mart",
        "reliance fresh",
        "reliance smart",
        "jiomart",
        "more supermarket",
        "spencer",
        "grofers",
        "natures basket",
        "licious",
        "country delight",
        "milkbasket",
        "grocery",
        "supermarket",
        "kirana",
        "whole foods",
        "walmart",
        "trader joe",
    ],
    "Dining": [
        "swiggy",
        "zomato",
        "eatsure",
        "dominos",
        "domino's",
        "pizza hut",
        "kfc",
        "mcdonald",
        "burger king",
        "starbucks",
        "chaayos",
        "haldiram",
        "barbeque",
        "cafe",
        "coffee",
        "restaurant",
        "biryani",
        "dunzo",
        "doordash",
        # Generic eatery words — "Sky Dining", "Cafe Break", "Curry Kitchen"...
        "dining",
        "diner",
        "eatery",
        "dhaba",
        "bhavan",
        "bhojan",
        "kitchen",
        "bakery",
        "sweets",
        "tiffin",
    ],
    "Transport": [
        "ola",
        "uber",
        "rapido",
        "irctc",
        "redbus",
        "indian railway",
        "metro",
        "namma metro",
        "dmrc",
        "fastag",
        "indianoil",
        "iocl",
        "hpcl",
        "bharat petroleum",
        "bpcl",
        "shell",
        "petrol",
        "fuel",
        "parking",
        "blu smart",
        "blusmart",
    ],
    "Subscriptions": [
        "netflix",
        "hotstar",
        "jio cinema",
        "jiocinema",
        "sonyliv",
        "zee5",
        "voot",
        "spotify",
        "gaana",
        "wynk",
        "youtube premium",
        "prime video",
        "amazon prime",
        "audible",
        "google one",
        "icloud",
        "adobe",
        "canva",
        "linkedin premium",
    ],
    "Shopping": [
        "flipkart",
        "myntra",
        "ajio",
        "meesho",
        "nykaa",
        "amazon",
        "tatacliq",
        "tata cliq",
        "snapdeal",
        "croma",
        "reliance digital",
        "decathlon",
        "ikea",
        "lenskart",
        "firstcry",
        "shopping",
        # Retail chains / malls — "Zudio", "Vishal Mega Mart", "Pantaloons"...
        "zudio",
        "vishal",
        "mall",
        "trends",
        "pantaloons",
        "lifestyle",
        "westside",
        "shoppers stop",
        "max fashion",
        "brand factory",
        "bazaar",
    ],
    "Entertainment": [
        "bookmyshow",
        "pvr",
        "inox",
        "cinema",
        "movie",
        "steam",
        "playstation",
        "xbox",
        "dream11",
        "mpl",
        "rummy",
        "gaming",
    ],
    "Utilities": [
        "jio",
        "airtel",
        "vodafone",
        "vi ",
        "bsnl",
        "tata power",
        "adani electricity",
        "bescom",
        "mseb",
        "torrent power",
        "electricity",
        "broadband",
        "act fibernet",
        "actfibernet",
        "hathway",
        "gas bill",
        "indane",
        "water bill",
        "recharge",
        "dth",
    ],
    "Health": [
        "pharmeasy",
        "1mg",
        "tata 1mg",
        "apollo",
        "netmeds",
        "practo",
        "cult",
        "cultfit",
        "medplus",
        "pharmacy",
        "hospital",
        "clinic",
        "diagnostic",
        "lab",
    ],
    "Travel": [
        "makemytrip",
        "goibibo",
        "ixigo",
        "cleartrip",
        "easemytrip",
        "indigo",
        "vistara",
        "air india",
        "spicejet",
        "oyo",
        "airbnb",
        "hotel",
        "irctc air",
    ],
    # Payments to an educational body usually mean fees/tuition — "XYZ University".
    "Education": [
        "university",
        "college",
        "institute",
        "vidyalaya",
        "vidhyalaya",
        "school",
        "tuition",
        "academy",
        "coaching",
        "polytechnic",
        "byju",
        "unacademy",
        "vedantu",
        "physics wallah",
        "pw ",
        "education",
        "campus",
    ],
    "Income": [
        "salary",
        "payroll",
        "neft cr",
        "imps cr",
        "interest",
        "dividend",
        "refund",
        "cashback",
        "credited",
    ],
    "Transfers": [
        "upi",
        "neft",
        "imps",
        "rtgs",
        "paytm",
        "phonepe",
        "google pay",
        "gpay",
        "bhim",
        "transfer",
        "self",
        "wallet",
        "razorpay",
        "cred",
    ],
    "Fees": [
        "fee",
        "charges",
        "charge",
        "gst",
        "penalty",
        "amc",
        "annual fee",
        "convenience fee",
        "overdraft",
    ],
}


def categorize_by_rules(merchant: str, description: str | None = None) -> str | None:
    """Return a category from keyword rules, or None if nothing matches."""
    text = f"{merchant} {description or ''}".lower()
    for category, keywords in RULES.items():
        for kw in keywords:
            if kw in text:
                return category
    return None


class MLCategorizer:
    """Lazy-trained Naive Bayes fallback over merchant text.

    Trained on demand from labelled examples (rule-derived + user corrections).
    Falls back to "Uncategorized" until there is enough signal to train.
    """

    MIN_EXAMPLES = 8

    def __init__(self) -> None:
        self._pipeline = None
        self._trained_on = 0

    def train(self, texts: list[str], labels: list[str]) -> bool:
        """Fit the classifier. Returns True if a model was trained."""
        # Need a few examples across at least two classes to be meaningful.
        if len(texts) < self.MIN_EXAMPLES or len(set(labels)) < 2:
            self._pipeline = None
            return False

        # Imported lazily so the app starts even before sklearn work is needed.
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.naive_bayes import MultinomialNB
        from sklearn.pipeline import Pipeline

        self._pipeline = Pipeline(
            [
                ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))),
                ("clf", MultinomialNB()),
            ]
        )
        self._pipeline.fit(texts, labels)
        self._trained_on = len(texts)
        return True

    @property
    def is_ready(self) -> bool:
        return self._pipeline is not None

    def predict(self, merchant: str, description: str | None = None) -> str | None:
        if self._pipeline is None:
            return None
        text = normalize_text(f"{merchant} {description or ''}")
        return str(self._pipeline.predict([text])[0])


def normalize_text(text: str) -> str:
    """Strip digits/punctuation noise so the model keys on merchant words."""
    text = text.lower()
    text = re.sub(r"[0-9]+", " ", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()
