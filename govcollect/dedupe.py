"""Fingerprints and near-duplicate merging. The same circular often appears on the agency site, a district portal copy
and a mirror; those collapse into one notice with several sources."""
import datetime as dt
import difflib
import hashlib
import re

from .textutil import norm

_JUNK = re.compile(r"[^\w\u0980-\u09FF ]+", re.U)
_STOP = {"the", "of", "for", "and", "a", "an", "in", "to", "notice", "circular", "রিক্রুটমেন্ট", "বিজ্ঞপ্তি", "নোটিশ", "demo"}


def norm_title(t):
    t = _JUNK.sub(" ", norm(t).lower())
    return " ".join(w for w in t.split() if w not in _STOP)


def fingerprint(n):
    if n.get("type") == "tender" and n.get("ref"):
        return "tender:" + n["ref"]
    key = norm_title(n["title"]) + "|" + (n.get("deadline") or "")
    return "n:" + hashlib.sha1(key.encode("utf8")).hexdigest()[:20]


def _near(a, b, limit=0.9):
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= limit


def find_duplicate(store, n):
    """Return the fingerprint of an existing near-identical notice, or None."""
    nt = norm_title(n["title"])
    if len(nt) < 12:
        return None
    rows = store.q("SELECT fingerprint,title,deadline,published FROM notices WHERE type=? ORDER BY first_seen DESC LIMIT 1500",
                   (n["type"],))
    for r in rows:
        if n.get("deadline") and r["deadline"] and r["deadline"] != n["deadline"]:
            continue
        if not n.get("deadline") and not r["deadline"] and n.get("published") and r["published"]:
            try:
                if abs((dt.date.fromisoformat(n["published"]) - dt.date.fromisoformat(r["published"])).days) > 7:
                    continue
            except ValueError:
                pass
        if _near(nt, norm_title(r["title"])):
            return r["fingerprint"]
    return None
