"""Federal contract awards (USAspending)."""

from .. import names
from ..util import http_json, stable_id, to_date, to_float, today

FEDERAL = "U.S. Federal government"


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
