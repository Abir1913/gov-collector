"""Turn a title plus extracted text into structured fields: type, sector, vacancies, tender id/method/value,
education, district, deadline, confidence. Rules are bilingual (English/Bangla) and deliberately conservative:
when unsure, fields stay empty and the notice is flagged for review rather than guessed."""
import re

from .textutil import norm, squash, find_deadline, find_district, district_from_host, lang, parse_dates

TENDER_RE = re.compile(r"\b(tender|e-?gp|rfq|rfp|eoi|quotation|bidding|procurement|ltm|otm|ostetm|ostem)\b|দরপত্র|টেন্ডার|কোটেশন|ক্রয়|সরবরাহ", re.I)
JOB_RE = re.compile(r"recruit|vacanc|job circular|appointment|walk-?in|career|applications? (?:are|is) invited|apply (?:online|by)|"
                    r"posts? of|নিয়োগ|পদে|চাকরি|জনবল", re.I)
EXAM_RE = re.compile(r"admission|result|routine|exam|ভর্তি|ফলাফল|পরীক্ষা|সময়সূচি", re.I)

SECTORS = [
    ("Engineering", r"engineer|প্রকৌশল"),
    ("Health", r"doctor|medical officer|nurse|health|hospital|চিকিৎসক|স্বাস্থ্য|নার্স|হাসপাতাল"),
    ("Education", r"teacher|lecturer|professor|school|college|universit|শিক্ষক|প্রভাষক|বিদ্যালয়|কলেজ|বিশ্ববিদ্যালয়|\bssc\b|\bhsc\b"),
    ("Security", r"police|ansar|constable|sub-?inspector|fire service|border guard|পুলিশ|আনসার|কনস্টেবল|বিজিবি|ফায়ার"),
    ("Banking", r"\bbank\b|insurance|ব্যাংক|বিমা"),
    ("ICT", r"\bict\b|software|computer|network|server|programmer|আইসিটি|কম্পিউটার"),
    ("Works", r"road|bridge|construction|building|repair|renovation|নির্মাণ|সড়ক|সেতু|মেরামত|ভবন"),
    ("Tax", r"\btax\b|\bvat\b|customs|কর\b|শুল্ক|ভ্যাট"),
    ("Agriculture", r"agricultur|farm|fisher|livestock|কৃষি|মৎস্য|প্রাণিসম্পদ"),
    ("Transport", r"railway|vehicle|transport|port authority|রেল|যানবাহন"),
    ("Energy", r"electric|power|\bgas\b|energy|বিদ্যুৎ|জ্বালানি"),
]
SECTORS = [(n, re.compile(p, re.I)) for n, p in SECTORS]

EDU = [("SSC", r"\bssc\b|এসএসসি|মাধ্যমিক|secondary school certificate|class (?:viii|8|ix|9|x|10)"),
       ("HSC", r"\bhsc\b|এইচএসসি|উচ্চ মাধ্যমিক|higher secondary|intermediate"),
       ("Diploma", r"diploma|ডিপ্লোমা"),
       ("Bachelor", r"bachelor|graduat|degree|স্নাতক|\bb\.?sc\b|\bb\.?a\b|\bbba\b|\bmbbs\b|\bbds\b"),
       ("Master", r"master|post-?graduat|স্নাতকোত্তর|\bm\.?sc\b|\bmba\b")]
EDU = [(n, re.compile(p, re.I)) for n, p in EDU]

METHODS = [("OTM", r"\botm\b|open tendering"), ("LTM", r"\bltm\b|limited tendering"), ("RFQ", r"\brfq\b|request for quotation|quotation"),
           ("OSTETM", r"\bostetm\b|\bostem\b|one stage two envelope"), ("DPM", r"\bdpm\b|direct procurement")]
METHODS = [(n, re.compile(p, re.I)) for n, p in METHODS]

ORG_BY_TYPE = {"ministry": "Ministry", "division": "Ministry", "executive_office": "Executive", "directorate": "Directorate",
               "authority": "Autonomous body", "board": "Autonomous body", "commission": "Autonomous body",
               "council": "Autonomous body", "institute": "Institute", "academy": "Institute", "trust": "Autonomous body",
               "corporation": "State enterprise", "company": "State enterprise", "bank_fin": "Bank", "edu_board": "Education",
               "edu_institution": "Education", "geo_district": "Local government", "geo_division": "Local government",
               "city_corporation": "Local government", "office": "Government office", "foundation": "Autonomous body"}


def classify(title, text, hint=None, tender_id=None):
    head = norm(title) + " . " + norm(text)[:800]
    t = len(TENDER_RE.findall(norm(title))) * 3 + len(TENDER_RE.findall(norm(text)[:800]))
    j = len(JOB_RE.findall(norm(title))) * 3 + len(JOB_RE.findall(norm(text)[:800]))
    if hint == "tender":
        t += 2
    if hint == "job":
        j += 2
    if tender_id:
        t += 3
    if t == 0 and j == 0:
        return "notice"
    if EXAM_RE.search(norm(title)) and j <= 3 and t <= 3:
        return "notice"
    return "tender" if t > j else "job"


def sector_of(title, text, default):
    probe = norm(title) + " " + norm(text)[:600]
    for name, rx in SECTORS:
        if rx.search(probe):
            return name
    return default or "General"


def education(text):
    t = norm(text)
    found = [n for n, rx in EDU if rx.search(t)]
    return found[0] if found else ""


def vacancies(text):
    t = norm(text)
    pats = [r"(?:total|মোট)\D{0,20}?(\d[\d,]*)\s*(?:টি|জন)?\s*(?:posts?|vacanc\w*|পদ)",
            r"(\d[\d,]*)\s*(?:টি\s*)?(?:posts?|vacancies|vacancy|পদে|পদ)\b",
            r"(?:posts?|vacancies|পদ সংখ্যা)\s*[:\-]?\s*(\d[\d,]*)"]
    best = 0
    for p in pats:
        for m in re.finditer(p, t, re.I):
            try:
                v = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 0 < v < 100000:
                best = max(best, v) if p is pats[0] else (best or v)
        if best:
            break
    return best


def tender_id(text):
    m = re.search(r"(?:tender|e-?gp)\s*(?:id|no\.?|number)\s*[:\-]?\s*(\d{4,9})|টেন্ডার\s*(?:আইডি|নং)\s*[:\-]?\s*(\d{4,9})", norm(text), re.I)
    return (m.group(1) or m.group(2)) if m else ""


def tender_method(text):
    t = norm(text)
    for name, rx in METHODS:
        if rx.search(t):
            return name
    return ""


def tender_value_m(text):
    t = norm(text)
    m = re.search(r"(?:estimated|official|approximate)\s*(?:cost|value)[^\d]{0,40}([\d,]+(?:\.\d+)?)\s*(lakh|lac|crore|million)?", t, re.I)
    if not m:
        m = re.search(r"(?:প্রাক্কলিত|আনুমানিক)\s*(?:মূল্য|ব্যয়)[^\d]{0,30}([\d,]+(?:\.\d+)?)\s*(লক্ষ|কোটি)?", t)
    if not m:
        return 0.0
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    unit = (m.group(2) or "").lower()
    if unit in ("lakh", "lac", "লক্ষ"):
        return round(v * 0.1, 3)
    if unit in ("crore", "কোটি"):
        return round(v * 10, 3)
    if unit == "million":
        return round(v, 3)
    return round(v / 1e6, 3) if v >= 1e5 else round(v, 3)


def age_limit(text):
    m = re.search(r"(?:age|বয়স)[^\d]{0,40}(\d{2})\s*(?:years|yrs|বছর)", norm(text), re.I)
    return int(m.group(1)) if m else 0


def fee(text):
    m = re.search(r"(?:fee|ফি)[^\d]{0,30}(?:tk\.?|taka|৳|টাকা)?\s*([\d,]{2,7})", norm(text), re.I)
    return int(m.group(1).replace(",", "")) if m else 0


def parse(title, text, src, hint=None, listing_date=None, today=None):
    """src: dict with sector, entity_type, host, dom, district, name. Returns notice dict (without fingerprint)."""
    text = text or ""
    tid = tender_id(text)
    typ = classify(title, text, hint, tid)
    full = norm(title) + " . " + norm(text)
    deadline = find_deadline(full)
    pubs = parse_dates(norm(text)[:700]) if text else []
    published = listing_date or (pubs[0][1] if pubs else None)
    if deadline and published and deadline < published:
        deadline = None  # a "deadline" before publication is a misparse
    edu = education(norm(text)[:3000]) if typ == "job" else ""
    vac = vacancies(full) if typ == "job" else 0
    method = tender_method(full) if typ == "tender" else ""
    val = tender_value_m(full) if typ == "tender" else 0.0
    district = find_district(full) or src.get("district") or district_from_host(src.get("host", ""))
    sector = sector_of(title, text, src.get("sector"))
    org = ORG_BY_TYPE.get(src.get("entity_type", ""), "Government office")
    summ = squash(text)[:280] or squash(title)

    c = 0.25
    c += 0.1 if len(squash(title)) > 12 else 0
    c += 0.25 if len(squash(text)) > 150 else 0
    if typ in ("job", "tender"):
        c += 0.25 if deadline else 0
        c += 0.15 if (vac or tid or method) else 0
    else:
        c += 0.2 if (published or deadline) else 0
    conf = round(min(c, 1.0), 2)
    return {"type": typ, "title": squash(title)[:240], "agency": src.get("name", ""), "host": src.get("host", ""),
            "sector": sector, "org": org, "edu": edu, "district": district, "method": method, "val": val, "vac": vac,
            "deadline": deadline.isoformat() if deadline else None, "published": published.isoformat() if published else None,
            "summary": summ, "ref": tid, "dom": src.get("dom", "gov"), "confidence": conf, "lang": lang(title + " " + text),
            "extra": {k: v for k, v in {"age_limit": age_limit(text), "fee": fee(text)}.items() if v}}
