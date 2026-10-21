# monk-gtm-signals

This project finds companies that sell to police, fire, 911, courts, and crime labs, and flags the
ones whose government customers pay slowly or are adding new paying departments. It works from
public government payment records and sends flagged accounts to Clay and HubSpot.

Many cities, counties, and states publish every vendor payment they make, often with the invoice
date. With both dates, you can measure how long each department takes to pay a given vendor. For
example, the Port of Los Angeles pays Lexipol a median of 93 days after receiving an invoice, and
LA Recreation and Parks paid the same vendor within 9 days.

## How it works

1. The pipeline reads payment ledgers from 19 governments and federal contract awards from
   USAspending, and maps them to one payment schema.
2. A Claude agent classifies each paying department, screens the vendors that public-safety
   departments pay, and checks each candidate on the web.
3. Every payment and award is stored as a dated event in SQLite.
4. The pipeline scores each vendor from its events and assigns signals and a tier.
5. New and changed accounts go to Clay for enrichment and approval, and Clay writes them to
   HubSpot.

## Documentation

| Document | Contents |
|---|---|
| [Data sources](docs/data-sources.md) | The 19 ledgers, how their columns map to one schema, how invoice dates are verified, and the federal awards source |
| [Pipeline](docs/pipeline.md) | What `discover`, `verify`, and `collect` do, the agent tasks, name matching, and the database tables |
| [Scoring](docs/scoring.md) | How each signal, tier, and headline is calculated |
| [Clay](docs/clay.md) | The Companies and Signal Feed tables, every column, and their run conditions |
| [HubSpot](docs/hubspot.md) | Custom properties, outcome codes, and how the outcome report is calculated |
| [Operations](docs/operations.md) | Setup, the daily run, and what to do in Clay when changes arrive |

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add the HubSpot service key and Clay webhook URLs
source .venv/bin/activate
python -m monk_signals queue  # score every target from the local database
```

## Commands

Run each command as `python -m monk_signals COMMAND`.

| Command | What it does |
|---|---|
| `ledgers [--verify]` | Lists the payment ledgers. `--verify` re-tests each mapped invoice date column on live data. |
| `discover` | Classifies departments, screens the vendors that public-safety departments pay, and saves targets. |
| `verify` | Checks each target on the web for domain, ownership, size, and sales channel. |
| `collect` | Pulls invoice-level payments and federal awards for every target into `signals.db`. |
| `queue [--as-of DATE]` | Scores every target and writes `out/queue.csv`. |
| `changes` | Lists events first seen in the latest `collect` run. |
| `backtest [--months N]` | Prints the queue for each past month and what happened in the following 90 days. |
| `push-clay [--dry-run]` | Sends new accounts to Clay's Companies table and changes to its Signal Feed table. |
| `hubspot-setup` | Creates or updates the "Monk signals" company properties in HubSpot. |
| `hubspot-report` | Groups HubSpot outcome codes by signal and week flagged, and writes `out/cohorts.csv`. |
| `run` | Runs `collect`, `queue`, and `push-clay`. |

`samples/` contains real output from October 20, 2026.
