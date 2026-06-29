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
    "Income",
    "Transfers",
    "Fees",
    "Uncategorized",
]

# Keyword -> category. Matched case-insensitively as whole-ish substrings against
# the merchant + description text. Ordering doesn't matter; first match wins.
RULES: dict[str, list[str]] = {
    "Groceries": ["walmart", "whole foods", "aldi", "kroger", "safeway", "trader joe", "grocery", "supermarket"],
    "Dining": ["starbucks", "mcdonald", "chipotle", "restaurant", "cafe", "coffee", "pizza", "doordash", "uber eats", "grubhub"],
    "Transport": ["uber", "lyft", "shell", "chevron", "exxon", "gas station", "parking", "transit", "metro"],
    "Subscriptions": ["netflix", "spotify", "hulu", "disney+", "youtube premium", "amazon prime", "icloud", "dropbox", "adobe", "notion"],
    "Shopping": ["amazon", "target", "best buy", "ebay", "etsy", "ikea", "nike"],
    "Entertainment": ["cinema", "movie", "steam", "playstation", "xbox", "concert", "ticketmaster"],
    "Utilities": ["electric", "water bill", "comcast", "verizon", "at&t", "t-mobile", "internet", "utility"],
    "Health": ["pharmacy", "cvs", "walgreens", "clinic", "hospital", "gym", "fitness"],
    "Travel": ["airlines", "delta", "united", "hotel", "airbnb", "expedia", "booking.com"],
    "Income": ["payroll", "salary", "deposit", "direct dep", "interest"],
    "Transfers": ["transfer", "zelle", "venmo", "paypal", "wire"],
    "Fees": ["fee", "overdraft", "service charge", "atm"],
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
