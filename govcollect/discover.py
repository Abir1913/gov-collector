"""Find the pages on a government site that list notices, recruitment circulars and tenders."""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .extract_html import extract_listing, find_feeds, is_doc
from .textutil import squash

KINDS = [
    ("tender", re.compile(r"tender|e-?gp|procurement|quotation|rfq|দরপত্র|টেন্ডার|ক্রয়", re.I)),
    ("job", re.compile(r"recruit|vacanc|career|\bjobs?\b|নিয়োগ|চাকরি|চাকুরি|জনবল", re.I)),
    ("notice", re.compile(r"notice|circular|announce|প্রজ্ঞাপন|নোটিশ|বিজ্ঞপ্তি|পরিপত্র|আদেশ", re.I)),
]
# Used only when the homepage reveals nothing; tried sparingly.
FALLBACK_PATHS = ["/site/view/notices", "/notice", "/notices", "/en/notices", "/site/notices"]
SKIP_TEXT = re.compile(r"login|sign in|register|facebook|twitter|youtube|linkedin|সাইটম্যাপ", re.I)


def _same_family(a, b):
    ha, hb = urlparse(a).netloc.split(":")[0], urlparse(b).netloc.split(":")[0]
    if ha == hb:
        return True
    ra, rb = ".".join(ha.split(".")[-3:]), ".".join(hb.split(".")[-3:])
    return ra == rb or hb.endswith(".gov.bd") or hb.endswith(".portal.gov.bd")


def find_listings(html, base_url):
    """Return [(url, kind, via)] ordered by usefulness: feeds first, then nav links by kind."""
    out, seen = [], set()
    for f in find_feeds(html, base_url):
        if f not in seen:
            seen.add(f)
            out.append((f, "feed", "autodiscovery"))
    soup = BeautifulSoup(html, "lxml")
    per_kind = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if url in seen or is_doc(url) or not url.startswith("http") or not _same_family(base_url, url):
            continue
        text = squash(a.get_text(" "))
        probe = text + " " + urlparse(url).path
        if SKIP_TEXT.search(text) or len(text) > 80:
            continue
        for kind, rx in KINDS:
            if rx.search(probe):
                if per_kind.get(kind, 0) >= 4:
                    break
                per_kind[kind] = per_kind.get(kind, 0) + 1
                seen.add(url)
                out.append((url, kind, "nav:" + text[:40]))
                break
    return out


def homepage_is_listing(html, base_url):
    cands, _ = extract_listing(html, base_url)
    return len(cands) >= 3
