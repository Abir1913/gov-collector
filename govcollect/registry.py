"""Load the host registry (the 830-row inventory, plus anything crawl_portal.py finds) into the collector."""
import csv

from .textutil import district_from_host

TIER1 = set("""bpsc.gov.bd eprocure.gov.bd ntrca.gov.bd dghs.gov.bd dpe.gov.bd dshe.gov.bd lged.gov.bd rhd.portal.gov.bd dphe.gov.bd
pwd.gov.bd nbr.portal.gov.bd police.portal.gov.bd dip.gov.bd railway.gov.bd bpdb.gov.bd bcc.gov.bd moedu.gov.bd shed.gov.bd mopa.gov.bd
cabinet.gov.bd mof.gov.bd ictd.gov.bd dgfp.gov.bd dgme.gov.bd dgda.gov.bd dae.gov.bd dls.gov.bd fisheries.gov.bd bgb.gov.bd ansarvdp.gov.bd
fireservice.gov.bd bsti.gov.bd btrc.gov.bd dwasa.gov.bd rajuk.gov.bd cda.gov.bd reb.gov.bd bida.gov.bd roc.gov.bd teletalk.gov.bd
dmp.portal.gov.bd bteb.gov.bd ugc.gov.bd dss.gov.bd dnc.gov.bd prison.gov.bd cmsd.gov.bd dgfood.gov.bd pmo.gov.bd bppa.gov.bd
dhakaeducationboard.portal.gov.bd bise-ctg.portal.gov.bd""".split())
TIER2_TYPES = {"ministry", "division", "directorate", "authority", "board", "commission", "council", "geo_district",
               "corporation", "geo_division", "city_corporation"}


def _dom(entity_type, host):
    if entity_type == "bank_fin":
        return "bank"
    if entity_type in ("edu_board", "edu_institution") or host.endswith((".edu.bd", ".ac.bd")) or \
            any(host.endswith(f".{n}.gov.bd") for n in ("school", "college", "polytech")):
        return "edu"
    return "gov"


def upsert_source(store, host, name="", entity_type="", ministry="", sector="", base_url=None, tier=None, dom=None):
    host = host.strip().lower()
    if not host:
        return False
    if tier is None:
        tier = 1 if host in TIER1 else (2 if entity_type in TIER2_TYPES else 3)
    store.x("""INSERT INTO sources(host,base_url,name,tier,entity_type,ministry,sector,dom,district)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(host) DO UPDATE SET name=excluded.name, tier=MIN(sources.tier,excluded.tier),
               entity_type=excluded.entity_type, ministry=excluded.ministry, sector=excluded.sector""",
            (host, base_url or f"https://{host}/", name, tier, entity_type, ministry, sector,
             dom or _dom(entity_type, host), district_from_host(host)))
    return True


def import_entities(store, path):
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("entity_type") in ("service_portal", "national_site") and r["host"].count(".") < 2:
                pass  # service portals are still worth watching for notices
            n += upsert_source(store, r["host"], r.get("name_en", ""), r.get("entity_type", ""),
                               r.get("ministry_name", ""), r.get("sector", ""))
    return n


def import_portal_csv(store, path):
    """Rows produced by crawl_portal.py: list,name,host,url (upazilas, unions, schools, banks, other offices...)."""
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            host = (r.get("host") or "").strip().lower()
            if not host.endswith("bd"):
                continue
            kind = {"upazilas": "upazila", "unions": "union", "districts": "geo_district", "divisions": "geo_division"}.get(r.get("list"), "office")
            n += upsert_source(store, host, r.get("name_bn_or_en", ""), kind, "", "Local administration" if kind in ("upazila", "union") else "", tier=3)
    return n
