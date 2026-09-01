"""Query normalization + fuzzy scoring.

Two traps, both confirmed against live platform data:
  1. token_set_ratio is case-sensitive -- "dolo 650" vs "Dolo 650mg Strip Of 15
     Tablets" scores 37 raw but 67 lowercased.
  2. Platforms punctuate differently. Apollo writes "Dolo-650", which tokenizes
     as ONE token and scores 42.9 (below the 55 cutoff) while the wrong product
     "Dolo 500" scores 66.7. Splitting on punctuation and separating letter/digit
     runs ("650mg" -> "650 mg") makes the exact match win everywhere.
"""
import re

from rapidfuzz import fuzz


def normalize(s: str) -> str:
    """Lowercase, split punctuation into token boundaries, separate
    letter/digit runs. Both sides of a comparison must pass through this."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)          # hyphens/apostrophes -> boundaries
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)    # 650mg -> 650 mg
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)    # dolo650 -> dolo 650
    return " ".join(s.split())


def score(query: str, title: str) -> float:
    """rapidfuzz token_set_ratio over normalized text. 0-100."""
    return fuzz.token_set_ratio(normalize(query), normalize(title))


# Generic retail words carry no identity: a result sharing only these is NOT the
# product. "cristello instant brightening face wash" vs "Smart and Handsome
# Instant Brightening Face Wash" scores 85 on token_set_ratio alone.
_GENERIC = {
    "tablet", "tablets", "tab", "capsule", "capsules", "cap", "strip", "strips",
    "syrup", "suspension", "injection", "cream", "gel", "ointment", "lotion",
    "drops", "solution", "powder", "sachet", "spray", "soap", "shampoo",
    "face", "wash", "facewash", "body", "skin", "hair", "oil", "serum",
    "mg", "ml", "gm", "g", "kg", "mcg", "iu", "pack", "of", "for", "with",
    "and", "the", "s", "x", "instant", "advance", "plus", "new", "daily",
    "gentle", "rich", "creamy", "brightening", "lightening", "whitening",
    "moisturising", "moisturizing", "nourishing", "anti", "pro", "max",
}


def key_tokens(query: str) -> list[str]:
    """The tokens that actually identify the product -- brand/molecule/strength.
    Pure digits count (650, 500); generic retail words don't."""
    toks = normalize(query).split()
    keep = [t for t in toks if t not in _GENERIC and (len(t) > 2 or t.isdigit())]
    return keep or toks  # never return empty: an all-generic query keeps its words


def identity_ok(query: str, title: str) -> bool:
    """True only if EVERY identifying token appears in the title.

    This is what stops 'Cristello ... Face Wash' matching a Glutafine product:
    token_set_ratio said 70, but the brand token is simply absent.
    """
    t = normalize(title)
    words = set(t.split())
    for tok in key_tokens(query):
        # digits must match a whole token (650 != 6500); words may be a prefix
        # so 'paracetamol' matches 'paracetamol' inside a longer title
        if tok.isdigit():
            if tok not in words:
                return False
        elif tok not in t:
            return False
    return True
