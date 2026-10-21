"""The judgments this system delegates to an agent. Code gathers facts; these decide meaning."""

import json

from . import llm
from .names import FUNCTIONS

ICP = """Monk (monk.com) sells AI accounts-receivable automation (collections, cash application,
invoice submission to customer portals) to fast-growing B2B sellers with lean finance teams.
Its customers include Othram (forensic DNA services billed per case to law-enforcement
agencies) and Subject (curriculum sold to hundreds of school districts).

A TARGET is a company that:
- sells its own product or service directly to government agencies (so the agency's invoices
  are the company's own receivables), with public safety / justice / emergency services a core market;
- is a privately held growth-stage company: venture- or growth-equity-backed, or founder-led and
  scaling, roughly 30 to 1,500 employees;
- is technology, software, data, devices-with-software, or specialized lab/forensic/professional
  services built for these agencies.

NOT a target: public companies and large incumbents (Axon, Motorola Solutions, Tyler, Hexagon,
Verizon, Microsoft...), PE roll-ups with large finance teams, resellers and distributors
(Carahsoft, CDW, SHI...), utilities and telecom carriers, construction and facilities, vehicle and
fuel suppliers, uniforms and commodity supplies, local small businesses, individuals, nonprofits,
universities, hospitals, and other government bodies."""


def classify_departments(con, items):
    """items: [{id, government, department, payments}] -> {id: {function, public_safety}}"""
    schema = {"type": "object", "required": ["results"], "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["id", "function"],
        "properties": {"id": {"type": "string"}, "function": {"type": "string", "enum": FUNCTIONS}}}}}}
    system = ("You map government department names to the function that approves and pays vendor "
              "invoices. Departments are named inconsistently across governments; decide from meaning, "
              "e.g. 'MOHSEP' (Mayor's Office of Homeland Security & Emergency Preparedness) is 911 & "
              "Emergency Management, 'HARBOR' is Port & Harbor, 'Airport Police' is Police, "
              "'Municipal Court Revenues' is Courts. Return every id.")

    def build(batch):
        return "Classify each department.\n" + "\n".join(
            json.dumps({"id": b["id"], "government": b["government"], "department": b["department"]}) for b in batch)
    res = llm.judge_many(con, "department_function", items, build, schema, system, batch=150, workers=6, model="haiku")
    for v in res.values():
        v["public_safety"] = v.get("function") in FUNCTIONS[:10]
    return res


PAYEE_SCHEMA = {"type": "object", "required": ["results"], "properties": {"results": {"type": "array", "items": {
    "type": "object",
    "required": ["id", "company", "is_target", "category", "confidence", "reason"],
    "properties": {
        "id": {"type": "string"},
        "company": {"type": "string", "description": "Canonical company name, same string for every spelling of one company"},
        "is_target": {"type": "boolean"},
        "category": {"type": "string", "enum": ["software/data", "devices + software", "forensic/lab services",
                                                "training/policy content", "professional services", "reseller/distributor",
                                                "large incumbent", "utility/telecom", "commodity/supplies",
                                                "construction/facilities", "government/nonprofit", "individual", "other"]},
        "domain": {"type": "string", "description": "Company website domain if known with confidence, else empty"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string", "description": "Under 20 words"}}}}}}


def classify_payees(con, items):
    """items: [{id, names, governments, departments, payments, total, first, last, samples}]"""
    system = ("You are a GTM analyst screening vendors that government public-safety departments pay, "
              "to find accounts for Monk.\n\n" + ICP +
              "\n\nUse what you know about each company. If a payee name is ambiguous or unknown to you, "
              "use the departments, descriptions and amounts as evidence, set confidence low, and only mark "
              "is_target true when the evidence supports it. Give one id per input and return every id.")

    def build(batch):
        return "Screen these payees.\n" + "\n".join(json.dumps(b) for b in batch)
    return llm.judge_many(con, "payee_screen", items, build, PAYEE_SCHEMA, system, batch=70, workers=6)



VERIFY_SCHEMA = {"type": "object", "required": ["results"], "properties": {"results": {"type": "array", "items": {
    "type": "object",
    "required": ["id", "domain", "domain_source", "ownership", "size_band", "sells_direct", "is_target", "reason"],
    "properties": {
        "id": {"type": "string"},
        "domain": {"type": "string", "description": "Official website domain, e.g. leadsonline.com; empty only if none exists"},
        "domain_source": {"type": "string", "description": "URL that confirms the domain belongs to this company"},
        "ownership": {"type": "string", "enum": ["venture-backed", "growth-equity", "PE-owned", "subsidiary of larger company",
                                                 "public", "founder-owned/bootstrapped", "unknown"]},
        "ownership_source": {"type": "string"},
        "size_band": {"type": "string", "enum": ["<30", "30-200", "200-1500", ">1500", "unknown"]},
        "sells_direct": {"type": "string", "enum": ["direct", "mixed", "reseller", "unknown"],
                         "description": "Do government agencies pay this company directly?"},
        "is_target": {"type": "boolean"},
        "reason": {"type": "string", "description": "Under 25 words, cite what you found"}}}}}}


def verify_targets(con, items):
    """Web-research each candidate target. items: [{id, company, spellings, governments, departments, prior_reason}]"""
    system = ("You verify sales targets for Monk using web search. For each company, find its official website, "
              "ownership (funding, PE owner, parent company, or public listing) and approximate size, and whether "
              "government agencies buy directly from it. Payee spellings come from government payment ledgers; make "
              "sure the company you research is the one those agencies pay (match the product to the departments). "
              "Base every field on what you found, with sources; use 'unknown' rather than guessing.\n\n" + ICP)

    def build(batch):
        return "Verify each company.\n" + "\n".join(json.dumps(b) for b in batch)
    return llm.judge_many(con, "target_verify", items, build, VERIFY_SCHEMA, system, batch=4, workers=8,
                          model="sonnet", tools=["WebSearch", "WebFetch"], timeout=600)
