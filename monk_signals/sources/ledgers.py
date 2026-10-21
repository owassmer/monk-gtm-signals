"""Government payment ledgers (Socrata SODA API) -> canonical payment facts.

Three query shapes:
  departments()    the paying departments of a government (grouped, small)
  payee_rollup()   payee x department x month for the departments that matter
  invoice_events() invoice-level rows for exact payee spellings
"""

import datetime as dt
import os
import statistics as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .. import names
from ..util import config, http_json, stable_id, to_date, to_float

PAGE = 50000


@dataclass
class Ledger:
    id: str
    payer: str
    level: str
    domain: str
    datasets: list
    vendor: str
    paid: str
    amount: str
    unit: str
    invoice_date: str = None
    invoice_date_meaning: str = None
    due_date: str = None
    invoice_id: str = None
    description: str = None
    notes: str = None
    _history_start: object = field(default=None, repr=False)

    @property
    def source(self):
        return f"ledger:{self.id}"

    def columns(self):
        cols = {"vendor": self.vendor, "paid": self.paid, "amount": self.amount, "unit": self.unit,
                "invoice_date": self.invoice_date, "due_date": self.due_date,
                "invoice_id": self.invoice_id, "description": self.description}
        return {k: v for k, v in cols.items() if v}

    def dataset_page(self, ds):
        return f"https://{self.domain}/d/{ds}"

    def rows_url(self, ds, where):
        return f"https://{self.domain}/resource/{ds}.json?" + urlencode({"$where": where})


def load():
    return [Ledger(**x) for x in config("ledgers.yaml")["ledgers"]]


def q(s):
    return "'" + (s or "").replace("'", "''") + "'"


def soql(ledger, ds, params, timeout=90):
    headers = {}
    if os.environ.get("SOCRATA_APP_TOKEN"):
        headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]
    return http_json(f"https://{ledger.domain}/resource/{ds}.json", params=params, headers=headers,
                     timeout=timeout, tries=3)


def soql_all(ledger, ds, params):
    out, offset = [], 0
    while True:
        batch = soql(ledger, ds, {**params, "$limit": PAGE, "$offset": offset})
        out += batch
        if len(batch) < PAGE:
            return out
        offset += PAGE


def history_start(ledger):
    if ledger._history_start is None:
        lows = []
        for ds in ledger.datasets:
            r = soql(ledger, ds, {"$select": f"min({ledger.paid}) as lo"})
            if r and r[0].get("lo"):
                lows.append(to_date(r[0]["lo"]))
        ledger._history_start = min(lows) if lows else None
    return ledger._history_start


def departments(ledger, since):
    """[{unit, payments}] for departments with payments since `since`."""
    agg = {}
    for ds in ledger.datasets:
        for r in soql_all(ledger, ds, {"$select": f"{ledger.unit} as u, count(*) as n",
                                       "$where": f"{ledger.paid} >= '{since.isoformat()}'",
                                       "$group": ledger.unit, "$order": "n DESC"}):
            u = (r.get("u") or "").strip()
            if u:
                agg[u] = agg.get(u, 0) + int(r.get("n") or 0)
    return [{"unit": u, "payments": n} for u, n in agg.items()]


def payee_rollup(ledger, units, since):
    """Payee x department x month for the given exact department names."""
    out = []
    if not units:
        return out
    desc = f", max({ledger.description}) as d" if ledger.description else ""
    for ds in ledger.datasets:
        for i in range(0, len(units), 40):
            in_list = ",".join(q(u) for u in units[i:i + 40])
            sel = (f"{ledger.vendor} as v, {ledger.unit} as u, date_trunc_ym({ledger.paid}) as m, "
                   f"count(*) as n, sum({ledger.amount}) as a{desc}")
            params = {"$select": sel, "$where": f"{ledger.unit} in ({in_list}) AND {ledger.paid} >= '{since.isoformat()}'",
                      "$group": f"{ledger.vendor}, {ledger.unit}, date_trunc_ym({ledger.paid})", "$order": "n DESC"}
            for r in soql_all(ledger, ds, params):
                out.append({"raw_name": (r.get("v") or "").strip(), "unit": (r.get("u") or "").strip(),
                            "month": (r.get("m") or "")[:7], "payments": int(r.get("n") or 0),
                            "amount": to_float(r.get("a")) or 0.0, "sample": (r.get("d") or "")[:120]})
    return out


def invoice_events(ledger, raw_to_company, since, unit_class):
    """Invoice-level payment events for exact payee spellings. unit_class(ledger_id, unit) -> function."""
    raws = sorted(raw_to_company)
    if not raws:
        return []
    select = ",".join(dict.fromkeys(ledger.columns().values()))
    agg = {}
    for ds in ledger.datasets:
        for i in range(0, len(raws), 150):
            in_list = ",".join(q(r) for r in raws[i:i + 150])
            rows = soql_all(ledger, ds, {"$select": select, "$order": ":id",
                                         "$where": f"{ledger.vendor} in ({in_list}) AND {ledger.paid} >= '{since.isoformat()}'"})
            for r in rows:
                raw = (r.get(ledger.vendor) or "").strip()
                paid = to_date(r.get(ledger.paid))
                if raw not in raw_to_company or not paid:
                    continue
                inv_date = to_date(r.get(ledger.invoice_date)) if ledger.invoice_date else None
                inv_id = (r.get(ledger.invoice_id) or "").strip() if ledger.invoice_id else ""
                unit = (r.get(ledger.unit) or "").strip()
                key = (raw, unit, inv_id, inv_date, paid, ds)
                e = agg.setdefault(key, {"amount": 0.0, "desc": "",
                                         "due": to_date(r.get(ledger.due_date)) if ledger.due_date else None})
                e["amount"] += to_float(r.get(ledger.amount)) or 0.0
                if not e["desc"] and ledger.description:
                    e["desc"] = (r.get(ledger.description) or "").strip()
    events = []
    for (raw, unit, inv_id, inv_date, paid, ds), e in agg.items():
        if e["amount"] <= 0:                       # voids and credits net out
            continue
        where = (f"{ledger.vendor}={q(raw)} AND {ledger.paid} >= '{paid.isoformat()}' "
                 f"AND {ledger.paid} < '{(paid + dt.timedelta(days=1)).isoformat()}'")
        if inv_id:
            where += f" AND {ledger.invoice_id}={q(inv_id)}"
        company = raw_to_company[raw]
        events.append({
            "event_id": stable_id(ledger.source, ds, company, raw, unit, inv_id, inv_date, paid),
            "vendor": company, "kind": "payment", "source": ledger.source,
            "jurisdiction": ledger.payer, "unit": names.clean_unit(unit), "unit_class": unit_class(ledger.id, unit),
            "event_date": paid.isoformat(),
            "invoice_date": inv_date.isoformat() if inv_date else None,
            "invoice_date_meaning": ledger.invoice_date_meaning if inv_date else None,
            "due_date": e["due"].isoformat() if e["due"] else None,
            "amount": round(e["amount"], 2), "ref": inv_id, "raw_name": raw, "title": e["desc"][:300],
            "url": ledger.rows_url(ds, where),
            "detail": {"dataset_page": ledger.dataset_page(ds), "level": ledger.level},
        })
    return events


def verify_invoice_dates(ledger, n=5000):
    """A mapped invoice_date must behave like one: paid - invoice_date over the latest n
    payments is spread out and almost never negative."""
    if not ledger.invoice_date:
        return None
    rows = soql(ledger, ledger.datasets[0], {"$select": f"{ledger.invoice_date} as i, {ledger.paid} as p",
                                             "$where": f"{ledger.invoice_date} IS NOT NULL",
                                             "$order": f"{ledger.paid} DESC", "$limit": n})
    lags = [(to_date(r["p"]) - to_date(r["i"])).days for r in rows if r.get("i") and r.get("p")]
    if len(lags) < 20:
        return {"n": len(lags), "pass": False}
    dec = st.quantiles(lags, n=10)
    res = {"n": len(lags), "p10": dec[0], "median": st.median(lags), "p90": dec[-1],
           "share_zero": round(sum(x == 0 for x in lags) / len(lags), 3),
           "share_negative": round(sum(x < 0 for x in lags) / len(lags), 3)}
    res["pass"] = res["share_negative"] < 0.05 and res["share_zero"] < 0.5 and res["median"] > 0
    return res


def run_parallel(fn, ledgers, *args, workers=8, label=""):
    """Run fn(ledger, *args) across ledgers, printing one line per ledger as it lands."""
    results, errors = {}, {}

    def call(L):
        t = dt.datetime.now()
        try:
            return L, fn(L, *args), None, (dt.datetime.now() - t).total_seconds()
        except Exception as e:
            return L, None, e, (dt.datetime.now() - t).total_seconds()

    with ThreadPoolExecutor(workers) as ex:
        for fut in as_completed([ex.submit(call, L) for L in ledgers]):
            L, res, err, secs = fut.result()
            if err:
                errors[L.id] = err
                print(f"    {label} {L.id:<22} ERROR after {secs:.0f}s: {str(err)[:100]}", flush=True)
            else:
                results[L.id] = res
                n = len(res) if hasattr(res, "__len__") else res
                print(f"    {label} {L.id:<22} {n} in {secs:.0f}s", flush=True)
    return results, errors
