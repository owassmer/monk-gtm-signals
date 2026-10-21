"""python -m monk_signals <command>

  ledgers [--verify]        list payment ledgers; --verify re-tests mapped invoice dates on live data
  discover                  departments -> public-safety payees -> agent screen -> targets
  verify                    agent web research per target: domain, ownership, size, channel
  collect                   invoice-level payments and federal awards for targets -> event store
  queue [--as-of DATE]      score every target as of a date -> out/queue.csv
  changes                   events first seen in the latest collect run
  backtest [--months N]     the queue a rep would have received each month, and what followed
  push-clay [--dry-run]     send new/changed act_now + watch accounts to the Clay webhook
  hubspot-setup             create/update the Monk signals company properties
  hubspot-report            weekly signal cohorts from HubSpot dispositions
  run                       collect + queue + push-clay (the daily job)
"""

import argparse
import csv
import datetime as dt

from . import clay, db, hubspot, pipeline, score
from .sources import ledgers as L
from .util import OUT, load_env, to_date


def write_queue(rows, path):
    OUT.mkdir(exist_ok=True)
    if not rows:
        print("no scored accounts")
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    tiers = {t: sum(r["tier"] == t for r in rows) for t in ("act_now", "watch", "skip")}
    print(f"wrote {path.relative_to(OUT.parent)}: {tiers}")
    for r in rows[:12]:
        print(f"  {r['tier']:<8} {r['source_signal']:<13} {r['company'][:30]:<30} {r['headline'][:110]}")


def cmd_ledgers(a):
    for led in L.load():
        line = f"{led.id:<22} {led.payer:<44} invoice_date={'yes (' + led.invoice_date_meaning + ')' if led.invoice_date else 'no'}"
        if a.verify and led.invoice_date:
            v = L.verify_invoice_dates(led)
            line += (f"  verify: {'PASS' if v['pass'] else 'FAIL'} n={v['n']} median={v.get('median')}d "
                     f"p90={v.get('p90', 0):.0f}d zero={v.get('share_zero')} neg={v.get('share_negative')}")
        print(line, flush=True)


def cmd_changes(con):
    last = db.last_run(con, "collect")
    if not last:
        raise SystemExit("no collect run yet")
    prev = db.last_run(con, "collect", before=last["started"])
    if not prev:
        print("first collect run: everything is new; changes appear from the next run on")
        return
    rows = con.execute("SELECT vendor, kind, jurisdiction, unit, event_date, amount, url FROM events "
                       "WHERE first_seen >= ? ORDER BY vendor, event_date DESC", (last["started"],)).fetchall()
    print(f"{len(rows)} records first seen in the run started {last['started']}")
    for r in rows[:60]:
        print(f"  {r['vendor'][:28]:<28} {r['kind']:<11} {r['event_date']} {r['jurisdiction']} {r['unit'] or ''}")


def cmd_backtest(con, months):
    today = dt.date.today()
    seen = set()
    events = [dict(r) for r in con.execute("SELECT vendor, jurisdiction, kind, event_date FROM events")]
    for m in range(months, -1, -1):
        as_of = today - dt.timedelta(days=30 * m)
        rows = [r for r in score.queue(con, as_of) if r["tier"] == "act_now"]
        entering = [r for r in rows if r["company"] not in seen]
        seen |= {r["company"] for r in rows}
        print(f"\nas of {as_of}: {len(rows)} act_now, {len(entering)} entering")
        for r in entering[:8]:
            # What the public record showed in the 90 days after the flag
            before = {e["jurisdiction"] for e in events if e["vendor"] == r["company"] and to_date(e["event_date"]) <= as_of}
            after = {e["jurisdiction"] for e in events if e["vendor"] == r["company"]
                     and as_of < to_date(e["event_date"]) <= as_of + dt.timedelta(days=90)} - before
            follow = f"+{len(after)} more governments within 90d" if as_of + dt.timedelta(days=90) <= today else "(window still open)"
            print(f"  {r['source_signal']:<12} {r['company'][:30]:<30} {follow:<34} {r['headline'][:90]}")


def main():
    load_env()
    ap = argparse.ArgumentParser(prog="monk_signals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ledgers"); s.add_argument("--verify", action="store_true")
    sub.add_parser("discover")
    sub.add_parser("collect")
    sub.add_parser("verify")
    s = sub.add_parser("queue"); s.add_argument("--as-of"); s.add_argument("--out")
    sub.add_parser("changes")
    s = sub.add_parser("backtest"); s.add_argument("--months", type=int, default=6)
    s = sub.add_parser("push-clay"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--limit", type=int)
    s.add_argument("--tiers", default="act_now,watch")
    sub.add_parser("hubspot-setup")
    sub.add_parser("hubspot-report")
    sub.add_parser("run")
    a = ap.parse_args()

    if a.cmd == "ledgers":
        return cmd_ledgers(a)
    if a.cmd == "hubspot-setup":
        return hubspot.setup()
    if a.cmd == "hubspot-report":
        return hubspot.report()
    con = db.connect()
    con.executescript(pipeline.DDL)
    if a.cmd == "discover":
        pipeline.discover(con)
    elif a.cmd == "collect":
        pipeline.collect(con)
    elif a.cmd == "verify":
        pipeline.verify(con)
    elif a.cmd == "queue":
        as_of = to_date(a.as_of) if a.as_of else None
        rows = score.queue(con, as_of)
        write_queue(rows, OUT / (a.out or (f"queue_{a.as_of}.csv" if a.as_of else "queue.csv")))
    elif a.cmd == "changes":
        cmd_changes(con)
    elif a.cmd == "backtest":
        cmd_backtest(con, a.months)
    elif a.cmd == "push-clay":
        clay.push(con, score.queue(con), tiers=tuple(a.tiers.split(",")), dry_run=a.dry_run, limit=a.limit)
    elif a.cmd == "run":
        pipeline.collect(con)
        rows = score.queue(con)
        write_queue(rows, OUT / "queue.csv")
        clay.push(con, rows)


if __name__ == "__main__":
    main()
