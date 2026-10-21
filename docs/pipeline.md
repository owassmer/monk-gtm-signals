# Pipeline

The pipeline has three stages. `discover` finds candidate vendors, `verify` checks them on the web,
and `collect` pulls their full payment history. Code handles fetching, aggregation, and dates. A
Claude agent handles decisions about meaning, such as what a department does or whether a payee is
a target.

All three stages write to `signals.db`, a SQLite file in the repository root.

## discover

`discover` builds the list of target vendors from what public-safety departments actually pay.

1. For each ledger, it lists every paying department with payments in the last three years. The
   October 2026 run found 2,931 departments across 19 governments.
2. The agent assigns each department one of 19 functions. The first ten count as public safety.

   | Public safety | Other |
   |---|---|
   | Police, Sheriff, Fire & EMS, 911 & Emergency Management, Courts, Corrections & Probation, Prosecutor, Public Defender, Medical Examiner & Crime Lab, Other public safety | Airport, Port & Harbor, Utilities, Transportation & Public Works, Health & Human Services, Parks & Libraries, IT, Finance & Administration, Other |

3. For the public-safety departments, it pulls every payee with its department, month, payment
   count, and total. Spellings that differ only in case, punctuation, or legal suffix are grouped
   together.
4. It drops payees paid less than $10,000 in total, payees with no payment in the last 18 months,
   and payees on the exclude list. See [Operations](operations.md#excluding-a-company).
5. The agent screens each remaining payee against Monk's ideal customer profile and returns a
   company name, whether it's a target, a category, a domain if known, a confidence level, and a
   reason.
6. Payees that the agent gave the same domain or company name are merged into one company.

The October 2026 run grouped 70,260 payees, screened 11,248, and kept 298 targets out of 10,885
companies. The screened list is written to `out/payees_screened.csv`.

## verify

`verify` sends each target to the agent with web search and web fetch enabled. For each company,
the agent returns:

| Field | Values |
|---|---|
| `domain`, `domain_source` | Official website and a URL that confirms it |
| `ownership`, `ownership_source` | `venture-backed`, `growth-equity`, `PE-owned`, `subsidiary of larger company`, `public`, `founder-owned/bootstrapped`, or `unknown` |
| `size_band` | `<30`, `30-200`, `200-1500`, `>1500`, or `unknown` |
| `sells_direct` | `direct`, `mixed`, `reseller`, or `unknown` |
| `is_target`, `reason` | Final decision and the evidence for it |

Companies the agent marks as non-targets are excluded with its reason. After verification and the
exclude list, 87 targets remain.

## collect

`collect` pulls the full record for each target.

1. It gathers every spelling of the target's name from every ledger, adds uppercase and title-case
   versions, and skips masked names. It then queries every ledger for exact matches on those
   spellings. This finds payments made by departments outside public safety, such as the Port of
   Los Angeles paying Lexipol.
2. It aggregates payment lines into one event per invoice, keyed on payee spelling, department,
   invoice ID, invoice date, payment date, and dataset. Invoices that net to zero or less, such as
   voids and credits, are dropped.
3. It queries USAspending for each target's federal contract awards. See
   [Data sources](data-sources.md#federal-contract-awards).
4. It records each ledger's earliest available date in the `coverage` table. Scoring uses this to
   decide whether enough history exists to call a payer new.

Each event's `url` is a Socrata API query that returns the exact rows behind it.

## Agent tasks

The agent runs through the local Claude Code CLI (`claude -p`) with structured JSON output. It uses
your existing Claude login, so no API key is needed.

| Task | Used in | Model | Batch size | Tools |
|---|---|---|---|---|
| `department_function` | `discover` | Haiku | 150 | None |
| `payee_screen` | `discover` | Sonnet | 70 | None |
| `target_verify` | `verify` | Sonnet | 4 | Web search, web fetch |

Every result is stored in the `judgments` table, keyed by task and a hash of the input. If the same
input comes up again, the stored result is reused, so reruns only send new items to the agent. Set
`MONK_AGENT_MODEL` to change the default model.

The profile the agent screens against is defined in `monk_signals/agent.py`. A target sells its own
product or service directly to government agencies, with public safety or justice as a core market.
It's privately held with roughly 30 to 1,500 employees, and it sells technology, data, devices with
software, or specialized lab or professional services.

## Name matching

`monk_signals/names.py` normalizes names in two ways.

- `vendor_key` lowercases a name, splits it into words, drops a leading "the" and trailing legal
  suffixes such as Inc, LLC, and Corp, and joins the rest. `LEADSONLINE LLC` and `LeadsOnline, LLC`
  both become `leadsonline`.
- `matches` checks whether one name appears inside another as a sequence of whole words. "Acme"
  doesn't match "PACMEN SUPPLY". CamelCase names like Mesa's are split before matching.

## Database tables

| Table | Contents |
|---|---|
| `events` | One row per payment or award: vendor, kind, source, paying government, department, department function, event date, invoice date, invoice date meaning, due date, amount, reference, payee spelling, description, evidence URL, and the dates first and last seen |
| `companies` | One row per company from `discover`, with target status, category, domain, payee spellings by ledger, exclusion reason, and the `verify` fields |
| `units` | Each ledger department and the function the agent assigned it |
| `judgments` | Every agent result, with its input, output, model, and timestamp |
| `coverage` | Earliest available date per source and government |
| `runs` | Start and end time and counts for each `discover` and `collect` run |
| `pushes` | For each company sent to Clay, a hash of the last payload and the dates first and last pushed |
