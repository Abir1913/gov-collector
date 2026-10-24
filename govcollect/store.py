"""SQLite store (system of record for the collector) plus an optional Supabase sink for the website."""
import json
import sqlite3
import threading
import datetime as dt

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  host TEXT PRIMARY KEY, base_url TEXT, name TEXT, tier INT DEFAULT 3, entity_type TEXT, ministry TEXT, sector TEXT,
  dom TEXT DEFAULT 'gov', district TEXT, active INT DEFAULT 1, home_status TEXT, home_checked TEXT, discovered_at TEXT,
  last_crawled TEXT, fail_count INT DEFAULT 0, notes TEXT);
CREATE TABLE IF NOT EXISTS listing_pages(
  url TEXT PRIMARY KEY, host TEXT, kind TEXT, via TEXT, etag TEXT, last_modified TEXT, content_hash TEXT,
  last_fetch TEXT, last_status TEXT, items_found INT DEFAULT 0, fail_count INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS documents(
  url TEXT PRIMARY KEY, host TEXT, content_hash TEXT, kind TEXT, method TEXT, chars INT, pages INT, garbled INT,
  lang TEXT, fetched_at TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS notices(
  fingerprint TEXT PRIMARY KEY, type TEXT, title TEXT, agency TEXT, host TEXT, sector TEXT, org TEXT, edu TEXT,
  district TEXT, method TEXT, val REAL, vac INT, deadline TEXT, published TEXT, url TEXT, summary TEXT, ref TEXT,
  dom TEXT, needs_review INT, confidence REAL, lang TEXT, first_seen TEXT, last_seen TEXT, extra TEXT,
  synced INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS notice_sources(fingerprint TEXT, url TEXT, host TEXT, seen_at TEXT, PRIMARY KEY(fingerprint,url));
CREATE TABLE IF NOT EXISTS seen_urls(url TEXT PRIMARY KEY, fingerprint TEXT, seen_at TEXT);
CREATE TABLE IF NOT EXISTS crawl_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, stage TEXT,
  hosts INT, pages INT, new_notices INT, errors INT, note TEXT);
CREATE TABLE IF NOT EXISTS fetch_log(ts TEXT, host TEXT, url TEXT, status TEXT, ms INT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_notices_type ON notices(type, deadline);
CREATE INDEX IF NOT EXISTS ix_notices_seen ON notices(last_seen);
CREATE INDEX IF NOT EXISTS ix_lp_host ON listing_pages(host);
"""

NOTICE_COLS = ["fingerprint", "type", "title", "agency", "host", "sector", "org", "edu", "district", "method", "val", "vac",
               "deadline", "published", "url", "summary", "ref", "dom", "needs_review", "confidence", "lang", "first_seen",
               "last_seen", "extra"]


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.c = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(SCHEMA)

    def x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args)
            self.c.commit()
            return cur

    def q(self, sql, args=()):
        with self.lock:
            return self.c.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    # ---- notices ----
    def upsert_notice(self, n):
        """Insert, or refresh last_seen and fill missing fields. Returns True if new."""
        with self.lock:
            old = self.one("SELECT * FROM notices WHERE fingerprint=?", (n["fingerprint"],))
            t = now()
            if old is None:
                row = {k: n.get(k) for k in NOTICE_COLS}
                row["first_seen"] = row["last_seen"] = t
                row["extra"] = json.dumps(n.get("extra") or {}, ensure_ascii=False)
                self.c.execute(f"INSERT INTO notices({','.join(NOTICE_COLS)}) VALUES({','.join('?' * len(NOTICE_COLS))})",
                               [row[k] for k in NOTICE_COLS])
                new = True
            else:
                # keep the best-known value for each field; richer data wins over empty
                upd = {}
                for k in ("deadline", "published", "edu", "district", "method", "ref", "summary"):
                    if not old[k] and n.get(k):
                        upd[k] = n[k]
                for k in ("val", "vac"):
                    if not old[k] and n.get(k):
                        upd[k] = n[k]
                if (n.get("confidence") or 0) > (old["confidence"] or 0) + 0.1:
                    upd.update(confidence=n["confidence"], needs_review=n.get("needs_review", 0))
                upd["last_seen"] = t
                if len(upd) > 1:
                    upd["synced"] = 0
                self.c.execute(f"UPDATE notices SET {','.join(k + '=?' for k in upd)} WHERE fingerprint=?",
                               list(upd.values()) + [n["fingerprint"]])
                new = False
            self.c.execute("INSERT OR IGNORE INTO notice_sources VALUES(?,?,?,?)",
                           (n["fingerprint"], n["url"], n["host"], t))
            self.c.execute("INSERT OR REPLACE INTO seen_urls VALUES(?,?,?)", (n["url"], n["fingerprint"], t))
            self.c.commit()
            return new

    def mark_seen(self, url, fp=None):
        self.x("INSERT OR REPLACE INTO seen_urls VALUES(?,?,?)", (url, fp, now()))

    def seen(self, url):
        return self.one("SELECT 1 FROM seen_urls WHERE url=?", (url,)) is not None

    def log(self, host, url, status, ms=0, note=""):
        self.x("INSERT INTO fetch_log VALUES(?,?,?,?,?,?)", (now(), host, url, str(status), int(ms), note))

    # ---- export / sync ----
    def export_json(self, path, open_only=True):
        rows = self.q("SELECT * FROM notices ORDER BY deadline IS NULL, deadline")
        out = []
        today = dt.date.today().isoformat()
        for r in rows:
            if open_only and r["deadline"] and r["deadline"] < today:
                continue
            out.append({k: r[k] for k in ("type", "title", "agency", "host", "sector", "org", "edu", "district", "method",
                                          "val", "vac", "deadline", "published", "url", "summary", "dom")})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        return len(out)

    def push_supabase(self, url, key, batch=200):
        import requests
        rows = self.q("SELECT * FROM notices WHERE synced=0")
        sent = 0
        hdr = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
        for i in range(0, len(rows), batch):
            chunk = []
            for r in rows[i:i + batch]:
                d = {k: r[k] for k in NOTICE_COLS}
                for dk in ("deadline", "published"):
                    d[dk] = d[dk] or None
                d["needs_review"] = bool(d["needs_review"])
                d["extra"] = json.loads(d["extra"] or "{}")
                d["first_seen"] = d["first_seen"] or None
                d["last_seen"] = d["last_seen"] or None
                chunk.append(d)
            resp = requests.post(f"{url.rstrip('/')}/rest/v1/notices?on_conflict=fingerprint", headers=hdr,
                                 data=json.dumps(chunk), timeout=60)
            if resp.status_code >= 300:
                raise RuntimeError(f"Supabase push failed {resp.status_code}: {resp.text[:300]}")
            with self.lock:
                self.c.executemany("UPDATE notices SET synced=1 WHERE fingerprint=?", [(r["fingerprint"],) for r in rows[i:i + batch]])
                self.c.commit()
            sent += len(chunk)
        return sent
