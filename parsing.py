"""Pull item names out of an ordinary human order message.

Orders arrive as WhatsApp-style prose or forwarded lists, mixing chatter with
the actual items:

    sir can u please order dolo , eno antacids and knee protextor
    please order 1. Dolo 650 2. Eno sachet 3. Knee cap

Deliberately NOT a clinical NER model. Med7/scispaCy/BioBERT are trained on
discharge summaries in medical English with US/UK drug names; this input is
Indian brand names ("Vigoquin", "Amplinak"), pharmacy shorthand ("E/d.") and
conversation. The problem is separating chatter from item lines, which a word
ratio solves in a few lines and with no dependency.
"""
import re

# Words that carry a request rather than name a product. A line made mostly of
# these is chatter. Kept deliberately small: over-listing starts eating real
# product words.
CHATTER = {
    "can", "we", "you", "u", "please", "pls", "sir", "madam", "order",
    "arrange", "kindly", "need", "want", "send", "get", "this", "that",
    "these", "and", "along", "with", "for", "me", "the", "a", "an", "also",
    "each", "qty", "qnty", "nos", "pcs", "hi", "hello", "thanks", "thank",
    "good", "morning", "evening", "afternoon", "available", "stock", "urgent",
    "asap", "one", "is", "are", "it", "my", "our", "do", "have",
}

# A request preamble to strip before splitting inline prose on commas.
_PREAMBLE = re.compile(r"^.*?\b(order|arrange|send|need|want|get)\b\s*", re.I)
_NUMBERED = re.compile(r"\s*\d+\s*[.)]\s*")
# Anchored: only a leading "1." is a list marker. Unanchored it would eat the
# "0." out of "Vigoquin 0.5%" and leave "Vigoquin5%".
_LEAD_NUM = re.compile(r"^\s*\d+\s*[.)]\s+")
_LIST_SEP = re.compile(r"\s*(?:,|\band\b|&)\s*", re.I)

# Percentage strengths ("0.5%") anywhere in the line, plus the dosage-form
# shorthand that trails them. Only stripped for SEARCHING -- identity_ok()
# demands digits match a whole token, so "Vigoquin 0.5%" matches "Vigoquin 0.5%
# Eye Drop" but FAILS the equally correct "Vigoquin Eye Drops". Verified live.
# Note this deliberately does NOT touch bare numbers: "Dolo 650" keeps its 650,
# because there the digits are part of the brand.
_STRENGTH = re.compile(r"\s*\d+(?:\.\d+)?\s*%")
_FORM = re.compile(r"\s*\b(?:e/?d|eye\s*drops?)\b\.?\s*$", re.I)

MAX_ITEMS = 10   # a 70-call fan-out is a mistake, not an order


def split_items(text: str) -> list[str]:
    """Cut a message into candidate item strings.

    Three shapes, in order of how confidently they can be told apart:
    several lines, one line of "1. X 2. Y", or one line of prose.
    """
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    if len(lines) != 1:
        return lines
    s = lines[0]
    if _NUMBERED.search(s) and re.search(r"\d\s*[.)]\s*\S", s):
        return _NUMBERED.split(s)
    # Inline prose: drop "sir can u please order" then split on , / and / &
    return _LIST_SEP.split(_PREAMBLE.sub("", s))


def clean_item(item: str) -> str | None:
    """Normalise one candidate, or None when it is chatter, not a product."""
    s = _LEAD_NUM.sub("", item or "").strip().strip(".,").strip()
    if not s or s.startswith("@"):      # @mentions are not products
        return None
    words = re.findall(r"[a-zA-Z]+", s)
    if not words:                       # bare quantities, emoji, punctuation
        return None
    if sum(w.lower() in CHATTER for w in words) / len(words) >= 0.6:
        return None
    return re.sub(r"\s{2,}", " ", s)


def search_term(item: str) -> str:
    """What to actually search for -- see _STRENGTH on why this differs.

    The display list keeps the original line so the user can still see the
    strength they asked for and check it against the result.
    """
    s = _FORM.sub("", _STRENGTH.sub("", item))
    return s.strip().strip(".,").strip() or item


def extract_items(text: str) -> list[str]:
    """Item names from an order message, in the order written.

    Returns [] when the message names nothing (a bare greeting), and one entry
    for an ordinary single-product search, so callers can treat "1 item" as
    today's behaviour.
    """
    out, seen = [], set()
    for raw in split_items(text):
        c = clean_item(raw)
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out[:MAX_ITEMS]


def demo():
    """Self-check. Run: python -m parsing"""
    cases = [
        ("sir can u please order dolo , eno antacids and knee protextor",
         ["dolo", "eno antacids", "knee protextor"]),
        ("can u please order 1. X , 2. Y , 3. Z", ["X", "Y", "Z"]),
        ("please order 1. Dolo 650 2. Eno sachet 3. Knee cap",
         ["Dolo 650", "Eno sachet", "Knee cap"]),
        ("@drjaydoshi\nCan we arrange this one 1 qty sir\nAlong with this\n"
         "Vigoquin 0.5%. E/d.\nAmplinak     E/d.\nAlthrocin drops\neach 1 qnty",
         ["Vigoquin 0.5%. E/d", "Amplinak E/d", "Althrocin drops"]),
        ("dolo 650", ["dolo 650"]),
        ("hi sir good morning", []),
        ("", []),
    ]
    for text, want in cases:
        got = extract_items(text)
        assert got == want, f"{text!r}\n  got  {got}\n  want {want}"
        first = (text.splitlines() or ["(empty)"])[0]
        print(f"  ok  {first[:38]:40} -> {got}")

    # Strength is dropped for the SEARCH but the display line keeps it.
    terms = [
        ("Vigoquin 0.5%. E/d", "Vigoquin"),   # both strength and form go
        ("Amplinak E/d", "Amplinak"),
        ("Althrocin drops", "Althrocin drops"),
        ("Dolo 650", "Dolo 650"),             # brand digits must survive
    ]
    for item, want in terms:
        got = search_term(item)
        assert got == want, f"search_term({item!r}) -> {got!r}, want {want!r}"
        print(f"  ok  search {item!r:24} -> {got!r}")
    print("parsing OK")


if __name__ == "__main__":
    demo()
