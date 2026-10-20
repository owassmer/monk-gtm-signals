"""HubSpot: the CRM schema as code, and the outcome report that closes the loop.
Clay is the only writer of records; this module defines the fields and reads results."""

import csv
import datetime as dt
import os
from collections import defaultdict

from .util import OUT, http_json

API = "https://api.hubapi.com"
GROUP = {"name": "monk_signals", "label": "Monk signals"}


def _opts(values):
    return [{"label": v.replace("_", " ").capitalize() if v.islower() else v, "value": v, "displayOrder": i}
            for i, v in enumerate(values)]


SIGNALS = ["NEW_PAYER", "EXPANDING", "SLOW_PAYER", "PAYER_SPRAWL"]
DISPOSITIONS = ["next_step", "problem_confirmed", "not_now", "already_solved", "no_problem", "wrong_person", "reseller_billed"]

PROPERTIES = [
    # written by Clay from the signal payload
    ("source_signal", "Source signal", "enumeration", "select", SIGNALS,
     "The public-record trigger that sourced this account. Cohort key for the feedback loop."),
    ("signal_tier", "Signal tier", "enumeration", "select", ["act_now", "watch"], ""),
    ("signal_reasons", "Signal reasons", "string", "text", None, "All reason codes present."),
    ("signal_headline", "Signal headline", "string", "textarea", None, "Plain-English evidence, newest first."),
    ("signal_evidence_url", "Signal evidence URL", "string", "text", None, "Public record behind the headline."),
    ("signal_first_flagged", "Signal first flagged", "date", "date", None, "Date the system first flagged this account."),
    ("governments_24m", "Government payers (24 mo)", "number", "number", None, ""),
    ("new_payers_90d", "New government payers (90 days)", "string", "textarea", None, ""),
    ("slowest_payer", "Slowest government payer", "string", "text", None, ""),
    ("slowest_payer_days", "Slowest payer median days to pay", "number", "number", None, ""),
    ("median_days_to_pay", "Median days to pay (gov)", "number", "number", None, ""),
    # written by Clay's research and AI columns
    ("ar_hypothesis", "AR hypothesis", "string", "textarea", None, ""),
    ("channel_check", "Channel / AR stack check", "string", "textarea", None, "Sells direct or via reseller; AR tooling seen."),
    ("signal_first_line", "Outreach first line", "string", "textarea", None, "Opening sentence anchored on the public record."),
    # set by reps; read back by `hubspot-report`
    ("hypothesis_status", "Hypothesis status", "enumeration", "select", ["untested", "confirmed", "rejected"], ""),
    ("disposition", "Signal disposition", "enumeration", "select", DISPOSITIONS,
     "Outcome code. Read weekly by signal cohort."),
]


def _h():
    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        raise SystemExit("HUBSPOT_SERVICE_KEY not set in .env")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def setup():
    h = _h()
    groups = {g["name"] for g in http_json(f"{API}/crm/v3/properties/companies/groups", headers=h)["results"]}
    if GROUP["name"] not in groups:
        http_json(f"{API}/crm/v3/properties/companies/groups", method="POST", json_body=GROUP, headers=h)
        print(f"created property group '{GROUP['label']}'")
    existing = {p["name"]: p for p in http_json(f"{API}/crm/v3/properties/companies", headers=h)["results"]}
    for i, (name, label, typ, field, options, desc) in enumerate(PROPERTIES):
        body = {"name": name, "label": label, "type": typ, "fieldType": field, "groupName": GROUP["name"],
                "description": desc, "displayOrder": i}
        if options:
            body["options"] = _opts(options)
        if name in existing:
            patch = {k: v for k, v in body.items() if k in ("label", "description", "options", "groupName", "displayOrder")}
            http_json(f"{API}/crm/v3/properties/companies/{name}", method="PATCH", json_body=patch, headers=h)
            print(f"  updated {name}")
        else:
            http_json(f"{API}/crm/v3/properties/companies", method="POST", json_body=body, headers=h)
            print(f"  created {name}")


def report():
    """Weekly cohorts by source_signal, read like loan vintages: each cohort's early-stage
    rates at its current age. Early stages resolve in weeks; revenue later confirms or overrules."""
    h = _h()
    props = ["name", "domain", "source_signal", "signal_first_flagged", "disposition", "hypothesis_status", "createdate"]
    rows, after = [], None
    while True:
        body = {"filterGroups": [{"filters": [{"propertyName": "source_signal", "operator": "HAS_PROPERTY"}]}],
                "properties": props, "limit": 100}
        if after:
            body["after"] = after
        r = http_json(f"{API}/crm/v3/objects/companies/search", method="POST", json_body=body, headers=h)
        rows += [x["properties"] for x in r["results"]]
        after = r.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    cohorts = defaultdict(lambda: defaultdict(int))
    today = dt.date.today()
    for p in rows:
        flagged = (p.get("signal_first_flagged") or p.get("createdate") or "")[:10]
        if not flagged:
            continue
        d = dt.date.fromisoformat(flagged)
        week = (d - dt.timedelta(days=d.weekday())).isoformat()
        c = cohorts[(week, p.get("source_signal"))]
        disp, status = p.get("disposition") or "", p.get("hypothesis_status") or ""
        c["accounts"] += 1
        c["age_weeks"] = (today - d).days // 7
        c["worked"] += bool(disp)
        c["reached_owner"] += bool(disp) and disp != "wrong_person"
        c["problem_confirmed"] += disp in ("problem_confirmed", "next_step") or status == "confirmed"
        c["next_step"] += disp == "next_step"
        c["signal_misfire"] += disp in ("no_problem", "reseller_billed")
    cols = ["cohort_week", "source_signal", "age_weeks", "accounts", "worked", "reached_owner",
            "problem_confirmed", "next_step", "signal_misfire"]
    OUT.mkdir(exist_ok=True)
    with open(OUT / "cohorts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        print(f"{'cohort':<11} {'signal':<13} {'age':>3} {'n':>3} {'worked':>6} {'owner':>5} {'confirmed':>9} {'next':>4} {'misfire':>7}")
        for (week, sig), c in sorted(cohorts.items()):
            w.writerow([week, sig] + [c[k] for k in cols[2:]])
            n = c["accounts"]
            pct = lambda k: f"{c[k] / n:.0%}" if n else "-"
            print(f"{week:<11} {sig:<13} {c['age_weeks']:>3} {n:>3} {pct('worked'):>6} {pct('reached_owner'):>5} "
                  f"{pct('problem_confirmed'):>9} {pct('next_step'):>4} {pct('signal_misfire'):>7}")
    print(f"{len(rows)} flagged companies in HubSpot -> out/cohorts.csv")
