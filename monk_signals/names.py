"""Mechanical name handling. Meaning (what a department is, which payee spellings are the
same company) is decided by the agent in agent.py; this module only normalizes strings."""

import re

LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "l", "c", "corp", "corporation", "co", "company", "ltd",
                  "limited", "lp", "llp", "plc", "pllc", "pc", "dba", "usa", "us", "na"}


def _camel_split(s):
    # Mesa stores "PeregrineTechnologiesInc"
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s or "")


def tokens(s):
    s = _camel_split(s).lower().replace("&", " and ")
    return re.findall(r"[a-z0-9]+", s)


def core_tokens(s):
    t = tokens(s)
    if t and t[0] == "the":
        t = t[1:]
    while t and t[-1] in LEGAL_SUFFIXES:
        t = t[:-1]
    return t


def vendor_key(s):
    """Groups trivially different spellings ('OTHRAM INC.' / 'Othram, Inc') before the agent
    resolves the harder cases."""
    return "".join(core_tokens(s))


def matches(text, name):
    """Whole-word sequence match ('Flock' does not match 'SELFLOCK SCREW PRODUCTS')."""
    a, t = core_tokens(name), tokens(text)
    n = len(a)
    return bool(a) and any(t[i:i + n] == a for i in range(len(t) - n + 1))


def clean_unit(raw):
    s = re.sub(r"\(\s*[\d\-\s]+\)", "", raw or "")
    return re.sub(r"\s+", " ", s).strip()


FUNCTIONS = ["Police", "Sheriff", "Fire & EMS", "911 & Emergency Management", "Courts",
             "Corrections & Probation", "Prosecutor", "Public Defender", "Medical Examiner & Crime Lab",
             "Other public safety", "Airport", "Port & Harbor", "Utilities", "Transportation & Public Works",
             "Health & Human Services", "Parks & Libraries", "IT", "Finance & Administration", "Other"]
PUBLIC_SAFETY = set(FUNCTIONS[:10])
