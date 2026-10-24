import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from govcollect.config import cfg
from govcollect.pipeline import Pipeline, open_store
from govcollect import registry, textutil
from govcollect.extract_doc import ocr_available


def text_pdf(lines):
    b = io.BytesIO()
    c = canvas.Canvas(b, pagesize=A4)
    y = 800
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 18
    c.save()
    return b.getvalue()


def scan_pdf(lines):
    img = Image.new("RGB", (1500, 900), "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.load_default(size=44)
    y = 40
    for ln in lines:
        d.text((40, y), ln, fill="black", font=f)
        y += 80
    b = io.BytesIO()
    img.save(b, "PDF")
    return b.getvalue()


JOB = text_pdf(["Directorate General of Health Services", "invites applications for the following posts.",
                "Total 40 posts of Medical Officer, Khulna.", "Education: MBBS degree. Age: maximum 32 years.",
                "Application fee: Tk 500.", "Last date of application: 30 November 2026"])
TENDER = text_pdf(["Bangladesh Computer Council", "Invitation for Tender", "Tender ID: 1234567", "Procurement Method: OTM",
                   "Supply of IT equipment, Dhaka", "Last selling date: 20 November 2026", "Closing date: 25 November 2026",
                   "Estimated cost: 4,200,000"])
SCAN = scan_pdf(["Recruitment circular", "Total 12 posts of Assistant Engineer", "Bachelor degree in Engineering",
                 "Last date of application 15 December 2026"])
EXAM = text_pdf(["Admission test routine published", "Date: 12 October 2026"])
SECRET = text_pdf(["Should never be fetched: Tender ID: 9999999"])


def page(title, rows, nxt=None):
    r = "".join(f'<tr><td>{d}</td><td><a href="{u}">{t}</a></td></tr>' for d, t, u in rows)
    n = f'<a href="{nxt}">next</a>' if nxt else ""
    return f"<html><head><title>{title}</title></head><body><nav><a href='/'>Home</a></nav><table>{r}</table>{n}</body></html>".encode()


SITE = {
    "/": ("text/html", b"<html><body><nav><a href='/notices.html'>Notice Board</a><a href='/jobs.html'>Recruitment</a>"
                       b"<a href='/tenders.html'>Tenders</a><a href='/login'>Login</a></nav><h1>Welcome</h1></body></html>"),
    "/robots.txt": ("text/plain", b"User-agent: *\nDisallow: /private/\n"),
    "/notices.html": ("text/html", page("Notices", [("১২ অক্টোবর ২০২৬", "ভর্তি পরীক্ষার সময়সূচি প্রকাশ", "/files/exam.pdf")], "/notices2.html")),
    "/notices2.html": ("text/html", page("Notices 2", [("10 October 2026", "Annual staff training schedule published", "/files/exam.pdf?x=2")])),
    "/jobs.html": ("text/html", page("Jobs", [("20-10-2026", "Recruitment circular for Medical Officer posts", "/files/job1.pdf"),
                                              ("21-10-2026", "Recruitment circular: Assistant Engineer (scanned notice)", "/files/job_scan.pdf")])),
    "/tenders.html": ("text/html", page("Tenders", [("22 October 2026", "Tender notice: Supply of IT equipment", "/files/tender1.pdf"),
                                                    ("22 October 2026", "Tender notice: hidden package", "/private/secret.pdf")])),
    "/files/job1.pdf": ("application/pdf", JOB), "/files/tender1.pdf": ("application/pdf", TENDER),
    "/files/job_scan.pdf": ("application/pdf", SCAN), "/files/exam.pdf": ("application/pdf", EXAM),
    "/private/secret.pdf": ("application/pdf", SECRET),
}
HITS = []


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        HITS.append(self.path)
        p = self.path.split("?")[0]
        if p in SITE:
            ct, body = SITE[p]
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


class Pipe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cfg.host_delay = 0.02
        cfg.max_age_days = 3650
        cls.db = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        cls.s = open_store(cls.db)
        for host in (f"127.0.0.1:{cls.port}", f"localhost:{cls.port}"):
            registry.upsert_source(cls.s, host, "Test Directorate", "directorate", "Ministry of Health", "Health",
                                   base_url=f"http://{host}/", tier=1)
        cls.pl = Pipeline(cls.s)
        cls.st = cls.pl.run("run", workers=2)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def notice(self, like):
        r = self.s.q("SELECT * FROM notices WHERE title LIKE ?", (f"%{like}%",))
        self.assertTrue(r, f"no notice like {like}")
        return r[0]

    def test_discovery_finds_three_kinds(self):
        kinds = {r["kind"] for r in self.s.q("SELECT kind FROM listing_pages")}
        self.assertTrue({"notice", "job", "tender"} <= kinds, kinds)

    def test_tender_fields(self):
        n = self.notice("IT equipment")
        self.assertEqual((n["type"], n["ref"], n["method"], n["deadline"]), ("tender", "1234567", "OTM", "2026-11-25"))
        self.assertAlmostEqual(n["val"], 4.2, places=1)
        self.assertEqual(n["district"], "Dhaka")

    def test_job_fields_and_extra(self):
        n = self.notice("Medical Officer")
        self.assertEqual((n["type"], n["vac"], n["edu"], n["district"], n["deadline"]), ("job", 40, "Bachelor", "Khulna", "2026-11-30"))
        self.assertEqual(json.loads(n["extra"]).get("age_limit"), 32)

    def test_ocr_for_scanned_pdf(self):
        if not ocr_available():
            self.skipTest("tesseract not installed")
        n = self.notice("scanned")
        self.assertEqual(n["type"], "job")
        self.assertEqual(json.loads(n["extra"]).get("doc_method"), "ocr")
        self.assertEqual(n["deadline"], "2026-12-15")

    def test_bangla_notice_and_pagination(self):
        n = self.notice("ভর্তি")
        self.assertEqual((n["type"], n["published"]), ("notice", "2026-10-12"))
        self.notice("staff training")  # page 2 followed

    def test_robots_respected(self):
        self.assertFalse(any("/private/" in h for h in HITS))
        self.assertEqual(self.s.q("SELECT * FROM notices WHERE ref='9999999'"), [])

    def test_mirror_deduplicated(self):
        rows = self.s.q("SELECT fingerprint FROM notices WHERE title LIKE '%Medical Officer%'")
        self.assertEqual(len(rows), 1)
        src = self.s.q("SELECT host FROM notice_sources WHERE fingerprint=?", (rows[0]["fingerprint"],))
        self.assertEqual(len({r["host"] for r in src}), 2)

    def test_second_run_is_incremental(self):
        before = len(HITS)
        st = Pipeline(self.s).run("run", workers=2)
        self.assertEqual(st["new_notices"], 0)
        self.assertLess(len(HITS) - before, 12)  # conditional/hash checks, no re-downloading documents

    def test_export_and_report(self):
        out = os.path.join(tempfile.mkdtemp(), "o.json")
        self.assertGreater(self.s.export_json(out), 3)
        data = json.load(open(out, encoding="utf-8"))
        self.assertTrue({"type", "title", "deadline", "dom"} <= set(data[0]))
        from govcollect.__main__ import report
        report(self.s)

    def test_textutil_edges(self):
        self.assertEqual(str(textutil.find_deadline("আবেদনের শেষ তারিখ: ৩০ নভেম্বর ২০২৬")), "2026-11-30")
        self.assertTrue(textutil.garbled("†Rjv cÖkvmK Kvh©vjq, XvKv| Dc‡Rjv wbev©nx Awdmv‡ii Kvh©vjq weÁwß †b‡Ui gva¨‡g"))

    def test_registry_import(self):
        s = open_store(os.path.join(tempfile.mkdtemp(), "r.sqlite"))
        n = registry.import_entities(s, os.path.join(os.path.dirname(__file__), "..", "data", "entities.csv"))
        self.assertGreater(n, 800)
        self.assertGreater(s.one("SELECT COUNT(*) c FROM sources WHERE tier=1")["c"], 20)


if __name__ == "__main__":
    unittest.main()
