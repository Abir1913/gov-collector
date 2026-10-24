"""Generic extraction of notice rows from the very different CMSs used across .gov.bd sites.

No per-site parsers are needed for the common cases: a notice row is "a link with a title, usually a date, often a
document". Rows are scored; weak candidates are dropped. Feeds (RSS/Atom) are preferred when a site offers them.
"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .textutil import squash, parse_dates, norm

DOC_EXT = (".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".xls", ".xlsx", ".zip", ".odt")
IMG_EXT = (".jpg", ".jpeg", ".png")
GENERIC = {"details", "detail", "more", "read more", "click here", "view", "download", "pdf", "view details", "here",
           "বিস্তারিত", "আরও", "আরো", "ডাউনলোড", "দেখুন", "এখানে ক্লিক করুন", "বিস্তারিত দেখুন", "pdf ডাউনলোড"}
NAVWORDS = {"home", "about", "contact", "login", "sitemap", "faq", "gallery", "হোম", "যোগাযোগ", "লগইন", "সাইটম্যাপ", "গ্যালারি"}
KEY = re.compile(r"notice|circular|recruit|vacanc|job|tender|quotation|admission|result|exam|routine|schedule|announce|"
                 r"invit|appointment|walk-?in|নোটিশ|বিজ্ঞপ্তি|নিয়োগ|দরপত্র|পরিপত্র|প্রজ্ঞাপন|ফলাফল|ভর্তি|পরীক্ষা|সময়সূচি|আদেশ|আমন্ত্রণ|চাকরি", re.I)
NAVCLS = re.compile(r"menu|navbar|nav-|breadcrumb|footer|pagination|social", re.I)
ROWCLS = re.compile(r"notice|item|post|card|list|row|news|entry|result", re.I)
NEXT_TXT = {"next", "next »", "next page", "›", "»", "পরবর্তী", ">", "older"}


def is_doc(url):
    p = urlparse(url).path.lower()
    return p.endswith(DOC_EXT)


def _in_nav(a):
    for p in a.parents:
        if p.name in ("nav", "footer", "header"):
            return True
        cls = " ".join(p.get("class", [])) + " " + (p.get("id") or "")
        if cls.strip() and NAVCLS.search(cls) and p.name != "table":
            return True
    return False


def _container(a):
    for p in a.parents:
        if p.name in ("tr", "li", "article"):
            return p
        if p.name == "div" and ROWCLS.search(" ".join(p.get("class", []))):
            return p
        if p.name in ("body", "html", "table", "ul", "ol", "main", "section"):
            break
    return a.parent


def extract_listing(html, base_url, hint=None):
    """Return (candidates, next_url). Candidate = dict(title,url,date,doc,score,hint)."""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    cands, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if not url.startswith("http") or url in seen:
            continue
        if _in_nav(a):
            continue
        text = squash(a.get_text(" "))
        cont = _container(a)
        ctext = squash(cont.get_text(" ")) if cont is not None else text
        if len(ctext) > 500:
            ctext = squash(a.parent.get_text(" ")) if a.parent is not None else text
        low = text.lower()
        if low in NAVWORDS:
            continue
        generic = low in GENERIC or len(text) < 8
        title = ctext if generic else text
        title = squash(re.sub(r"(?i)\b(details|view|download|বিস্তারিত|ডাউনলোড)\b", "", title))[:240]
        if len(title) < 8:
            continue
        dates = parse_dates(ctext)
        date = dates[0][1] if dates else None
        doc = is_doc(url)
        score = 0
        score += 2 if date else 0
        score += 2 if doc else 0
        score += 1 if KEY.search(title) else 0
        score += 1 if (cont is not None and cont.name in ("tr", "li", "article")) else 0
        score += 1 if hint else 0
        if score < 3:
            continue
        seen.add(url)
        cands.append({"title": title, "url": url, "date": date, "doc": doc, "score": score, "hint": hint})
    return cands, _next_link(soup, base_url)


def _next_link(soup, base_url):
    l = soup.find("link", rel=lambda v: v and "next" in v)
    if l and l.get("href"):
        return urljoin(base_url, l["href"])
    for a in soup.find_all("a", href=True):
        rel = a.get("rel") or []
        txt = squash(a.get_text(" ")).lower()
        if "next" in rel or txt in NEXT_TXT:
            u = urljoin(base_url, a["href"])
            if u != base_url and urlparse(u).netloc == urlparse(base_url).netloc and not u.endswith("#"):
                return u
    return None


def html_text(html, limit=20000):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "form"]):
        t.decompose()
    node = soup.find("main") or soup.find("article") or soup.find(id=re.compile("content|main", re.I)) or \
        soup.find(class_=re.compile("content|details|notice-body|post", re.I)) or soup.body or soup
    return squash(node.get_text(" "))[:limit]


def doc_links(html, base_url):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        u = urljoin(base_url, a["href"]).split("#")[0]
        if is_doc(u) and u not in out:
            out.append(u)
    return out


def find_feeds(html, base_url):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for l in soup.find_all("link", href=True):
        t = (l.get("type") or "").lower()
        if "rss" in t or "atom" in t:
            out.append(urljoin(base_url, l["href"]))
    return out


def parse_feed(xml_text, base_url):
    soup = BeautifulSoup(xml_text, "xml")
    out = []
    for it in soup.find_all(["item", "entry"]):
        link = it.find("link")
        url = (link.get("href") if link is not None and link.get("href") else link.get_text() if link is not None else "").strip()
        title = squash(it.find("title").get_text()) if it.find("title") else ""
        d = it.find(["pubDate", "published", "updated", "dc:date"])
        dates = parse_dates(d.get_text()) if d else []
        if not dates and d:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d.get_text().strip())
            if m:
                dates = parse_dates("%s-%s-%s" % m.groups())
        if url and title:
            out.append({"title": title, "url": urljoin(base_url, url), "date": dates[0][1] if dates else None,
                        "doc": is_doc(url), "score": 5, "hint": None})
    return out
