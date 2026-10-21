# Operations

## Setup

1. Install Python 3 (tested on 3.14), then create the environment:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```

2. Install the Claude Code CLI and sign in by running `claude` once. The agent tasks use this login.
3. Copy `.env.example` to `.env` and set these variables.

   | Variable | Value |
   |---|---|
   | `HUBSPOT_SERVICE_KEY` | HubSpot service key. See [HubSpot](hubspot.md#authentication). |
   | `CLAY_WEBHOOK_URL`, `CLAY_WEBHOOK_TOKEN` | Webhook URL and auth token of the Companies table |
   | `CLAY_SIGNAL_FEED_URL`, `CLAY_SIGNAL_FEED_TOKEN` | Webhook URL and auth token of the Signal Feed table |
   | `SOCRATA_APP_TOKEN` | Optional. Raises Socrata rate limits. |
   | `MONK_AGENT_MODEL` | Optional. Default model for agent tasks. |

4. Run `python -m monk_signals hubspot-setup` to create the HubSpot properties.

## First run

```bash
python -m monk_signals discover   # about 45 minutes, mostly agent screening
python -m monk_signals verify     # about 10 minutes
python -m monk_signals collect    # about 1 minute
python -m monk_signals queue
python -m monk_signals push-clay --dry-run
python -m monk_signals push-clay
```

`push-clay --dry-run` writes the payloads to `out/clay_payloads.jsonl` without sending them.

## Daily run

`python -m monk_signals run` pulls new payments and awards, rescores every target, and sends new and
changed accounts to Clay. Schedule it with cron. For example, to run at 6 AM every day:

```
0 6 * * * cd /path/to/monk-gtm-signals && .venv/bin/python -m monk_signals run >> out/run.log 2>&1
```

`python -m monk_signals changes` lists the records first seen in the latest run.

## In Clay after a run

New accounts arrive in Companies. Changes arrive in Signal Feed.

For changed accounts:

1. Run Signal Updates on all rows. The Signal column and every signal column update.
2. Run Gov AR Issue & Timing and then First Line on the approved rows that changed.
3. Run HubSpot Sync on the same rows.

For new accounts:

1. Run Enrich company, then Finance Signals, Find Finance Buyer, Buyer Pick, Gov AR Issue & Timing,
   and First Line.
2. Review each row and check Approved for CRM.
3. Run the email waterfall, then HubSpot Lookup, HubSpot Company, and HubSpot Contact.

Run each paid column on one row first to check its credit cost.

## Rerunning discovery

Run `discover` and `verify` again when you add a ledger, change the exclude list, or want to pick up
vendors that started selling to public-safety departments. Agent results are cached, so a rerun
only screens departments and payees it hasn't seen before.

## Excluding a company

Add the name to `config/exclude.yaml` under a reason, and run `queue` and `push-clay`. For
exclusions you don't want in the repository, use `config/exclude.local.yaml` with the same format.
It's read automatically and isn't committed. If the company was already in Clay, it receives a
`removed` record in Signal Feed.
