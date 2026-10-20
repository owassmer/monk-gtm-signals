# monk-gtm-signals

Finds companies whose accounts receivable is getting harder, using what governments publish
about the invoices they pay.

## Thesis

Monk's customers share a shape: a fast-growing seller with a lean finance team, billing many
payers that each have their own rules. Othram bills law-enforcement agencies per case; Subject
bills hundreds of school districts. Vendors to police, sheriffs, courts, 911 and crime labs have
that shape at full strength, and **their customers publish their payables**: cities, counties and
states release every vendor payment, many with the invoice date next to the payment date.

So the AR problem is visible from outside. From Checkbook LA: the Port of LA's Harbor department
pays Lexipol a median 93 days after receiving its invoices, while LA Recreation and Parks paid the
same vendor in 9. Same city, different department, different rules. That is what Monk automates.

## How it works

```
19 government payment ledgers ─┐
USAspending federal awards ────┴─> event store (SQLite) ─> point-in-time scoring ─> Clay ─> HubSpot
                                   two clocks per record      reason codes, tiers     enrich,   dispositions
                                   (happened / first seen)                            buyer, AI ─> cohort report
```

**Code gathers facts; an agent decides meaning.** Judgments run through the local Claude Code CLI
(`claude -p`, structured JSON output, web search where needed), are cached in the database with
their reasoning, and are made once:

| Judgment | Why it isn't a regex |
|---|---|
| What function each of ~2,900 departments across 19 governments is | "MOHSEP", "HARBOR", "Genl Fd Police Administration", "Court Finance" |
| Which of ~11,000 payees of public-safety departments are targets, and which spellings are one company | Discovery has to find vendors nobody listed in advance |
| Web verification of each target: domain, ownership, size, whether agencies pay it directly | Resellers, PE roll-ups and subsidiaries look like vendors in a ledger |

**Schemas are unified and checked against the data.** `config/ledgers.yaml` maps each
government's columns onto one payment schema. A column is used as an invoice date only if
`paid - invoice_date` behaves like one on live data (`python -m monk_signals ledgers --verify`).
Connecticut's `invoice_date` equals the payment date in every row and Pittsburgh's checkbook is a
general ledger, so neither is used for payment lag. Masked payee names (College Station publishes
"Lexip...#5401") are never matched to a company.

**Reason codes, not a black-box score.**

| Code | Meaning |
|---|---|
| `NEW_PAYER` | A government paid or awarded the vendor for the first time in 90 days, with 24 months of visible history showing no prior relationship |
| `EXPANDING` | A new department inside an existing government customer started paying |
| `SLOW_PAYER` | A department takes a median 45+ days from invoice to payment (3+ invoices) |
| `PAYER_SPRAWL` | Paid by 3+ governments or 6+ departments in 24 months |

`act_now` = a trigger (new payer / expanding) plus pain (slow payer / sprawl); `watch` = either.
The leading reason becomes HubSpot `source_signal`, the key the feedback loop learns on.

**Point in time.** Every record keeps its real-world date and the date this system first saw it.
`queue --as-of 2026-06-01` rebuilds the queue from records dated on or before then; `backtest`
shows the queue a rep would have received each month and what the public record showed next.
(A replay assumes a record was available on its event date; ledgers post with a lag of days to
weeks, so live triggers arrive slightly later than the replay shows.)

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
python -m monk_signals discover        # departments -> public-safety payees -> agent screen -> targets
python -m monk_signals verify          # agent web research per target
python -m monk_signals collect         # invoice-level payments + federal awards -> signals.db
python -m monk_signals queue           # -> out/queue.csv
python -m monk_signals backtest        # monthly queues for the last 6 months
python -m monk_signals hubspot-setup   # "Monk signals" property group on companies
python -m monk_signals push-clay       # new accounts -> Companies; changes -> Signal Feed
python -m monk_signals hubspot-report  # weekly cohorts by source_signal -> out/cohorts.csv
python -m monk_signals run             # daily: collect + queue + push
```

## Division of labor

| System | Owns |
|---|---|
| This repo | Public records at scale, event history, agent judgments, scoring, backtests, CRM schema, cohort report |
| Clay | Firmographics, finance research (Claygent), finance buyer, email waterfall, AR hypothesis and first line, human approval, HubSpot create/update |
| HubSpot | System of record: signal properties, contacts, dispositions |

Clay webhook tables only insert rows, so new accounts land in **Companies** and every later change
lands in **Signal Feed**; Companies reads the latest signal back with a Lookup. Only accounts whose
signal changed are sent, so enrichment spend tracks change, not list size.

`samples/` holds real output from Oct 20, 2026.
