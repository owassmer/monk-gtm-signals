# Data sources

The pipeline uses two kinds of public record: government payment ledgers, which show every payment
a government made to a vendor, and federal contract awards from USAspending.

## Payment ledgers

All 19 ledgers are published through the Socrata open data API, so one client reads all of them.
Each government names its columns differently. `config/ledgers.yaml` maps each ledger's columns to
one schema.

| Field | Meaning |
|---|---|
| `vendor` | Payee name as the government records it |
| `paid` | Date the payment left the government |
| `amount` | Dollar amount of the payment line |
| `unit` | Paying department |
| `invoice_date` | Invoice date, only where verified (see below) |
| `invoice_date_meaning` | `issued` if the date is when the vendor issued the invoice, `received` if it's when the government received it |
| `due_date`, `invoice_id`, `description` | Mapped where the ledger has them |

### Ledgers with a verified invoice date

These five ledgers support payment-delay measurement.

| ID | Government | Invoice date column | Meaning |
|---|---|---|---|
| `los_angeles` | Los Angeles, CA | `inv_date` | Received |
| `fulton_county` | Fulton County, GA | `vendor_invoice_date` | Received |
| `baton_rouge` | Baton Rouge / East Baton Rouge Parish, LA | `invoice_date` | Issued |
| `montgomery_county_md` | Montgomery County, MD | `inv_date` | Issued |
| `moreno_valley` | Moreno Valley, CA | `invoice_date` | Issued |

### Ledgers with payment dates only

These 14 ledgers show which governments and departments pay a vendor, and when.

| ID | Government | Notes |
|---|---|---|
| `dallas` | Dallas, TX | The dataset holds the current fiscal year only. |
| `kansas_city` | Kansas City, MO | Three yearly datasets (2024, 2025, 2026). Amount uses `sum_amount`, the per-voucher amount, because `payment_amount` is the whole check. |
| `cincinnati` | Cincinnati, OH | |
| `mesa` | Mesa, AZ | Payee names are stored without spaces, such as `PeregrineTechnologiesInc`. |
| `atlanta` | Atlanta, GA | |
| `riverside_county` | Riverside County, CA | |
| `ramsey_county` | Ramsey County, MN | |
| `providence` | Providence, RI | |
| `college_station` | College Station, TX | Some payee names are masked, such as `Lexip...#5401`. These are never matched to a company. |
| `mesquite` | Mesquite, TX | |
| `corona` | Corona, CA | |
| `cook_county` | Cook County, IL | |
| `connecticut` | State of Connecticut | The agency name is in `program`. The `invoice_date` column equals the payment date in every tested row, so it isn't mapped. |
| `delaware` | State of Delaware | |

## Invoice date verification

A column is mapped as `invoice_date` only if it behaves like an invoice date in the live data. The
check takes the latest 5,000 payments in the ledger, computes payment date minus invoice date for
each, and requires all of the following:

- Fewer than 5% of gaps are negative.
- Fewer than 50% of gaps are zero.
- The median gap is greater than zero.

Run `python -m monk_signals ledgers --verify` to repeat the check. Connecticut fails it because its
column is an accounting date.

## Federal contract awards

`collect` queries the USAspending `spending_by_award` endpoint for each target. It requests contract
award types A through D from the last three years. USAspending's recipient search is fuzzy, so an
award is kept only if the recipient name contains one of the target's known names as whole words.
All federal awards are stored under one payer, `U.S. Federal government`, with the awarding
sub-agency as the department.

## Adding a ledger

1. Find the dataset ID on the government's Socrata portal.
2. Add an entry to `config/ledgers.yaml` with the column mapping. Map `invoice_date` only after
   running `ledgers --verify` and confirming it passes.
3. Run `discover` to classify the new government's departments and screen its payees, then
   `collect`.
