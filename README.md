# monk-gtm-signals

This project finds companies that sell to police, fire, 911, courts, and crime labs, and flags the
ones whose government customers pay slowly or are adding new paying departments. It works from
public government payment records and sends flagged accounts to Clay and HubSpot.

## Background

Many cities, counties, and states publish every vendor payment they make, often with the invoice
date. With both dates, you can measure how long each department takes to pay a given vendor. For
example, the Port of Los Angeles pays Lexipol a median of 93 days after receiving an invoice, and
LA Recreation and Parks paid the same vendor within 9 days.

Vendors to public-safety agencies bill many separate departments, each with its own purchase
orders, approvers, and payment timing. Several Monk customers, such as Othram and Subject, bill
public agencies the same way.

## How it works

1. The pipeline reads payment ledgers from 19 governments and federal contract awards from
   USAspending. `config/ledgers.yaml` maps each government's columns to one payment schema.
2. A Claude agent, run through the local Claude Code CLI, classifies each paying department,
   screens the vendors that public-safety departments pay, and checks each candidate on the web for
   ownership, size, and whether agencies pay it directly. Each decision is cached in the database
   with its reasoning, so it's made once.
3. Every payment and award is stored as an event in a SQLite database. Each event keeps the date it
   happened and the date the pipeline first saw it.
4. The pipeline scores each vendor from its events and assigns signals and a tier.
5. New and changed accounts are sent to Clay, which enriches them and writes them to HubSpot after
   a person approves them.

### Signals

| Signal | Meaning |
|---|---|
| `SLOW_PAYER` | A department takes a median of 45 or more days to pay after the invoice date, across at least 3 invoices. |
| `EXPANDING` | A new department inside an existing government customer started paying in the last 90 days. |
| `NEW_PAYER` | A government paid the vendor for the first time in the last 90 days, with two years of history showing no earlier payments. |
| `PAYER_SPRAWL` | Three or more governments, or six or more departments, paid the vendor in the last 24 months. |

An account is `act_now` when it has `NEW_PAYER` or `EXPANDING` together with `SLOW_PAYER` or
`PAYER_SPRAWL`. It's `watch` when it has any one of them. The first signal on an account becomes
its `source_signal` in HubSpot.

Because events keep their real dates, `queue --as-of DATE` rebuilds the queue from records dated on
or before that day, and `backtest` shows what the queue would have flagged each month.

## Data quality checks

- A column is used as an invoice date only if the gap between invoice and payment looks like a real
  payment delay in the live data. Run `python -m monk_signals ledgers --verify` to repeat the check.
  Connecticut's invoice date column matches the payment date in every row, and Pittsburgh publishes
  a general ledger, so neither is used to measure payment delay.
- Payee names that a government masks, such as College Station's `Lexip...#5401`, are never matched
  to a company.
- Vendors in `config/exclude.yaml` are never flagged. The list covers Monk customers, large
  incumbents, resellers, and brand-risk companies.

## Set up

1. Create a virtual environment and install the dependencies:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in the HubSpot service key and the two Clay webhook URLs
   and tokens.
3. Sign in to the Claude Code CLI (`claude`). The agent steps use your existing login.

## Commands

Run each command as `python -m monk_signals COMMAND`.

| Command | What it does |
|---|---|
| `discover` | Classifies departments, screens the vendors that public-safety departments pay, and saves targets. |
| `verify` | Checks each target on the web for domain, ownership, size, and sales channel. |
| `collect` | Pulls invoice-level payments and federal awards for every target into `signals.db`. |
| `queue` | Scores every target and writes `out/queue.csv`. Add `--as-of DATE` for a past date. |
| `backtest` | Prints the queue for each of the last 6 months and what happened next. |
| `push-clay` | Sends new and changed accounts to Clay. Add `--dry-run` to preview. |
| `hubspot-setup` | Creates or updates the "Monk signals" company properties in HubSpot. |
| `hubspot-report` | Groups HubSpot outcome codes by signal and week flagged, and writes `out/cohorts.csv`. |
| `run` | Runs `collect`, `queue`, and `push-clay`. Use this for a daily job. |

## Clay and HubSpot

Clay webhook tables only add rows, so the push uses two tables. New accounts go to the Companies
table. Later changes go to the Signal Feed table, and Companies reads the latest version with a
lookup on the company name. Only accounts whose signal changed are sent, so enrichment spend follows
changes instead of list size.

In Clay, each account gets company data, a finance contact and email, an AR hypothesis, and an
opening line. A person approves each account. Clay then looks the company up in HubSpot by domain,
creates it if it doesn't exist, and updates it on later changes.

In HubSpot, reps set an outcome code on each company they work. `hubspot-report` groups those
outcomes by signal so you can see which signals lead to real conversations.

## Sample output

`samples/` contains real output from October 20, 2026.
