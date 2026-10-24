import argparse
import json
import sys

from .config import cfg
from .pipeline import Pipeline, open_store
from . import registry


def main(argv=None):
    ap = argparse.ArgumentParser(prog="govcollect", description="Bangladesh government notice collector")
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import-registry")
    p.add_argument("--entities", default="data/entities.csv")
    p.add_argument("--portal", help="portal_all.csv from crawl_portal.py")
    for name in ("discover", "crawl", "run"):
        p = sub.add_parser(name)
        p.add_argument("--tier", type=int)
        p.add_argument("--limit", type=int)
        p.add_argument("--hosts", help="comma-separated hosts")
        p.add_argument("--workers", type=int)
        p.add_argument("--push", action="store_true", help="sync new notices to Supabase afterwards")
    p = sub.add_parser("update", help="full run + Supabase sync + live status (what the website button triggers)")
    p.add_argument("--scope", default="full", choices=["full", "tier1", "tier2"])
    sub.add_parser("report")
    p = sub.add_parser("export-json")
    p.add_argument("out")
    p.add_argument("--all", action="store_true", help="include closed notices")
    sub.add_parser("push")
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8787)
    a = ap.parse_args(argv)
    if a.db:
        cfg.db = a.db
    s = open_store()
    if a.cmd == "import-registry":
        n = registry.import_entities(s, a.entities)
        if a.portal:
            n += registry.import_portal_csv(s, a.portal)
        print(f"registry: {n} hosts upserted; total {s.one('SELECT COUNT(*) c FROM sources')['c']}")
    elif a.cmd in ("discover", "crawl", "run"):
        pl = Pipeline(s)
        hosts = a.hosts.split(",") if a.hosts else None
        st = pl.run(a.cmd, tier=a.tier, limit=a.limit, hosts=hosts, workers=a.workers,
                    on_progress=lambda state, d: print(f"[{state}] {d['hosts_done']}/{d['hosts_total']} hosts, {d['new_notices']} new", file=sys.stderr))
        print(json.dumps(st, indent=1))
        if a.push and cfg.supabase_url:
            print("synced", s.push_supabase(cfg.supabase_url, cfg.supabase_key))
    elif a.cmd == "update":
        from .jobs import run_job
        print(json.dumps(run_job(a.scope, store=s), indent=1, default=str))
    elif a.cmd == "report":
        report(s)
    elif a.cmd == "export-json":
        print("exported", s.export_json(a.out, open_only=not a.all))
    elif a.cmd == "push":
        print("synced", s.push_supabase(cfg.supabase_url, cfg.supabase_key))
    elif a.cmd == "serve":
        from .serve import serve
        serve(a.port)


def report(s):
    q = lambda sql: s.q(sql)
    print("sources:", q("SELECT COUNT(*) c FROM sources")[0]["c"], "| reachable:", q("SELECT COUNT(*) c FROM sources WHERE home_status='200'")[0]["c"],
          "| listing pages:", q("SELECT COUNT(*) c FROM listing_pages")[0]["c"])
    print("notices by type:", {r["type"]: r["c"] for r in q("SELECT type, COUNT(*) c FROM notices GROUP BY type")},
          "| needs review:", q("SELECT COUNT(*) c FROM notices WHERE needs_review=1")[0]["c"])
    print("\nHosts with a listing page but no notices (parser work queue):")
    for r in q("""SELECT l.host, COUNT(*) pages FROM listing_pages l LEFT JOIN notice_sources n ON n.host=l.host
                  WHERE n.host IS NULL GROUP BY l.host ORDER BY pages DESC LIMIT 25"""):
        print("  ", r["host"], r["pages"])
    print("\nUnreachable hosts (top failures):")
    for r in q("SELECT host, home_status, fail_count FROM sources WHERE home_status IS NOT NULL AND home_status!='200' ORDER BY fail_count DESC LIMIT 15"):
        print("  ", r["host"], r["home_status"], r["fail_count"])
    print("\nHosts never discovered:", q("SELECT COUNT(*) c FROM sources WHERE home_checked IS NULL")[0]["c"])


if __name__ == "__main__":
    main()
