"""Orchestrates discover -> crawl -> extract -> parse -> dedupe -> store across all registry hosts.

Hosts run in parallel (cfg.workers); each host is processed by exactly one worker at a time and the fetcher enforces a
per-host delay, so breadth is fast while any single site sees a gentle request rate.
"""
import datetime as dt
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import discover, extract_doc, extract_html
from .config import cfg
from .dedupe import fingerprint, find_duplicate
from .fetch import Fetcher
from .parse_fields import parse
from .store import Store, now


def _due(ts, hours):
    if not ts:
        return True
    try:
        return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.datetime.fromisoformat(ts.rstrip("Z")) > dt.timedelta(hours=hours)
    except ValueError:
        return True


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.d = dict(hosts_total=0, hosts_done=0, hosts_failed=0, listings=0, pages=0, new_notices=0, seen_again=0, errors=0,
                      docs=0, ocr=0, review=0)

    def add(self, **kw):
        with self.lock:
            for k, v in kw.items():
                self.d[k] = self.d.get(k, 0) + v

    def snap(self):
        with self.lock:
            return dict(self.d)


class Pipeline:
    def __init__(self, store, fetcher=None):
        self.s = store
        self.f = fetcher or Fetcher(store)
        self.stats = Stats()
        self.stop = threading.Event()

    # ------------------------------------------------------------ discovery
    def discover_host(self, src):
        host, base = src["host"], src["base_url"]
        r = self.f.get(base)
        status = r.error or str(r.status)
        self.s.x("UPDATE sources SET home_status=?, home_checked=? WHERE host=?", (status, now(), host))
        if not r.ok:
            self.s.x("UPDATE sources SET fail_count=fail_count+1 WHERE host=?", (host,))
            return 0
        self.s.x("UPDATE sources SET fail_count=0, discovered_at=? WHERE host=?", (now(), host))
        html = r.text()
        found = discover.find_listings(html, r.url)
        if discover.homepage_is_listing(html, r.url):
            found.insert(0, (r.url, "notice", "home"))
        if not found:
            for p in discover.FALLBACK_PATHS[:4]:
                u = base.rstrip("/") + p
                rr = self.f.get(u)
                if rr.ok and len(extract_html.extract_listing(rr.text(), rr.url)[0]) >= 3:
                    found.append((u, "notice", "fallback"))
                    break
        n = 0
        for url, kind, via in found:
            self.s.x("INSERT OR IGNORE INTO listing_pages(url,host,kind,via) VALUES(?,?,?,?)", (url, host, kind, via))
            n += 1
        return n

    # ------------------------------------------------------------ crawling
    def crawl_listing(self, src, lp, budget):
        url, kind, pages, new = lp["url"], lp["kind"], 0, 0
        first = True
        while url and pages < cfg.max_pages and not self.stop.is_set():
            r = self.f.get(url, lp["etag"] if first else None, lp["last_modified"] if first else None)
            self.stats.add(pages=1)
            if r.not_modified:
                self.s.x("UPDATE listing_pages SET last_fetch=?, last_status='304' WHERE url=?", (now(), lp["url"]))
                return new
            if not r.ok:
                self.s.x("UPDATE listing_pages SET last_fetch=?, last_status=?, fail_count=fail_count+1 WHERE url=?",
                         (now(), r.error or str(r.status), lp["url"]))
                self.stats.add(errors=1)
                return new
            h = hashlib.sha1(r.content).hexdigest()
            if first:
                same = h == lp["content_hash"]
                self.s.x("UPDATE listing_pages SET etag=?, last_modified=?, content_hash=?, last_fetch=?, last_status='200', fail_count=0 WHERE url=?",
                         (r.headers.get("ETag"), r.headers.get("Last-Modified"), h, now(), lp["url"]))
                if same:
                    return new
            first = False
            if kind == "feed" or r.ctype in ("application/rss+xml", "application/atom+xml", "text/xml", "application/xml"):
                cands, nxt = extract_html.parse_feed(r.text(), r.url), None
            else:
                cands, nxt = extract_html.extract_listing(r.text(), r.url, kind if kind in ("job", "tender", "notice") else None)
            self.s.x("UPDATE listing_pages SET items_found=MAX(items_found,?) WHERE url=?", (len(cands), lp["url"]))
            fresh = [c for c in cands if not self.s.seen(c["url"])]
            for c in fresh:
                if budget["n"] <= 0 or self.stop.is_set():
                    return new
                if c["date"] and (dt.date.today() - c["date"]).days > cfg.max_age_days:
                    self.s.mark_seen(c["url"])
                    continue
                budget["n"] -= 1
                new += self.process(src, kind, c)
            pages += 1
            if not fresh:
                break  # reached items we already have
            url = nxt
        return new

    def process(self, src, kind, c):
        text, d, note, ok = "", None, "", True
        resp = self.f.get(c["url"])
        if resp.ok:
            d = extract_doc.extract(resp.content, resp.ctype, resp.url)
            text = d.text
            if d.method == "html" and len(text) < 200:
                for link in extract_html.doc_links(resp.text(), resp.url)[:1]:
                    r2 = self.f.get(link)
                    if r2.ok:
                        d2 = extract_doc.extract(r2.content, r2.ctype, r2.url)
                        if len(d2.text) > len(text):
                            d, text = d2, d2.text
            self.stats.add(docs=1, ocr=1 if d.method == "ocr" else 0)
            self.s.x("INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (c["url"], src["host"], hashlib.sha1(resp.content).hexdigest(), resp.ctype, d.method, len(text), d.pages,
                      int(d.garbled), d.lang, now(), text[:60000]))
        else:
            ok, note = False, resp.error or f"http_{resp.status}"
        n = parse(c["title"], text, dict(src), hint=kind, listing_date=c["date"])
        n["url"] = c["url"]
        n["needs_review"] = int(n["confidence"] < 0.6 or (d is not None and d.garbled) or not ok)
        n["extra"] = dict(n["extra"], **({"doc_note": d.note} if d and d.note else {}), **({"fetch_note": note} if note else {}),
                          **({"doc_method": d.method} if d else {}))
        fp = fingerprint(n)
        dup = None if self.s.one("SELECT 1 FROM notices WHERE fingerprint=?", (fp,)) else find_duplicate(self.s, n)
        n["fingerprint"] = dup or fp
        is_new = self.s.upsert_notice(n)
        self.stats.add(new_notices=int(is_new), seen_again=int(not is_new), review=int(is_new and n["needs_review"]))
        return int(is_new)

    # ------------------------------------------------------------ one host
    def run_host(self, src, stage):
        try:
            if self.stop.is_set():
                return
            if stage in ("discover", "run") and _due(src["home_checked"], cfg.rediscover_days * 24):
                self.discover_host(src)
            if stage in ("crawl", "run", "crawl-force"):
                lps = self.s.q("SELECT * FROM listing_pages WHERE host=? AND fail_count<5", (src["host"],))
                budget = {"n": cfg.max_new_per_host}
                for lp in lps:
                    if _due(lp["last_fetch"], cfg.tier_hours.get(src["tier"], 24)) or stage == "crawl-force":
                        self.stats.add(listings=1)
                        self.crawl_listing(src, lp, budget)
                self.s.x("UPDATE sources SET last_crawled=? WHERE host=?", (now(), src["host"]))
        except Exception as e:  # never let one host stop the run
            self.stats.add(errors=1, hosts_failed=1)
            self.s.x("INSERT INTO fetch_log VALUES(?,?,?,?,?,?)", (now(), src["host"], "", "exception", 0, repr(e)[:200]))
        finally:
            self.stats.add(hosts_done=1)

    # ------------------------------------------------------------ run
    def run(self, stage="run", tier=None, limit=None, hosts=None, workers=None, on_progress=None):
        q, args = "SELECT * FROM sources WHERE active=1", []
        if tier:
            q += " AND tier<=?"
            args.append(tier)
        if hosts:
            q += f" AND host IN ({','.join('?' * len(hosts))})"
            args += list(hosts)
        q += " ORDER BY tier, host"
        if limit:
            q += f" LIMIT {int(limit)}"
        srcs = self.s.q(q, args)
        self.stats.d["hosts_total"] = len(srcs)
        run_id = self.s.x("INSERT INTO crawl_runs(started,stage) VALUES(?,?)", (now(), stage)).lastrowid
        stop_ticker = threading.Event()

        def ticker():
            while not stop_ticker.wait(15):
                if on_progress:
                    on_progress("running", self.stats.snap())

        threading.Thread(target=ticker, daemon=True).start()
        try:
            with ThreadPoolExecutor(max_workers=workers or cfg.workers) as ex:
                list(ex.map(lambda s: self.run_host(s, stage), srcs))
        finally:
            stop_ticker.set()
        st = self.stats.snap()
        self.s.x("UPDATE crawl_runs SET finished=?,hosts=?,pages=?,new_notices=?,errors=?,note=? WHERE id=?",
                 (now(), st["hosts_done"], st["pages"], st["new_notices"], st["errors"], json.dumps(st), run_id))
        if on_progress:
            on_progress("done", st)
        return st


def open_store(path=None):
    return Store(path or cfg.db)
