# Clay

Clay enriches each account, finds a finance contact, writes the hypothesis and opening line, holds
the approval step, and writes approved accounts to HubSpot. The workbook is "Monk Demo" and has two
tables.

## Tables

Clay webhook tables only add rows, so the integration uses two tables.

| Table | Receives | Purpose |
|---|---|---|
| Companies | New accounts from `push-clay` | One row per account. All enrichment, AI, approval, and HubSpot columns live here. |
| Signal Feed | Changed and removed accounts from `push-clay` | One row per change. Companies reads the latest version back with a lookup. |

`push-clay` compares a hash of each account's signal fields with the last one sent. Accounts that
haven't changed aren't sent. An account that no longer qualifies gets a record with tier `removed`.

### Signal Feed columns

| Column | Formula |
|---|---|
| Company | `{{Webhook}}?.company` |
| Signal Version | `{{Webhook}}?.signal_version`, the push timestamp |
| Payload | `JSON.stringify({{Webhook}})`, the full record as text |

## Companies columns

Columns are listed in the order they run. Auto-run is off for the table, so each column is run
manually.

### Identity, from the first push

Company, Domain, Category, Why Target, First Flagged, Ownership, Size Band, and Sells Direct read
directly from the Webhook column. These describe the company and don't change between pushes.

### Signal, from the latest push

| Column | Type | What it does |
|---|---|---|
| Signal Updates | Lookup multiple rows | Finds Signal Feed rows where Company matches. |
| Signal | Formula | Returns the Payload of the newest matching row by Signal Version. If there are none, returns the original Webhook record. |

These columns read from Signal with `JSON.parse({{Signal}} || "{}")?.FIELD`: Tier, Source Signal,
Reasons, Headline, Evidence Url, Evidence Urls, Slowest Payer, Slowest Payer Days, Median Days To
Pay, New Payers 90d, New Departments 90d, Governments 24m, Departments 24m, Paid 24m, Payers, Last
Public Record, Change, and As Of. Empty values are replaced with `none`, `n/a`, or `not enough dated
invoices`, because Clay's AI columns won't run when an input is empty.

Slowest Payer Days # and Median Days To Pay # hold the same two values as numbers for HubSpot.

### Enrichment and fit

| Column | Type | Run if | What it does |
|---|---|---|---|
| Enrich company | Clay company enrichment | Domain is set | Returns LinkedIn company data. Employee Count, Type, Industry, Size, Description, Company LinkedIn URL, Locality, and Founded are extracted. |
| Fit | Formula | | True when the company isn't public by LinkedIn type or by `verify` ownership, and has 30 to 1,500 employees. If Employee Count is missing, Size Band of `30-200` or `200-1500` counts. |
| Finance Signals | Claygent, Anthropic key | Fit | Researches open finance roles, the billing and AR tools the company uses, and its finance leaders. Extracted as Finance Roles, Ar Stack, and Finance Team. |

### Contact

| Column | Type | Run if | What it does |
|---|---|---|---|
| Find Finance Buyer | Surfe, find people at company | Fit and Sells Direct is `direct` or `mixed` | Finds one person titled Controller, VP Finance, Head of Finance, Director of Finance, or CFO. Extracted as Buyer Name, First, Last, Job Title, and LinkedIn. |
| Buyer Pick | AI, Anthropic key | Same as above | Chooses the best AR contact from the Surfe result and Finance Team. Rejects retired, former, and non-finance people. Never invents a name. Extracted as Contact Name, First, Last, Title, LinkedIn, Source, and Why. |

### Writing

| Column | Type | Run if | What it does |
|---|---|---|---|
| Gov AR Issue & Timing | AI, Anthropic key | Fit and Sells Direct is `direct` or `mixed` | Writes two sentences: the likely AR problem with government customers, and why now. Uses only the signal, company, and finance fields. Starts with `WEAK:` when evidence is thin. Extracted as Ar Hypothesis. |
| First Line | AI, Anthropic key | Contact Name, Contact Title, and Ar Hypothesis are set | Writes an opening sentence under 25 words anchored on the strongest public-record fact. |
| Channel Check | Formula | | Combines Sells Direct and Ar Stack into one line for HubSpot. |

### Approval and email

| Column | Type | Run if | What it does |
|---|---|---|---|
| Approved for CRM | Checkbox | | Set by a person after reading the hypothesis against the evidence. Nothing below runs without it. |
| Email waterfall | Find work email | Approved and Contact Last is set | Tries Findymail, Hunter, Prospeo, Kitt, Datagma, Wiza, Icypeas, Enrow, Dropcontact, LeadMagic, and SMARTe in order. Each result is validated with Findymail, and the first valid email goes into Contact Email. |

### HubSpot

| Column | Type | Run if | What it does |
|---|---|---|---|
| HubSpot Lookup | Look up object, Companies | Domain is set | Searches HubSpot for a company with the same domain. |
| HubSpot Company | Create object, Companies | Approved, Domain is set, the lookup ran, and it found nothing | Creates the company with the fields listed in [HubSpot](hubspot.md#properties). |
| HubSpot Company ID | Formula | | The created company's ID, or the ID the lookup found. |
| HubSpot Contact | Create object, Contacts | Company ID is set, Contact Last is set, and the lookup found nothing | Creates the contact and associates it with the company. Only runs for companies created in this pass, so contacts aren't duplicated. |
| HubSpot Sync | Update object, Companies | Company ID is set | Writes the current signal, hypothesis, opening line, and channel check to the existing company. Blank values don't overwrite HubSpot. |

## Views

The "Approved accounts" view filters to Approved for CRM and shows Company, Tier, Source Signal,
Headline, Evidence Url, Slowest Payer, Ar Hypothesis, Contact Name, Contact Title, Contact Why,
First Line, and Approved for CRM.
