"""Council agenda items (Legistar) and federal contract awards (USAspending)."""

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import names
from ..util import config, http_json, money, stable_id, to_date, to_float, today

FEDERAL = "U.S. Federal government"
KIND = {"new_contract": "approval", "purchase": "approval", "renewal_or_amendment": "renewal",
        "termination_or_opposition": "termination"}


def councils():
    return config("councils.yaml")["councils"]


def _search_term(company):
    t = names.core_tokens(company)
    if not t:
        return None
    # The most distinctive leading word is enough to find candidates; the agent decides relevance.
    return t[0] if len(t[0]) >= 5 or len(t) == 1 else " ".join(t[:2])


def council_candidates(companies, since):
    """All agenda items whose title mentions a company's name -> candidate list for the agent."""
    terms = {}
    for c in companies:
        if (t := _search_term(c)):
            terms.setdefault(t, []).append(c)
    term_list = sorted(terms)
    out, seen = [], set()

    def pull(client, chunk):
        ors = " or ".join(f"substringof('{t.replace(chr(39), chr(39) * 2)}',MatterTitle) eq true" for t in chunk)
        f = f"({ors}) and MatterIntroDate ge datetime'{since.isoformat()}'"
        try:
            return client, chunk, http_json(f"https://webapi.legistar.com/v1/{client}/matters",
                                            params={"$filter": f, "$top": 1000}, timeout=60, tries=3)
        except Exception as e:
            print(f"    legistar {client}: {str(e)[:80]}", flush=True)
            return client, chunk, []

    jobs = [(cl, term_list[i:i + 8]) for cl in councils() for i in range(0, len(term_list), 8)]
    with ThreadPoolExecutor(12) as ex:
        for fut in as_completed([ex.submit(pull, *j) for j in jobs]):
            client, chunk, matters = fut.result()
            for m in matters:
                title = " ".join((m.get("MatterTitle") or "").split())
                d = to_date(m.get("MatterIntroDate") or m.get("MatterAgendaDate") or m.get("MatterPassedDate"))
                if not d or d > today() + dt.timedelta(days=60):     # mistyped future dates
                    continue
                for t in chunk:
                    if t.lower() not in title.lower():
                        continue
                    for company in terms[t]:
                        key = (client, m.get("MatterId"), company)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append({"id": stable_id("legistar", *key), "company": company,
                                    "government": councils()[client], "title": title[:600],
                                    "_client": client, "_date": d.isoformat(),
                                    "_url": f"https://{client}.legistar.com/LegislationDetail.aspx?ID={m.get('MatterId')}&GUID={m.get('MatterGuid')}"})
    return out


def council_events(candidates, judgments):
    events = []
    for c in candidates:
        j = judgments.get(c["id"])
        if not j or not j.get("is_party") or j.get("kind") not in KIND:
            continue
        events.append({"event_id": c["id"], "vendor": c["company"], "kind": KIND[j["kind"]],
                       "source": f"legistar:{c['_client']}", "jurisdiction": c["government"],
                       "unit": "", "unit_class": "", "event_date": c["_date"],
                       "amount": j.get("amount") or None, "title": j.get("summary") or c["title"][:300],
                       "url": c["_url"], "detail": {"agenda_title": c["title"]}})
    return events


def federal_events(company, aliases, since):
    body = {"filters": {"recipient_search_text": [company], "award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": since.isoformat(), "end_date": today().isoformat()}]},
            "fields": ["Award ID", "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
                       "Award Amount", "Start Date", "Description", "generated_internal_id"],
            "limit": 100, "page": 1, "sort": "Start Date", "order": "desc"}
    rows = http_json("https://api.usaspending.gov/api/v2/search/spending_by_award/", method="POST",
                     json_body=body, timeout=60, tries=3).get("results", [])
    events = []
    for x in rows:
        recip = x.get("Recipient Name") or ""
        if not any(names.matches(recip, a) for a in aliases):
            continue
        d = to_date(x.get("Start Date"))
        if not d:
            continue
        unit = x.get("Awarding Sub Agency") or x.get("Awarding Agency") or ""
        events.append({"event_id": stable_id("usaspending", x.get("generated_internal_id")), "vendor": company,
                       "kind": "award", "source": "usaspending", "jurisdiction": FEDERAL, "unit": unit,
                       "unit_class": "Federal agency", "event_date": d.isoformat(),
                       "amount": to_float(x.get("Award Amount")), "ref": x.get("Award ID"), "raw_name": recip,
                       "title": (x.get("Description") or "")[:300],
                       "url": f"https://www.usaspending.gov/award/{x.get('generated_internal_id')}",
                       "detail": {"agency": x.get("Awarding Agency")}})
    return events
