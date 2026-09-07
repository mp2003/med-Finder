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
from rapidfuzz.distance import Levenshtein


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


def _word_aligned(tok: str, title_words: list[str]) -> bool:
    """True when tok lines up with word boundaries in the title.

    normalize() turns "Lido-Plast" into "lido plast", so a user typing
    "lidoplast" fails a plain substring test -- the space sits inside the
    token. This glues adjacent words back together to recover the match.

    Two ways to align:
      1. tok is a prefix of ONE word     "paracetamol" in "paracetamol 500"
      2. tok spans whole words exactly   "lidoplast" == "lido" + "plast"

    (1) is the long-standing rule and is kept as-is; (2) is what this function
    adds. Note (1) also accepts "dolo" -> "Dologel", a different product -- it
    does so in the shipped gate today, and narrowing it would break brand
    prefixes generally. Ranking, not the gate, is what puts the exact "Dolo
    650" above it.

    What is NOT accepted is a multi-word run that overshoots a boundary, so
    the glued path cannot invent matches the prefix rule would have refused.
    """
    if any(w.startswith(tok) for w in title_words):
        return True
    for i in range(len(title_words)):
        run = ""
        for w in title_words[i:]:
            run += w
            if run == tok:
                return True
            if len(run) >= len(tok):
                break
    return False


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
        elif not _word_aligned(tok, t.split()):
            return False
    return True


def typo_ok(query: str, title: str) -> bool:
    """True when every identifying token is within one edit of a title word.

    identity_ok() demands exact containment, so a single mistyped letter --
    "BIODEMS-F" for the real "BIODENS-F" -- reports not-found on the very
    product the user wanted.

    Score cannot separate these: token_set_ratio rewards the shared generic
    words, so a wrong brand ("O3+ Brightening Face Wash" for a Cristello query)
    scores 91.3 while a real typo match scores 70.8. Edit distance on the
    IDENTIFYING tokens does separate them, because a typo is one letter off the
    brand while a wrong brand is a different word entirely.

    Digits must still match exactly: 650 and 500 are one edit apart but are
    different products.
    """
    words = set(normalize(title).split())
    for tok in key_tokens(query):
        if tok.isdigit():
            if tok not in words:
                return False
        elif not any(Levenshtein.distance(tok, w) <= 1 for w in words):
            return False
    return True


def respell(query: str, titles: list[str]) -> str | None:
    """The query rewritten using the platforms' own spelling, or None.

    "paracitamol" comes back from 1mg as "Paracetamol 500mg Tablet" -- the
    right product, but only 1mg's search was forgiving enough to return it, so
    the other six report not-stocked. Correcting the query and re-asking widens
    that: "crocine" reaches 2 results, "crocin" reaches 15.

    The correction is taken from titles the platforms actually returned, never
    invented, and only at edit distance 1 -- the same bar typo_ok already uses
    to call something a typo rather than a different brand. Digits are left
    alone, so 650 never becomes 500.

    This only widens the SEARCH. Whether a result is shown as an exact match or
    a flagged near-match is still typo_ok's call against the original query,
    because "lasix" and "lanix" are one edit apart and are different drugs.
    """
    words = {w for t in titles for w in normalize(t).split()}
    out, changed = [], False
    for tok in key_tokens(query):
        if tok.isdigit() or tok in words:
            out.append(tok)
            continue
        near = [(Levenshtein.distance(tok, w), w) for w in words]
        near = [(d, w) for d, w in near if d == 1]
        if not near:
            return None            # nothing close: do not guess
        # Ties are decided alphabetically only to stay deterministic; a real
        # tie means two brands are both one edit away and neither is safe to
        # prefer, which is why the result still faces typo_ok.
        out.append(min(near)[1])
        changed = True
    return " ".join(out) if changed else None


def demo():
    """Self-check for the identity gate. Run: python -m matching

    The glued fallback is the risky part -- it deliberately relaxes a safety
    check -- so every near-miss brand that must still be REJECTED is asserted
    here alongside the ones that must now pass.
    """
    ok = [("lidoplast", "Lido-Plast Lidocaine 350mg Patch"),
          ("betadinegargle", "Betadine Gargle Mint"),
          ("ecosprinav", "Ecosprin-AV 75 Capsule"),
          ("ecosprin", "Ecosprin 75 Tablet"),
          ("liv52", "Himalaya Liv. 52 DS Tablet"),
          ("dolo", "Dolo 650 Tablet"),
          ("dolo650", "Dolo 650 Tablet"),
          ("crocin", "Crocin Advance 500"),
          ("volini", "Volini Gel"),
          ("shelcal", "Shelcal 500 Tablet"),
          ("zincovit", "Zincovit SF Liquid"),
          ("pan40", "PAN 40 Tablet")]
    # Wrong products that must stay rejected. Brand-prefix hits such as
    # "dolo" -> "Dologel" are NOT here: the shipped gate has always accepted
    # those via the prefix rule, and ranking is what demotes them.
    bad = [("dolo650", "Dolo 500 Tablet"),
           ("lidoplast", "Lidocaine Gel"),
           ("lidoplast", "Ketolin Alsi Lep Anti-Plast (100 g)"),
           ("cristello", "O3+ Brightening Face Wash")]
    for q, t in ok:
        assert identity_ok(q, t), f"rejected a real match: {q!r} vs {t!r}"
    for q, t in bad:
        assert not identity_ok(q, t), f"ACCEPTED A WRONG PRODUCT: {q!r} vs {t!r}"

    # respell(): fix from the platform's own titles, never invent.
    tt = ["Paracetamol 500mg Tablet", "Crocin 650 Tablets"]
    assert respell("paracitamol", tt) == "paracetamol"
    assert respell("crocine", tt) == "crocin"
    assert respell("paracetamol", tt) is None, "rewrote an already-correct query"
    assert respell("zzzzzzz", tt) is None, "guessed at an unrelated query"
    # Digits are never respelt: 650 and 500 are one edit apart.
    assert respell("dolo 650", ["Dolo 500 Tablet"]) is None

    # A look-alike drug one edit away may widen the SEARCH, but must never be
    # presented as the confident match: lasix/lanix are different drugs.
    assert respell("lasix", ["Lanix Syrup"]) == "lanix"
    assert not identity_ok("lasix", "Lanix Syrup"), "look-alike shown as exact"
    assert identity_ok("lasix", "Lasix 40mg Tablet"), "real match demoted"

    # The glued path must never accept a run that overshoots a word boundary.
    assert not identity_ok("lidoplas", "Lido-Plast Patch"), "overshot a boundary"
    # A digit token must still match whole, or 650 starts matching 6500.
    assert not identity_ok("dolo 650", "Dolo 6500 Tablet")
    # typo_ok stays independent of the glued path.
    assert typo_ok("biodems f", "BIODENS-F Tablet")
    print(f"  ok  {len(ok)} matches accepted, {len(bad)} wrong products rejected")
    print("matching OK")


if __name__ == "__main__":
    demo()
