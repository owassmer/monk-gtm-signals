"""Event store. Every public record becomes one row with two clocks:

  event_date  when it happened in the world (drives point-in-time scoring / backtests)
  first_seen  when this system first observed it (drives "what changed since last run")
"""

import json
import sqlite3

from .util import ROOT, now_iso

DB_PATH = ROOT / "signals.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  vendor TEXT NOT NULL,          -- canonical company name
  kind TEXT NOT NULL,            -- payment | award
  source TEXT NOT NULL,          -- ledger:<id> | usaspending
  jurisdiction TEXT,             -- the paying government
  unit TEXT,                     -- department as published
  unit_class TEXT,               -- department normalized to a function
  event_date TEXT,
  invoice_date TEXT,
  invoice_date_meaning TEXT,     -- issued | received
  due_date TEXT,
  amount REAL,
  ref TEXT,                      -- invoice or award id
  raw_name TEXT,                 -- vendor name as the source wrote it
  title TEXT,
  url TEXT,
  detail TEXT,                   -- JSON
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_vendor ON events(vendor, event_date);
CREATE TABLE IF NOT EXISTS coverage (
  source TEXT PRIMARY KEY,
  jurisdiction TEXT,
  history_start TEXT,            -- earliest date this source can see for this run
  checked_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT, finished TEXT, command TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS pushes (
  vendor TEXT PRIMARY KEY,
  payload_hash TEXT,
  first_pushed TEXT,
  last_pushed TEXT
);
"""

FIELDS = ["event_id", "vendor", "kind", "source", "jurisdiction", "unit", "unit_class", "event_date",
          "invoice_date", "invoice_date_meaning", "due_date", "amount", "ref", "raw_name", "title", "url", "detail"]


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def upsert_events(con, events):
    """Insert new events; for known ones only refresh last_seen and mutable fields."""
    ts = now_iso()
    new = 0
    for e in events:
        row = {k: e.get(k) for k in FIELDS}
        if isinstance(row["detail"], (dict, list)):
            row["detail"] = json.dumps(row["detail"])
        cur = con.execute("SELECT 1 FROM events WHERE event_id=?", (row["event_id"],)).fetchone()
        if cur:
            con.execute("UPDATE events SET last_seen=?, url=?, title=?, detail=? WHERE event_id=?",
                        (ts, row["url"], row["title"], row["detail"], row["event_id"]))
        else:
            new += 1
            con.execute(f"INSERT INTO events ({','.join(FIELDS)}, first_seen, last_seen) "
                        f"VALUES ({','.join('?' * len(FIELDS))}, ?, ?)", [row[k] for k in FIELDS] + [ts, ts])
    con.commit()
    return new


def set_coverage(con, source, jurisdiction, history_start):
    con.execute("INSERT INTO coverage VALUES (?,?,?,?) ON CONFLICT(source) DO UPDATE SET "
                "jurisdiction=excluded.jurisdiction, history_start=excluded.history_start, checked_at=excluded.checked_at",
                (source, jurisdiction, history_start, now_iso()))
    con.commit()


def coverage_by_jurisdiction(con):
    """Earliest date from which we can see each government at all."""
    out = {}
    for r in con.execute("SELECT jurisdiction, MIN(history_start) h FROM coverage GROUP BY jurisdiction"):
        out[r["jurisdiction"]] = r["h"]
    return out


def log_run(con, command, started, stats):
    con.execute("INSERT INTO runs (started, finished, command, stats) VALUES (?,?,?,?)",
                (started, now_iso(), command, json.dumps(stats)))
    con.commit()


def last_run(con, command, before=None):
    q = "SELECT * FROM runs WHERE command=?"
    args = [command]
    if before:
        q += " AND started < ?"
        args.append(before)
    return con.execute(q + " ORDER BY run_id DESC LIMIT 1", args).fetchone()
