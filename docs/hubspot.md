# HubSpot

HubSpot stores each approved account with the signal that sourced it, and records what happened
when a rep worked it. Clay creates and updates company records. The pipeline creates the
properties and reads outcomes back.

## Properties

`python -m monk_signals hubspot-setup` creates these company properties in a group named "Monk
signals". Running it again updates labels, descriptions, and options.

| Property | Label | Type | Written by |
|---|---|---|---|
| `source_signal` | Source signal | Dropdown: `NEW_PAYER`, `EXPANDING`, `SLOW_PAYER`, `PAYER_SPRAWL` | Clay |
| `signal_tier` | Signal tier | Dropdown: `act_now`, `watch` | Clay |
| `signal_reasons` | Signal reasons | Text | Clay |
| `signal_headline` | Signal headline | Multi-line text | Clay |
| `signal_evidence_url` | Signal evidence URL | Text | Clay |
| `signal_first_flagged` | Signal first flagged | Date | Clay |
| `governments_24m` | Government payers (24 mo) | Number | Clay |
| `new_payers_90d` | New government payers (90 days) | Multi-line text | Clay |
| `slowest_payer` | Slowest government payer | Text | Clay |
| `slowest_payer_days` | Slowest payer median days to pay | Number | Clay |
| `median_days_to_pay` | Median days to pay (gov) | Number | Clay |
| `ar_hypothesis` | AR hypothesis | Multi-line text | Clay |
| `channel_check` | Channel / AR stack check | Multi-line text | Clay |
| `signal_first_line` | Outreach first line | Multi-line text | Clay |
| `disposition` | Signal disposition | Dropdown, see below | Reps |

Clay also sets the standard Company name and Company domain name properties, and creates one
contact per company with first name, last name, job title, and email.

## Outcome codes

A rep sets Signal disposition after working an account. Each code says something about the signal
that sourced the account.

| Code | Meaning | What it says about the signal |
|---|---|---|
| `next_step` | A meeting or evaluation is booked | The signal works |
| `problem_confirmed` | The AR problem is real, but the timing is wrong | The signal is right; revisit later |
| `not_now` | The problem may exist, with no appetite now | Neutral |
| `already_solved` | They already use an AR tool | The signal is right, but the account is taken |
| `no_problem` | Government invoicing isn't painful for them | The signal misfired |
| `wrong_person` | The contact doesn't own AR | The contact selection needs work, not the signal |
| `reseller_billed` | Agencies pay a reseller, not the company | The signal misfired; the channel check missed it |

## Outcome report

`python -m monk_signals hubspot-report` reads every company with a `source_signal` and groups them
by signal and by the week they were first flagged. Weeks start on Monday. It prints the table and
writes `out/cohorts.csv`.

| Column | Calculation |
|---|---|
| `cohort_week` | Monday of the week in `signal_first_flagged`, or the create date if that's empty |
| `source_signal` | The signal that sourced the accounts |
| `age_weeks` | Weeks since the cohort was flagged |
| `accounts` | Companies in the group |
| `worked` | Share with any disposition |
| `reached_owner` | Share with a disposition other than `wrong_person` |
| `problem_confirmed` | Share with `problem_confirmed` or `next_step` |
| `next_step` | Share with `next_step` |
| `signal_misfire` | Share with `no_problem` or `reseller_billed` |

Compare cohorts at the same age. A signal whose cohorts keep showing a high misfire share can be
cut or reweighted within a few weeks, before any deal closes.

## Record layout

The company record's About this company card shows the Monk signals properties. To change it, go
to **Settings > Data Management > Objects > Companies > Record Customization > Default view**, and
edit the card.

## Authentication

The pipeline uses a HubSpot service key with these scopes: `crm.objects.companies.read`,
`crm.objects.companies.write`, `crm.schemas.companies.read`, `crm.schemas.companies.write`, and
`crm.objects.contacts.read`. Set it as `HUBSPOT_SERVICE_KEY` in `.env`. Clay uses its own HubSpot
connection.
