"""discover: find target vendors from what public-safety departments actually pay.
collect:  pull every dated public record for those vendors into the event store."""

import csv
import datetime as dt
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import agent, db, names
from .sources import ledgers as L
from .sources import public_records as P
import yaml

from .util import CONFIG, OUT, config, now_iso, today

LOOKBACK_DAYS = 3 * 365          # history needed to call a payer "new" 24 months back, plus backtests
MIN_TOTAL = 10_000               # de minimis payees are not accounts
ACTIVE_DAYS = 548                # paid in the last 18 months

DDL = """
CREATE TABLE IF NOT EXISTS units (ledger TEXT, unit TEXT, function TEXT, PRIMARY KEY (ledger, unit));
CREATE TABLE IF NOT EXISTS companies (
  company TEXT PRIMARY KEY, domain TEXT, category TEXT, is_target INTEGER, confidence TEXT, reason TEXT,
  raw_names TEXT, governments INTEGER, total REAL, excluded TEXT, updated TEXT);
"""
VERIFY_COLS = ["domain_source", "ownership", "ownership_source", "size_band", "sells_direct", "verified_reason", "verified_at"]


def _exclusions():
    ex = dict(config("exclude.yaml")["exclude"])
    local = CONFIG / "exclude.local.yaml"
    if local.exists():
        for reason, lst in (yaml.safe_load(local.read_text()) or {}).get("exclude", {}).items():
            ex[reason] = list(ex.get(reason, [])) + list(lst)
    return [(n, reason) for reason, lst in ex.items() for n in lst]


def excluded_reason(raw_names, excl):
    for raw in raw_names:
        for n, reason in excl:
            if names.matches(raw, n):
                return f"{reason}: {n}"
    return ""


def discover(con):
    con.executescript(DDL)
    started = now_iso()
    since = today() - dt.timedelta(days=LOOKBACK_DAYS)
    ledgers = L.load()

    print("1/4 departments per government", flush=True)
    deps, _ = L.run_parallel(L.departments, ledgers, since, label="departments")
    items = [{"id": f"{lid}|{d['unit']}", "government": next(x.payer for x in ledgers if x.id == lid),
              "department": d["unit"], "payments": d["payments"]} for lid, ds in deps.items() for d in ds]
    print(f"2/4 agent: classifying {len(items)} departments by function", flush=True)
    fn = agent.classify_departments(con, items)
    for it in items:
        lid, unit = it["id"].split("|", 1)
        if it["id"] in fn:
            con.execute("INSERT OR REPLACE INTO units VALUES (?,?,?)", (lid, unit, fn[it["id"]]["function"]))
    con.commit()
    safety = defaultdict(list)
    for lid, unit, f in con.execute("SELECT ledger, unit, function FROM units"):
        if f in names.PUBLIC_SAFETY:
            safety[lid].append(unit)

    print("3/4 payees of public-safety departments", flush=True)
    roll, _ = L.run_parallel(lambda led: L.payee_rollup(led, safety.get(led.id, []), since), ledgers, label="rollup")
    unit_fn = {(lid, u): f for lid, u, f in con.execute("SELECT ledger, unit, function FROM units")}
    acc = {}
    for lid, rows in roll.items():
        gov = next(x.payer for x in ledgers if x.id == lid)
        for r in rows:
            k = names.vendor_key(r["raw_name"])
            if not k:
                continue
            a = acc.setdefault(k, {"names": set(), "raw_by_ledger": defaultdict(set), "governments": set(),
                                   "departments": set(), "payments": 0, "total": 0.0, "months": set(), "samples": set()})
            a["names"].add(r["raw_name"])
            a["raw_by_ledger"][lid].add(r["raw_name"])
            a["governments"].add(gov)
            a["departments"].add(unit_fn.get((lid, r["unit"]), r["unit"]))
            a["payments"] += r["payments"]
            a["total"] += r["amount"]
            a["months"].add(r["month"])
            if r["sample"] and len(a["samples"]) < 3:
                a["samples"].add(r["sample"])
    active_since = (today() - dt.timedelta(days=ACTIVE_DAYS)).isoformat()[:7]
    excl = _exclusions()
    pool, skipped = [], defaultdict(int)
    for k, a in acc.items():
        if a["total"] < MIN_TOTAL:
            skipped["under $10K"] += 1
            continue
        if max(a["months"]) < active_since:
            skipped["inactive 18 months"] += 1
            continue
        reason = excluded_reason(a["names"], excl)
        if reason:
            skipped["exclude list"] += 1
            continue
        pool.append({"id": k, "names": sorted(a["names"])[:4], "governments": sorted(a["governments"]),
                     "departments": sorted(a["departments"]), "payments": a["payments"], "total": round(a["total"]),
                     "first": min(a["months"]), "last": max(a["months"]), "samples": sorted(a["samples"])})
    print(f"   {len(acc)} payees; {len(pool)} to screen; skipped {dict(skipped)}", flush=True)

    print(f"4/4 agent: screening {len(pool)} payees against the ICP", flush=True)
    verdicts = agent.classify_payees(con, pool)

    # Entity resolution: payee spellings the agent named as one company (or one domain) merge.
    companies = {}
    by_domain = {}
    for it in pool:
        v = verdicts.get(it["id"])
        if not v:
            continue
        dom = (v.get("domain") or "").lower().removeprefix("www.")
        ck = by_domain.get(dom) if dom else None
        ck = ck or names.vendor_key(v["company"]) or it["id"]
        if dom:
            by_domain.setdefault(dom, ck)
        c = companies.setdefault(ck, {"company": v["company"], "domain": dom, "category": v["category"],
                                      "is_target": v["is_target"], "confidence": v["confidence"],
                                      "reason": v["reason"], "raw": defaultdict(set), "governments": set(), "total": 0.0})
        c["is_target"] = c["is_target"] or v["is_target"]
        for lid, raws in acc[it["id"]]["raw_by_ledger"].items():
            c["raw"][lid] |= raws
        c["governments"] |= set(it["governments"])
        c["total"] += it["total"]
    con.execute("DELETE FROM companies")
    for c in companies.values():
        con.execute("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (c["company"], c["domain"], c["category"], int(c["is_target"]), c["confidence"], c["reason"],
                     json.dumps({k: sorted(v) for k, v in c["raw"].items()}), len(c["governments"]),
                     round(c["total"]), excluded_reason([c["company"]], excl), now_iso()))
    con.commit()

    OUT.mkdir(exist_ok=True)
    rows = sorted(companies.values(), key=lambda c: (-int(c["is_target"]), -len(c["governments"]), -c["total"]))
    with open(OUT / "payees_screened.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "is_target", "category", "confidence", "reason", "domain", "governments", "total_paid",
                    "spellings"])
        for c in rows:
            w.writerow([c["company"], c["is_target"], c["category"], c["confidence"], c["reason"], c["domain"],
                        len(c["governments"]), round(c["total"]), " | ".join(sorted({r for v in c["raw"].values() for r in v}))])
    n_t = sum(c["is_target"] for c in companies.values())
    db.log_run(con, "discover", started, {"payees": len(acc), "screened": len(pool), "companies": len(companies), "targets": n_t})
    print(f"targets: {n_t} of {len(companies)} companies -> out/payees_screened.csv", flush=True)


def targets(con):
    con.executescript(DDL)
    return [dict(r) for r in con.execute(
        "SELECT * FROM companies WHERE is_target=1 AND (excluded IS NULL OR excluded='') ORDER BY governments DESC")]


def collect(con):
    started = now_iso()
    since = today() - dt.timedelta(days=LOOKBACK_DAYS)
    ts = targets(con)
    if not ts:
        raise SystemExit("no targets yet: run `discover` first")
    print(f"collecting public records for {len(ts)} target companies", flush=True)
    unit_fn = {(lid, u): f for lid, u, f in con.execute("SELECT ledger, unit, function FROM units")}
    ledgers = L.load()
    stats = {}

    # Every target is looked up in every ledger under every spelling seen anywhere, in the
    # case variants governments use. Discovery only sees payees of public-safety departments;
    # this also finds payments from a government's other departments, such as a port or parks.
    spellings = {}
    for t in ts:
        for raws in json.loads(t["raw_names"]).values():
            for raw in raws:
                if "..." in raw:          # masked payee names (College Station) are not reusable
                    continue
                for variant in {raw, raw.upper(), raw.title()}:
                    spellings.setdefault(variant, t["company"])

    print(f"1/2 invoice-level payments ({len(spellings)} spellings x {len(ledgers)} ledgers)", flush=True)
    def pull(led):
        raw_to_company = dict(spellings)
        raw_to_company.update({raw: t["company"] for t in ts for raw in json.loads(t["raw_names"]).get(led.id, [])
                               if "..." not in raw})
        return L.invoice_events(led, raw_to_company, since, lambda lid, u: unit_fn.get((lid, u), names.clean_unit(u)))
    pays, _ = L.run_parallel(pull, ledgers, label="payments")
    events = [e for evs in pays.values() for e in evs]
    for led in ledgers:
        try:
            hs = L.history_start(led)
            db.set_coverage(con, led.source, led.payer, max(hs, since).isoformat() if hs else since.isoformat())
        except Exception as e:
            print(f"    coverage {led.id}: {str(e)[:80]}")
    stats["payments"] = len(events)


    print("2/2 federal awards", flush=True)
    def fed(t):
        aliases = [t["company"]] + sorted({r for v in json.loads(t["raw_names"]).values() for r in v})
        try:
            return P.federal_events(t["company"], aliases, since)
        except Exception as e:
            print(f"    usaspending {t['company']}: {str(e)[:80]}")
            return []
    with ThreadPoolExecutor(8) as ex:
        fed_events = [e for evs in ex.map(fed, ts) for e in evs]
    db.set_coverage(con, "usaspending", P.FEDERAL, since.isoformat())
    events += fed_events
    stats["federal_awards"] = len(fed_events)

    stats["new_events"] = db.upsert_events(con, events)
    db.log_run(con, "collect", started, stats)
    print(f"done: {stats}", flush=True)


def verify(con):
    """Web-verify every target: domain, ownership, size, channel. Non-targets are excluded with the reason."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(companies)")}
    for c in VERIFY_COLS:
        if c not in cols:
            con.execute(f"ALTER TABLE companies ADD COLUMN {c} TEXT")
    ts = targets(con)
    items = [{"id": t["company"], "company": t["company"],
              "spellings": sorted({r for v in json.loads(t["raw_names"]).values() for r in v})[:4],
              "governments_paying": t["governments"], "prior_reason": t["reason"]} for t in ts]
    print(f"agent (web): verifying {len(items)} targets", flush=True)
    res = agent.verify_targets(con, items)
    kept = 0
    for t in ts:
        v = res.get(t["company"])
        if not v:
            continue
        con.execute("UPDATE companies SET domain=?, domain_source=?, ownership=?, ownership_source=?, size_band=?, "
                    "sells_direct=?, verified_reason=?, verified_at=?, excluded=? WHERE company=?",
                    ((v.get("domain") or t["domain"] or "").lower().removeprefix("www."), v.get("domain_source"),
                     v.get("ownership"), v.get("ownership_source"), v.get("size_band"), v.get("sells_direct"),
                     v.get("reason"), now_iso(), "" if v["is_target"] else f"verified: {v.get('reason')}", t["company"]))
        kept += bool(v["is_target"])
    con.commit()
    print(f"verified {len(res)}/{len(ts)}: {kept} remain targets", flush=True)
