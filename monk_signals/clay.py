"""Push accounts to Clay. Clay webhook tables only insert rows, so the integration uses two
tables:

  Companies     (CLAY_WEBHOOK_URL)          one row per account, inserted the first time it
                                            qualifies; all enrichment, AI and CRM columns live here
  Signal Feed   (CLAY_SIGNAL_FEED_URL)      every later change to an account's signal (new
                                            evidence, new tier, removal); Companies reads the
                                            latest version back with a Lookup on `company`

Only accounts whose signal payload changed since the last push are sent, so Clay spend
tracks change, not list size."""

import hashlib
import json
import os
import time

from .util import OUT, now_iso, session

STABLE_FIELDS = ["company", "domain", "tier", "source_signal", "reasons", "headline", "evidence_url",
                 "governments_24m", "departments_24m", "new_payers_90d", "new_departments_90d",
                 "slowest_payer", "slowest_payer_days", "median_days_to_pay"]


def post(url, payload, headers=None):
    """Clay answers 200 with a non-JSON body; success is the status code. No retry on a
    request that may have landed (it would create a duplicate row); retry only on 429/5xx."""
    for i in range(3):
        r = session().post(url, json=payload, headers=headers, timeout=30)
        if r.status_code < 300:
            return
        if r.status_code not in (429, 500, 502, 503, 504):
            raise RuntimeError(f"Clay webhook {r.status_code}: {r.text[:200]}")
        time.sleep(2 ** i)
    raise RuntimeError(f"Clay webhook still failing: {r.status_code}")


def _headers(token_var):
    tok = os.environ.get(token_var)
    return {"x-clay-webhook-auth": tok} if tok else None


def _hash(r):
    return hashlib.sha1(json.dumps({k: r.get(k) for k in STABLE_FIELDS}, sort_keys=True).encode()).hexdigest()


def push(con, rows, tiers=("act_now", "watch"), dry_run=False, limit=None):
    companies_url = os.environ.get("CLAY_WEBHOOK_URL")
    feed_url = os.environ.get("CLAY_SIGNAL_FEED_URL")
    if not dry_run and not companies_url:
        raise SystemExit("CLAY_WEBHOOK_URL not set in .env (or use --dry-run)")
    pushed = {r["vendor"]: dict(r) for r in con.execute("SELECT * FROM pushes")}
    current = {r["company"]: r for r in rows}
    ts = now_iso()
    plan = []   # (destination, payload, hash)

    for r in [r for r in rows if r["tier"] in tiers][:limit]:
        h, prev = _hash(r), pushed.get(r["company"])
        if prev and prev["payload_hash"] == h:
            continue
        payload = {**r, "first_flagged": (prev["first_pushed"] if prev else ts)[:10],
                   "change": "updated" if prev else "new", "signal_version": ts}
        plan.append(("feed" if prev else "companies", payload, h))

    # Accounts already in Clay that no longer qualify (excluded, or fell to skip) get a
    # removal record so Companies can show it instead of silently keeping stale evidence.
    for company, prev in pushed.items():
        r = current.get(company)
        if prev["payload_hash"] == "removed" or (r and r["tier"] in tiers):
            continue
        payload = {"company": company, "tier": "removed", "change": "removed", "signal_version": ts,
                   "headline": "No longer qualifies: " + ("now below the signal threshold" if r else "excluded")}
        plan.append(("feed", payload, "removed"))

    if any(dest == "feed" for dest, _, _ in plan) and not feed_url and not dry_run:
        raise SystemExit("Some accounts already in Clay changed. Set CLAY_SIGNAL_FEED_URL "
                         "(the Signal Feed table's webhook) so they update instead of duplicating.")

    for dest, payload, h in plan:
        if dry_run:
            continue
        if dest == "companies":
            post(companies_url, payload, _headers("CLAY_WEBHOOK_TOKEN"))
        else:
            post(feed_url, payload, _headers("CLAY_SIGNAL_FEED_TOKEN"))
        time.sleep(0.15)
        con.execute("INSERT INTO pushes VALUES (?,?,?,?) ON CONFLICT(vendor) DO UPDATE SET "
                    "payload_hash=excluded.payload_hash, last_pushed=excluded.last_pushed",
                    (payload["company"], h, (payload.get("first_flagged") or ts)[:10], ts))
        con.commit()

    OUT.mkdir(exist_ok=True)
    with open(OUT / "clay_payloads.jsonl", "w") as f:
        for dest, p, _ in plan:
            f.write(json.dumps({"destination": dest, **p}) + "\n")
    n_new = sum(d == "companies" for d, _, _ in plan)
    verb = "would send" if dry_run else "sent"
    print(f"{verb}: {n_new} new -> Companies, {len(plan) - n_new} changed/removed -> Signal Feed")
    return plan
