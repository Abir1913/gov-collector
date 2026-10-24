"""Polite, fast-across-hosts fetcher.

Aggressive in breadth (many hosts in parallel), gentle per host: robots.txt, per-host delay, conditional GETs,
backoff on 429/5xx, a circuit breaker for hosts that keep failing, and a response-size cap.
"""
import threading
import time
import urllib.robotparser as robotparser
from urllib.parse import urlparse

import requests
from bs4 import UnicodeDammit

from .config import cfg

try:  # pragma: no cover
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


class Resp:
    def __init__(self, url, status=0, headers=None, content=b"", error="", ms=0, not_modified=False, insecure=False):
        self.url, self.status, self.headers = url, status, headers or {}
        self.content, self.error, self.ms = content, error, ms
        self.not_modified, self.insecure = not_modified, insecure

    @property
    def ok(self):
        return self.status == 200 and not self.error

    @property
    def ctype(self):
        return (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()

    def text(self):
        if not self.content:
            return ""
        return UnicodeDammit(self.content, is_html=True).unicode_markup or ""


class _Host:
    def __init__(self):
        self.lock = threading.Lock()
        self.last = 0.0
        self.fails = 0
        self.robots = None
        self.delay = cfg.host_delay


class Fetcher:
    def __init__(self, store=None):
        self.store = store
        self.hosts = {}
        self.hl = threading.Lock()
        self.tl = threading.local()

    def _session(self):
        s = getattr(self.tl, "s", None)
        if s is None:
            s = self.tl.s = requests.Session()
            s.headers.update({"User-Agent": cfg.ua, "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
                              "Accept-Language": "bn,en;q=0.8"})
        return s

    def _state(self, netloc):
        with self.hl:
            return self.hosts.setdefault(netloc, _Host())

    def _robots(self, st, scheme, netloc):
        if st.robots is not None:
            return st.robots
        rp = robotparser.RobotFileParser()
        try:
            r = self._session().get(f"{scheme}://{netloc}/robots.txt", timeout=10, allow_redirects=True,
                                    verify=not cfg.tls_fallback)
            if r.status_code == 200 and "html" not in r.headers.get("Content-Type", "").lower():
                rp.parse(r.text.splitlines())
            else:
                rp.parse([])
        except Exception:
            rp.parse([])
        st.robots = rp
        cd = rp.crawl_delay(cfg.ua) or rp.crawl_delay("*")
        if cd:
            st.delay = max(st.delay, min(float(cd), 30.0))
        return rp

    def circuit_open(self, netloc):
        return self._state(netloc).fails >= cfg.circuit

    def get(self, url, etag=None, last_modified=None, retries=2, max_bytes=None):
        u = urlparse(url)
        st = self._state(u.netloc)
        max_bytes = max_bytes or cfg.max_bytes
        if st.fails >= cfg.circuit:
            return Resp(url, error="circuit_open")
        with st.lock:  # one request at a time per host
            if cfg.respect_robots:
                rp = self._robots(st, u.scheme, u.netloc)
                if not rp.can_fetch(cfg.ua, url):
                    return Resp(url, error="robots_disallow")
            hdr = {}
            if etag:
                hdr["If-None-Match"] = etag
            if last_modified:
                hdr["If-Modified-Since"] = last_modified
            attempt, insecure = 0, False
            while True:
                wait = st.last + st.delay - time.time()
                if wait > 0:
                    time.sleep(wait)
                t0 = time.time()
                st.last = t0
                try:
                    r = self._session().get(url, headers=hdr, timeout=cfg.timeout, stream=True, allow_redirects=True,
                                            verify=not insecure)
                    body, size = [], 0
                    for chunk in r.iter_content(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            r.close()
                            return self._done(st, Resp(url, r.status_code, dict(r.headers), b"", "too_large", _ms(t0)), u)
                        body.append(chunk)
                    resp = Resp(r.url, r.status_code, dict(r.headers), b"".join(body), "", _ms(t0),
                                not_modified=r.status_code == 304, insecure=insecure)
                except requests.exceptions.SSLError:
                    if cfg.tls_fallback and not insecure:
                        insecure = True
                        continue
                    return self._done(st, Resp(url, error="tls_error", ms=_ms(t0)), u)
                except requests.exceptions.RequestException as e:
                    if attempt < retries:
                        attempt += 1
                        time.sleep(min(2 ** attempt, 8))
                        continue
                    return self._done(st, Resp(url, error=type(e).__name__, ms=_ms(t0)), u)
                if resp.status in (429, 502, 503, 504) and attempt < retries:
                    attempt += 1
                    ra = resp.headers.get("Retry-After", "")
                    time.sleep(min(float(ra) if ra.isdigit() else 2 ** attempt * 2, 30))
                    continue
                return self._done(st, resp, u)

    def _done(self, st, resp, u):
        bad = bool(resp.error) or resp.status >= 500 or resp.status == 0
        st.fails = st.fails + 1 if bad else 0
        if self.store:
            try:
                self.store.log(u.netloc, resp.url, resp.status or resp.error, resp.ms)
            except Exception:
                pass
        return resp


def _ms(t0):
    return int((time.time() - t0) * 1000)
