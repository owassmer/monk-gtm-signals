"""Point-in-time account scoring from the event store.

Everything is computed `as_of` a date using only events dated on or before it, so the same
function produces today's queue and any historical queue (backtest).

Reason codes (the unit the feedback loop learns on; becomes HubSpot `source_signal`):
  NEW_PAYER     a government paid or awarded the vendor for the first time in 90 days,
                with 24 months of visible history showing no prior relationship
  EXPANDING     a new department inside an existing government paying customer, same test
  SLOW_PAYER    a department takes a median 45+ days from invoice to payment (3+ invoices)
  PAYER_SPRAWL  paid by 3+ governments or 6+ departments in 24 months
Tier: act_now = trigger (NEW_PAYER | EXPANDING) and pain (SLOW_PAYER | PAYER_SPRAWL);
      watch = either; skip = neither.
"""

import datetime as dt
import statistics as st
from collections import defaultdict

from .util import money, to_date

PAYER_KINDS = {"payment", "award"}
NEW_WINDOW = 90
QUIET = 730
SLOW_DAYS = 45
TRIGGERS = ["NEW_PAYER", "EXPANDING"]
PAINS = ["SLOW_PAYER", "PAYER_SPRAWL"]


def _fmt(d):
    return d.strftime("%b %-d, %Y")


def score_vendor(evs, as_of, coverage):
    evs = [e for e in evs if (d := to_date(e["event_date"])) and d <= as_of]
    if not evs:
        return None
    w24, w18, w90 = as_of - dt.timedelta(days=730), as_of - dt.timedelta(days=548), as_of - dt.timedelta(days=NEW_WINDOW)
    payer_evs = [e for e in evs if e["kind"] in PAYER_KINDS]

    first_j, first_u = {}, {}
    for e in sorted(payer_evs, key=lambda e: e["event_date"]):
        first_j.setdefault(e["jurisdiction"], e)
        if e["unit_class"]:
            first_u.setdefault((e["jurisdiction"], e["unit_class"]), e)

    def visible_since(j, d):
        c = to_date(coverage.get(j))
        return c is not None and c <= d - dt.timedelta(days=QUIET)

    new_j = [e for j, e in first_j.items() if to_date(e["event_date"]) >= w90 and visible_since(j, to_date(e["event_date"]))]
    new_j_names = {e["jurisdiction"] for e in new_j}
    new_u = [e for (j, u), e in first_u.items() if j not in new_j_names and to_date(e["event_date"]) >= w90
             and to_date(first_j[j]["event_date"]) < to_date(e["event_date"]) and visible_since(j, to_date(e["event_date"]))]

    recent = [e for e in payer_evs if to_date(e["event_date"]) >= w24]
    govs = {e["jurisdiction"] for e in recent}
    units = {(e["jurisdiction"], e["unit_class"]) for e in recent if e["unit_class"]}

    by_unit = defaultdict(list)
    for e in evs:
        if e["kind"] == "payment" and e["invoice_date"] and to_date(e["event_date"]) >= w18:
            lag = (to_date(e["event_date"]) - to_date(e["invoice_date"])).days
            if 0 <= lag <= 540:
                by_unit[(e["jurisdiction"], e["unit"], e["invoice_date_meaning"])].append((lag, e))
    lag_rows = []
    for (j, unit, meaning), xs in by_unit.items():
        if len(xs) >= 3:
            lag_rows.append({"jurisdiction": j, "unit": unit, "meaning": meaning, "n": len(xs),
                             "median": st.median(l for l, _ in xs),
                             "url": max(xs, key=lambda x: x[0])[1]["url"]})
    all_lags = [l for xs in by_unit.values() for l, _ in xs]
    slowest = max(lag_rows, key=lambda r: r["median"]) if lag_rows else None
    fastest = min(lag_rows, key=lambda r: r["median"]) if lag_rows else None

    reasons = []
    if new_j:
        reasons.append("NEW_PAYER")
    if new_u:
        reasons.append("EXPANDING")
    if slowest and slowest["median"] >= SLOW_DAYS:
        reasons.append("SLOW_PAYER")
    if len(govs) >= 3 or len(units) >= 6:
        reasons.append("PAYER_SPRAWL")
    trig = [r for r in TRIGGERS if r in reasons]
    pain = [r for r in PAINS if r in reasons]
    tier = "act_now" if trig and pain else ("watch" if trig or pain else "skip")
    source_signal = (trig or pain or ["NONE"])[0]

    # Plain-English evidence, newest-first, each clause backed by a URL.
    clauses, urls = [], []
    for e in sorted(new_j, key=lambda e: e["event_date"], reverse=True)[:2]:
        verb = "first paid" if e["kind"] == "payment" else "first awarded"
        who = f"{e['jurisdiction']} ({e['unit']})" if e["unit"] else e["jurisdiction"]
        clauses.append(f"New payer: {who} {verb} them on {_fmt(to_date(e['event_date']))}"
                       + (f" ({money(e['amount'])})" if e["amount"] else ""))
        urls.append(e["url"])
    for e in sorted(new_u, key=lambda e: e["event_date"], reverse=True)[:1]:
        clauses.append(f"Expanding: {e['jurisdiction']} {e['unit']} started paying them on {_fmt(to_date(e['event_date']))}")
        urls.append(e["url"])
    if slowest:
        rec = "receipt" if slowest["meaning"] == "received" else "invoice date"
        s = f"{slowest['jurisdiction']} {slowest['unit']} pays them a median {slowest['median']:.0f} days after {rec} ({slowest['n']} invoices)"
        if fastest and fastest is not slowest and fastest["median"] * 2 <= slowest["median"]:
            where = fastest["unit"] if fastest["jurisdiction"] == slowest["jurisdiction"] else f"{fastest['jurisdiction']} {fastest['unit']}"
            s += f", vs {fastest['median']:.0f} days at {where}"
        clauses.append(s)
        urls.append(slowest["url"])
    if len(govs) >= 3 or len(units) >= 6:
        g = f"{len(govs)} governments" if len(govs) != 1 else "1 government"
        clauses.append(f"Paid by {len(units)} departments across {g} in 24 months")

    last = max(to_date(e["event_date"]) for e in payer_evs) if payer_evs else None
    return {
        "tier": tier, "source_signal": source_signal, "reasons": ", ".join(reasons),
        "headline": ". ".join(clauses[:3]) + ("." if clauses else ""),
        "evidence_url": urls[0] if urls else "", "evidence_urls": " ".join(urls[:4]),
        "governments_24m": len(govs), "departments_24m": len(units),
        "new_payers_90d": " | ".join(f"{e['jurisdiction']} ({e['event_date']})" for e in new_j),
        "new_departments_90d": " | ".join(f"{e['jurisdiction']} {e['unit']} ({e['event_date']})" for e in new_u),
        "median_days_to_pay": round(st.median(all_lags)) if all_lags else "",
        "slowest_payer": f"{slowest['jurisdiction']} {slowest['unit']}" if slowest else "",
        "slowest_payer_days": round(slowest["median"]) if slowest else "",
        "paid_24m": round(sum(e["amount"] or 0 for e in recent if e["kind"] == "payment")),
        "last_public_record": last.isoformat() if last else "",
        "payers": " | ".join(sorted(govs)),
    }


def queue(con, as_of=None):
    as_of = as_of or dt.date.today()
    coverage = {r["jurisdiction"]: r["h"] for r in
                con.execute("SELECT jurisdiction, MIN(history_start) h FROM coverage GROUP BY jurisdiction")}
    comp = {r["company"]: dict(r) for r in con.execute(
        "SELECT * FROM companies WHERE is_target=1 AND (excluded IS NULL OR excluded='')")}
    evs = defaultdict(list)
    for r in con.execute("SELECT * FROM events"):
        evs[r["vendor"]].append(dict(r))
    rows = []
    for company, c in comp.items():
        s = score_vendor(evs.get(company, []), as_of, coverage)
        if not s:
            continue
        rows.append({"company": company, "domain": c["domain"], "category": c["category"],
                     "why_target": c.get("verified_reason") or c["reason"], **s,
                     "ownership": c.get("ownership") or "", "size_band": c.get("size_band") or "",
                     "sells_direct": c.get("sells_direct") or "", "as_of": as_of.isoformat()})
    order = {"act_now": 0, "watch": 1, "skip": 2}
    rows.sort(key=lambda r: (order[r["tier"]], -r["governments_24m"], -r["paid_24m"]))
    return rows
