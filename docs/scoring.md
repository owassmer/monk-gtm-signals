# Scoring

`monk_signals/score.py` scores each target from its events. Scoring is point in time. `queue
--as-of DATE` uses only events dated on or before that date, so the same code produces today's
queue and any past queue.

## Windows

| Window | Length | Used for |
|---|---|---|
| New | 90 days | `NEW_PAYER` and `EXPANDING` |
| Breadth | 24 months | Payer and department counts |
| Payment delay | 18 months | Days-to-pay medians |
| Prior history | 24 months | Confirming a payer is new |

Payments, approvals, renewals, and awards all count as payer events.

## Signals

### NEW_PAYER

A government's first payer event for this vendor falls in the last 90 days, and the ledger has at
least 24 months of history before that date. The history requirement comes from the `coverage`
table. Without it, a government whose dataset starts recently would look new. Dallas publishes
only its current fiscal year, so it can't produce this signal.

### EXPANDING

A department, by function, makes its first payer event for this vendor in the last 90 days. The
same government already paid the vendor through a different department, and the ledger has at
least 24 months of history before the new department's first event.

### SLOW_PAYER

For each payment with an invoice date in the last 18 months, the delay is the payment date minus
the invoice date. Delays below 0 or above 540 days are discarded as data errors. Delays are grouped
by government, department, and invoice date meaning. A group needs at least 3 invoices. The group
with the highest median is the slowest payer. The signal applies when that median is 45 days or
more.

### PAYER_SPRAWL

Three or more governments, or six or more departments, had payer events in the last 24 months.

### CONTRACT_RISK

A council voted to end or oppose a contract in the last 24 months. Council records aren't collected
at the moment, so this signal doesn't appear.

## Tiers

| Tier | Rule |
|---|---|
| `act_now` | At least one trigger (`NEW_PAYER` or `EXPANDING`) and at least one pain signal (`SLOW_PAYER` or `PAYER_SPRAWL`) |
| `watch` | Any one of those four signals |
| `skip` | None of them |

The account's `source_signal` is its first trigger. If it has no trigger, it's the first pain
signal. HubSpot uses `source_signal` to group outcomes.

## Headline

The headline is built from up to three clauses, in this order:

1. Up to two new payers, with government, department, date, and amount.
2. One new department, with government, department, and date.
3. The slowest payer, with its median delay and invoice count. When another department's median is
   half or less, the comparison is added. The other department's government is named if it differs.
4. The payer and department counts, when `PAYER_SPRAWL` applies.

The evidence URL is the record behind the first clause that has one. The counts clause has no
single record.

## Queue columns

`out/queue.csv` has one row per target, sorted by tier, then number of governments, then dollars
paid.

| Column | Meaning |
|---|---|
| `tier`, `source_signal`, `reasons` | Tier, leading signal, and every signal present |
| `headline`, `evidence_url`, `evidence_urls` | Plain-language evidence and up to four record links |
| `governments_24m`, `departments_24m` | Distinct governments and departments with payer events in 24 months |
| `new_payers_90d`, `new_departments_90d` | Each new payer or department with its date |
| `median_days_to_pay` | Median delay across all invoices with an invoice date |
| `slowest_payer`, `slowest_payer_days` | Slowest department and its median delay |
| `paid_24m` | Dollars paid by ledgers in 24 months |
| `last_public_record` | Date of the most recent payer event |
| `payers` | Governments with payer events in 24 months |
| `ownership`, `size_band`, `sells_direct`, `why_target` | Fields from `verify` |

## Backtest

`backtest --months N` scores the queue as of each of the last N months. For each account that
newly reaches `act_now`, it shows how many more governments started paying in the following 90
days. A replay assumes each record was available on its event date. In practice, ledgers post
payments some days or weeks later.
