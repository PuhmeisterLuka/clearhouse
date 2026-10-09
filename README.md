# Clearhouse

A data pipeline that takes order exports from three systems that disagree about everything, works out what actually happened, and proves how well it did.

## The scenario

Velo Supply is a (fictional) Slovenian bike-parts distributor with three sales channels, each exporting orders its own way:

| Source | Format | What's awkward about it |
| --- | --- | --- |
| Shop till, Ljubljana | `pos_ljubljana_2025.csv` | Windows-1250 encoding, semicolons, Slovenian headers, a junk preamble and footer, decimal commas, local time with DST, one row per line item, anonymous walk-in sales |
| Webshop API | `webshop_orders_2025.json` | Nested orders, three timestamp styles (UTC, offset, epoch ms), EUR/USD/GBP, prices as strings or numbers, webhook retries, double-encoded names |
| Wholesale feed | `wholesale_2025.xml` | Prices in cents, dates as `YYYYMMDD`, buyer codes instead of emails, attribute drift between versions |

Click-and-collect web orders also get rung through the till when the customer picks them up, so they appear twice. Staff usually type the web order number into the till's `Ref` field, but not always.

Alongside the raw files there is reference data: a product catalog, a CRM customer list and monthly FX rates.

## Why synthetic data

The generator builds a clean "true" world first, then writes the messy exports from it and records every defect it injects in a ledger. Each ledger entry says what a correct pipeline should do: **fix** it, **quarantine** the order, or **dedupe** a copy. That makes the pipeline measurable. Instead of "the output looks clean", the report can say how many defects were caught, how many good orders were wrongly rejected, and whether every surviving order total matches the truth to the cent.

## Pipeline stages

Each stage looks at a wider scope than the one before: one value, then one order against reference data, then all orders together.

1. **Ingest.** One adapter per format, each producing the same raw record shape with lineage (file, row or element, raw value). Encoding detection, preamble skipping, nested-JSON flattening.
2. **Parse.** Validate and clean every value in one pass. Each field goes through a reusable parser that either returns a typed value, logging any repair (SKU formats, emails, mojibake, text quantities, buyer codes), or rejects it. Number formats, time zones and cents are read according to each source's format.
3. **Check.** Rules that need the whole order or the reference data: known SKUs, positive quantities, no dates in the future, a currency inferred from the customer's country when it's missing, 100x price slips against the catalog, repeated rows, and header totals against line sums.
4. **Reconcile.** Resolve every customer against the CRM, drop repeated records such as webhook retries, match till pickups to their web orders (by reference, then by customer, items and timing), and convert every total to EUR. Each record ends up with one status: clean, quarantined, or a duplicate copy of another record.
5. **Report.** Clean tables (orders, lines, customers), a quarantine file with reasons, an issue log, and an HTML data-quality report.
6. **Score.** Compare the pipeline's output with the ground truth and ledger: defect detection rates per kind, false quarantines, and value accuracy.

Every issue carries a severity. An **error** means the order can't be trusted and goes to quarantine, a **warning** means something was repaired, and **info** is worth knowing but needs no action. Issues about an order travel with that order through every stage, so the final quarantine decision can see all of them.

## Running it

```bash
uv run clearhouse generate
```

This writes `data/raw`, `data/reference` and `data/truth`. The pipeline must never read `data/truth`; only the scorer does.

```bash
uv run clearhouse run
```

Runs the pipeline over `data/raw`, prints what was found per source and a count of every issue by stage and code, and writes `out/orders.jsonl` (every record with its final status) and `out/issues.csv`. Orders dated after `--as-of` (default: today) are rejected as impossible.

## Development

Tests and lint (ruff covers unused imports and variables, so dead code fails the check):

```bash
uv run pytest && uv run ruff check .
```

## How ingestion works

Each adapter knows one format and nothing else. It maps that format's fields onto a shared `RawOrder` / `RawLine` shape and tags every record with where it came from (`line 214`, `orders[37].items[1]`, `Order[12]/Line[3]`), so any problem found later can be traced back to the exact spot in the source file.

Values are kept exactly as written. `"10,50"`, `" BRK-0042 "` and `"2 pcs"` all reach the next stage untouched, because deciding what they mean is cleaning, and cleaning should be logged. JSON is parsed with numbers as strings, so `23.0` stays `"23.0"` instead of becoming a float. The one thing adapters do interpret is scale: wholesale prices in cents are recorded with `price_scale = 100` rather than divided.

Adapters don't trust their input structurally either. The till CSV header is found by column names rather than position, so a preamble, reordered or extra columns, or a different delimiter don't break it. Encoding is tried as strict UTF-8 first and only then as cp1250. The footer's row count is checked against the rows actually read to catch truncated exports. Webshop and wholesale readers report fields they don't recognise, which is usually the first sign an upstream system changed its schema. Files that no adapter claims are reported, not ignored.

## How parsing works

Parsers live in [`parse/fields.py`](src/clearhouse/parse/fields.py) and know nothing about sources. A source format just picks and configures them: the till's timestamps are `%d.%m.%Y %H:%M` in Ljubljana time with decimal commas, the webshop's are ISO 8601 or epoch milliseconds, the wholesale feed's are bare dates with prices in cents. Adding a source means describing its format, not writing new parsing code.

One rule decides severity. A required field (order id, date, and each line's SKU, quantity and price) that is missing or unparseable is an error, because the order can't be trusted without it. An optional field that fails to parse is dropped with a warning, because the order is still sound. A value that only needed tidying is kept and the repair logged, with the original value alongside it.

Parsing only fixes what can be fixed without guessing. `brk_42` is unambiguously `BRK-0042` and `Å½iga` is unambiguously `Žiga`, but `NOVAK ŽIGA` might or might not be the CRM's Žiga Novak, so that's left for reconciliation, which can check the email.

The tests compare parsed values against the ground truth for every order whose defects parsing alone should recover, on two different seeds. That comparison found a real bug while this stage was being built: `Š` in UTF-8 is the bytes `C5 A0`, and `A0` in Windows-1252 is a non-breaking space, so tidying whitespace before undoing mojibake turned `Å\xa0pela` into an unrecoverable `Å pela`.

## How checks work

[`check.py`](src/clearhouse/check.py) runs two kinds of rule. Validations (future dates, unknown SKUs, quantities below 1) only report, and run on every order so the issue log lists every problem at once. Repairs change the order, and stop as soon as it has an error. An order headed for quarantine isn't worth repairing, and repairing it can do harm: an early version "fixed" the totals of orders with negative quantities, quietly rewriting them from numbers already known to be wrong.

Repairs run in a fixed order because each one makes the next more reliable. Prices are compared with the catalog in euros, using that month's exchange rate, and a price around 100 times the list price is read as cents. Only then are repeated lines judged. A repeat on its own proves nothing, since someone can buy the same part twice on separate lines, so it's only dropped when the order's own header total excludes it. Last, the header total is compared with the sum of the lines, and the lines win because they're the more detailed record.

The tests check that the orders rejected after this stage are exactly the ones the ledger says should be quarantined, with no misses and no false alarms, and that every recoverable order matches the ground truth field for field.

## How reconciliation works

[`reconcile.py`](src/clearhouse/reconcile.py) makes the decisions that need more than one record at a time.

Customers are identified by buyer code or email, never by name, because names are typed by hand. Once a customer is found in the CRM, the order takes the CRM's spelling, which is how `NOVAK ŽIGA` and `Tomaz Krajnc` become `Žiga Novak` and `Tomaž Krajnc`.

When a source sends the same order id twice, the first record wins and an identical later one is marked as its copy. If the two records differ, nobody can say which is right, so the order goes to quarantine.

Click-and-collect orders are recorded twice: once by the webshop when they're placed, and again by the till when the customer picks them up. The till receipt is treated as the copy, because the sale happened online and counting both would double the revenue. Receipts carrying the web order number in `Ref` are matched first. The rest are matched on the same customer, items and total within a seven-day pickup window, but only when exactly one web order fits. Two equally good candidates mean the receipt is kept as its own sale and flagged, rather than guessed at.

The tests check the whole pipeline end to end: the copies it drops are exactly the ledger's duplicates, paired with the right originals, and every true order survives exactly once with the outcome the ledger expects. Every clean order matches the ground truth down to its CRM customer and its euro total. Breaking the matcher on purpose (skipping the no-`Ref` pass, shrinking the pickup window, keeping typed names) makes those tests fail.

## Build log

- [x] Step 1: ground-truth world and messy data generator with defect ledger
- [x] Step 2: ingestion adapters
- [x] Step 3: field parsing (validate and clean each value)
- [x] Step 4: order checks against reference data
- [x] Step 5: reconciliation
- [ ] Step 6: report and scoring
- [ ] Step 7: CI and polish
