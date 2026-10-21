import datetime as dt
import hashlib
import os
import re
import threading
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
OUT = ROOT / "out"

_local = threading.local()
UA = "monk-gtm-signals (+https://github.com/owassmer/monk-gtm-signals)"


def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def config(name):
    return yaml.safe_load((CONFIG / name).read_text())


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = UA
    return _local.s


def http_json(url, *, params=None, method="GET", json_body=None, headers=None, tries=5, timeout=120):
    """JSON request with retry/backoff on throttling and transient errors."""
    last = None
    for i in range(tries):
        try:
            r = session().request(method, url, params=params, json=json_body, headers=headers, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {r.text[:200]}", response=r)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (400, 401, 403, 404):
                raise
            time.sleep(min(30, 2 ** i))
    raise last


def today():
    return dt.date.today()


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")


def to_date(s):
    """'2026-08-13T00:00:00.000' | '2026-08-13' | '08/13/2026' -> date, else None."""
    if not s:
        return None
    if isinstance(s, dt.date):
        return s
    s = str(s).strip()
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        m = _MDY.match(s)
        if m:
            return dt.date(int(m[3]), int(m[1]), int(m[2]))
    return None


def to_float(x):
    try:
        return float(str(x).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def stable_id(*parts):
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:20]


def money(x):
    if x is None:
        return ""
    return f"${x:,.0f}" if abs(x) >= 1 else f"${x:,.2f}"
