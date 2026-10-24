"""One entry point used by the CLI, the GitHub Action and the local control server: run a crawl, sync to Supabase,
and publish live status (table crawl_status) so the website's 'Update all sources' button can show progress."""
import json
import threading

import requests

from .config import cfg
from .pipeline import Pipeline, open_store
from .store import now

_lock = threading.Lock()
LOCAL = {"state": "idle", "started": None, "finished": None, "stats": {}, "note": ""}


def push_status(state, stats=None, note="", started=None, finished=None):
    LOCAL.update(state=state, stats=stats or LOCAL["stats"], note=note)
    if started:
        LOCAL["started"] = started
    if finished:
        LOCAL["finished"] = finished
    if not (cfg.supabase_url and cfg.supabase_key):
        return
    row = {"id": 1, "state": state, "stats": stats or LOCAL["stats"], "note": note, "updated": now()}
    if started:
        row["started"] = started
    if finished:
        row["finished"] = finished
    try:
        requests.post(f"{cfg.supabase_url.rstrip('/')}/rest/v1/crawl_status?on_conflict=id", timeout=20,
                      headers={"apikey": cfg.supabase_key, "Authorization": f"Bearer {cfg.supabase_key}",
                               "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"},
                      data=json.dumps(row))
    except Exception:
        pass


def run_job(scope="full", push=True, workers=None, store=None):
    """scope: 'full' (every registry host), 'tier1' (high-volume sources), 'tier2' (tiers 1-2)."""
    if not _lock.acquire(blocking=False):
        return {"error": "a crawl is already running"}
    try:
        s = store or open_store()
        tier = {"tier1": 1, "tier2": 2}.get(scope)
        started = now()
        push_status("running", {}, f"scope={scope}", started=started, finished=None)
        pl = Pipeline(s)
        stats = pl.run("run", tier=tier, workers=workers, on_progress=lambda st, d: push_status(st if st == "running" else "running", d, f"scope={scope}"))
        synced = 0
        if push and cfg.supabase_url and cfg.supabase_key:
            try:
                synced = s.push_supabase(cfg.supabase_url, cfg.supabase_key)
            except Exception as e:
                push_status("error", stats, f"sync failed: {e}", finished=now())
                return {"error": str(e), "stats": stats}
        stats["synced"] = synced
        push_status("done", stats, f"scope={scope}", finished=now())
        return stats
    finally:
        _lock.release()
