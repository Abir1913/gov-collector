"""Bangla/English text helpers: digits, dates, deadlines, language, garbled-font detection, districts."""
import re
import unicodedata
import datetime as dt

BN_DIG = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def norm(s):
    return unicodedata.normalize("NFC", (s or "").translate(BN_DIG))


def squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


EN_M = "january february march april may june july august september october november december".split()
MONTHS = {}
for _i, _m in enumerate(EN_M, 1):
    MONTHS[_m] = _i
    MONTHS[_m[:3]] = _i
MONTHS["sept"] = 9
_BN = [("জানুয়ারি", "জানুয়ারী"), ("ফেব্রুয়ারি", "ফেব্রুয়ারী"), ("মার্চ",), ("এপ্রিল",), ("মে",), ("জুন",), ("জুলাই",),
       ("আগস্ট", "আগষ্ট"), ("সেপ্টেম্বর",), ("অক্টোবর",), ("নভেম্বর",), ("ডিসেম্বর",)]
for _i, _names in enumerate(_BN, 1):
    for _n in _names:
        MONTHS[unicodedata.normalize("NFC", _n)] = _i
_MALT = "|".join(re.escape(k) for k in sorted(MONTHS, key=len, reverse=True))

P_DMY = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4}|\d{2})(?!\d)")
P_YMD = re.compile(r"(?<!\d)(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)")
P_DMONY = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s,\-]*(" + _MALT + r")[\s,\.\-]*(\d{4})", re.I)
P_MONDY = re.compile(r"\b(" + _MALT + r")\.?\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})", re.I)


def _mk(y, m, d):
    try:
        y = int(y)
        if y < 100:
            y += 2000
        if not 2000 <= y <= 2100:
            return None
        return dt.date(y, int(m), int(d))
    except ValueError:
        return None


def parse_dates(text):
    """Return [(position, date)] sorted by position. Day-first, as in Bangladesh."""
    t = norm(text)
    out, spans = [], []

    def add(m, d):
        if d and not any(m.start() < e and s < m.end() for s, e in spans):
            spans.append((m.start(), m.end()))
            out.append((m.start(), d))

    for m in P_DMONY.finditer(t):
        add(m, _mk(m.group(3), MONTHS.get(m.group(2).lower(), 0), m.group(1)))
    for m in P_MONDY.finditer(t):
        add(m, _mk(m.group(3), MONTHS.get(m.group(1).lower(), 0), m.group(2)))
    for m in P_YMD.finditer(t):
        add(m, _mk(m.group(1), m.group(2), m.group(3)))
    for m in P_DMY.finditer(t):
        add(m, _mk(m.group(3), m.group(2), m.group(1)))
    return sorted(out)


DL_KW = re.compile(r"(last\s*date|closing(?:\s*date)?|deadline|last\s*selling|due\s*date|on\s*or\s*before|not\s*later\s*than|"
                   r"submission\s*date|আবেদনের\s*শেষ|শেষ\s*তারিখ|শেষ\s*সময়|সর্বশেষ|শেষ\s*দিন)", re.I)
DL_AFTER = re.compile(r"(তারিখের\s*মধ্যে|তারিখ\s*পর্যন্ত|তারিখের\s*মধ্যেই)")


def find_deadline(text):
    t = norm(text)
    ds = parse_dates(t)
    if not ds:
        return None
    found = []
    for m in DL_KW.finditer(t):
        for pos, d in ds:
            if m.end() - 5 <= pos <= m.end() + 160:
                found.append((m.group(0).lower(), d))
                break
    for m in DL_AFTER.finditer(t):
        cand = [d for pos, d in ds if m.start() - 40 <= pos <= m.start()]
        if cand:
            found.append(("after", cand[-1]))
    if not found:
        return None
    clos = [d for k, d in found if "clos" in k]
    return max(clos) if clos else max(d for _, d in found)


BN_RANGE = re.compile(r"[\u0980-\u09FF]")
LEGACY = set("†‡¤¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ")


def has_bangla(s):
    return bool(BN_RANGE.search(s or ""))


def garbled(s):
    """Legacy-font (Bijoy/SutonnyMJ-style) text extracted from PDFs looks like Latin-1 noise with no Unicode Bangla."""
    s = s or ""
    if len(s) < 40 or has_bangla(s):
        return False
    return sum(c in LEGACY for c in s) / max(1, len(s)) > 0.04


def lang(s):
    letters = [c for c in (s or "") if c.isalpha()]
    if not letters:
        return "und"
    return "bn" if sum(bool(BN_RANGE.match(c)) for c in letters) / len(letters) > 0.3 else "en"


DISTRICTS = [l.split("|") for l in """Dhaka|ঢাকা
Faridpur|ফরিদপুর
Gazipur|গাজীপুর
Gopalganj|গোপালগঞ্জ
Kishoreganj|কিশোরগঞ্জ
Madaripur|মাদারীপুর
Manikganj|মানিকগঞ্জ
Munshiganj|মুন্সীগঞ্জ
Narayanganj|নারায়ণগঞ্জ
Narsingdi|নরসিংদী
Rajbari|রাজবাড়ী
Shariatpur|শরীয়তপুর
Tangail|টাঙ্গাইল
Bagerhat|বাগেরহাট
Chuadanga|চুয়াডাঙ্গা
Jashore|যশোর
Jhenaidah|ঝিনাইদহ
Khulna|খুলনা
Kushtia|কুষ্টিয়া
Magura|মাগুরা
Meherpur|মেহেরপুর
Narail|নড়াইল
Satkhira|সাতক্ষীরা
Bandarban|বান্দরবান
Brahmanbaria|ব্রাহ্মণবাড়িয়া
Chandpur|চাঁদপুর
Chattogram|চট্টগ্রাম
Cumilla|কুমিল্লা
Cox's Bazar|কক্সবাজার
Feni|ফেনী
Khagrachhari|খাগড়াছড়ি
Lakshmipur|লক্ষ্মীপুর
Noakhali|নোয়াখালী
Rangamati|রাঙ্গামাটি
Bogura|বগুড়া
Joypurhat|জয়পুরহাট
Naogaon|নওগাঁ
Natore|নাটোর
Chapainawabganj|চাঁপাইনবাবগঞ্জ
Pabna|পাবনা
Rajshahi|রাজশাহী
Sirajganj|সিরাজগঞ্জ
Habiganj|হবিগঞ্জ
Moulvibazar|মৌলভীবাজার
Sunamganj|সুনামগঞ্জ
Sylhet|সিলেট
Dinajpur|দিনাজপুর
Gaibandha|গাইবান্ধা
Kurigram|কুড়িগ্রাম
Lalmonirhat|লালমনিরহাট
Nilphamari|নীলফামারী
Panchagarh|পঞ্চগড়
Rangpur|রংপুর
Thakurgaon|ঠাকুরগাঁও
Jamalpur|জামালপুর
Mymensingh|ময়মনসিংহ
Netrokona|নেত্রকোণা
Sherpur|শেরপুর
Barguna|বরগুনা
Barishal|বরিশাল
Bhola|ভোলা
Jhalakathi|ঝালকাঠি
Patuakhali|পটুয়াখালী
Pirojpur|পিরোজপুর""".splitlines()]
_ALIASES = {"comilla": "Cumilla", "chittagong": "Chattogram", "jessore": "Jashore", "bogra": "Bogura", "barisal": "Barishal",
            "cox’s bazar": "Cox's Bazar", "coxs bazar": "Cox's Bazar", "jhenidah": "Jhenaidah", "netrakona": "Netrokona"}
_DIST_RE = [(re.compile(r"\b" + re.escape(en) + r"\b", re.I), en) for en, _ in DISTRICTS] + \
           [(re.compile(r"\b" + re.escape(a) + r"\b", re.I), en) for a, en in _ALIASES.items()] + \
           [(re.compile(re.escape(unicodedata.normalize("NFC", bn))), en) for en, bn in DISTRICTS]


def find_district(text):
    t = norm(text)
    best = None
    for rx, en in _DIST_RE:
        m = rx.search(t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), en)
    return best[1] if best else ""


def district_from_host(host):
    h = host.lower().split(":")[0]
    if not h.endswith(".gov.bd"):
        return ""
    keys = {en.lower().replace("'", "").replace(" ", ""): en for en, _ in DISTRICTS}
    keys.update({a.replace(" ", "").replace("'", "").replace("’", ""): en for a, en in _ALIASES.items()})
    keys.update({"coxsbazar": "Cox's Bazar"})
    for p in h.split(".")[:-2]:
        if p in keys:
            return keys[p]
    return ""
