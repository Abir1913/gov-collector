"""Runtime settings. Everything can be overridden with environment variables."""
import os


def _f(name, default):
    return type(default)(os.getenv(name, default))


class Config:
    def __init__(self):
        self.db = os.getenv("GOV_DB", "govcollect.sqlite")
        self.contact = os.getenv("GOV_CONTACT", "set-GOV_CONTACT@example.com")
        self.workers = _f("GOV_WORKERS", 24)            # parallel HOSTS (each host is processed serially)
        self.host_delay = _f("GOV_HOST_DELAY", 2.0)      # min seconds between requests to the same host
        self.timeout = _f("GOV_TIMEOUT", 25.0)
        self.max_bytes = _f("GOV_MAX_BYTES", 20 * 1024 * 1024)
        self.max_pages = _f("GOV_MAX_PAGES", 3)          # listing pages followed per listing URL per run
        self.max_new_per_host = _f("GOV_MAX_NEW", 40)    # new notices processed per host per run
        self.max_age_days = _f("GOV_MAX_AGE_DAYS", 180)  # ignore listing items older than this
        self.pdf_pages = _f("GOV_PDF_PAGES", 6)
        self.ocr_pages = _f("GOV_OCR_PAGES", 4)
        self.circuit = _f("GOV_CIRCUIT", 4)              # consecutive failures before a host is skipped this run
        self.respect_robots = os.getenv("GOV_ROBOTS", "1") != "0"
        self.tls_fallback = os.getenv("GOV_TLS_FALLBACK", "0") == "1"  # retry public pages without cert checks
        self.supabase_url = os.getenv("SUPABASE_URL", "")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
        self.rediscover_days = _f("GOV_REDISCOVER_DAYS", 7)
        self.tier_hours = {1: 2, 2: 12, 3: 24}

    @property
    def ua(self):
        return f"govradar-collector/1.0 (public notices aggregator; contact: {self.contact})"


cfg = Config()
