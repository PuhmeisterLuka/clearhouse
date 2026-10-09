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
3. **Check.** Rules that need the whole order or the reference data: known SKUs, plausible quantities, dates inside the export period, header totals against line sums, 100x price errors against the catalog, a currency inferred from the customer's country when it's missing.
4. **Reconcile.** Dedupe exact repeats and webhook retries, match till pickups to web orders (by reference, then fuzzily by customer, items and timing), resolve customers against the CRM, convert to EUR.
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

Runs the pipeline over `data/raw`, prints what was found per source and a count of every issue by stage and code, and writes `out/parsed_orders.jsonl` and `out/issues.csv`.

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

## Build log

- [x] Step 1: ground-truth world and messy data generator with defect ledger
- [x] Step 2: ingestion adapters
- [x] Step 3: field parsing (validate and clean each value)
- [ ] Step 4: order checks against reference data
- [ ] Step 5: reconciliation
- [ ] Step 6: report and scoring
- [ ] Step 7: CI and polish
