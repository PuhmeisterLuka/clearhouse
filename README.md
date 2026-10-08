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

1. **Ingest.** One adapter per format, each producing the same raw record shape with lineage (file, row or element, raw value). Encoding detection, preamble skipping, nested-JSON flattening.
2. **Validate.** Declarative rules per field and per order: required fields, parseable dates, plausible ranges, known SKUs, known customers. Every failure becomes an issue row, never a silent drop.
3. **Clean.** Normalise what can be safely repaired: SKU formats, emails, mojibake, number formats, timezones, cents vs euros, text quantities.
4. **Reconcile.** Dedupe exact repeats and webhook retries, match till pickups to web orders (by reference, then fuzzily by customer, items and timing), resolve customers against the CRM, check header totals against line sums, flag 100x price errors against the catalog, convert to EUR.
5. **Report.** Clean tables (orders, lines, customers), a quarantine file with reasons, an issue log, and an HTML data-quality report.
6. **Score.** Compare the pipeline's output with the ground truth and ledger: defect detection rates per kind, false quarantines, and value accuracy.

## Running it

```bash
uv run clearhouse generate
```

This writes `data/raw`, `data/reference` and `data/truth`. The pipeline must never read `data/truth`; only the scorer does.

```bash
uv run clearhouse ingest --dump out/raw_orders.jsonl
```

Reads every raw file into one common record shape and prints what was found per source, plus any structural issues.

## Development

Tests and lint (ruff covers unused imports and variables, so dead code fails the check):

```bash
uv run pytest && uv run ruff check .
```

## How ingestion works

Each adapter knows one format and nothing else. It maps that format's fields onto a shared `RawOrder` / `RawLine` shape and tags every record with where it came from (`line 214`, `orders[37].items[1]`, `Order[12]/Line[3]`), so any problem found later can be traced back to the exact spot in the source file.

Values are kept exactly as written. `"10,50"`, `" BRK-0042 "` and `"2 pcs"` all reach the next stage untouched, because deciding what they mean is cleaning, and cleaning should be logged. JSON is parsed with numbers as strings, so `23.0` stays `"23.0"` instead of becoming a float. The one thing adapters do interpret is scale: wholesale prices in cents are recorded with `price_scale = 100` rather than divided.

Adapters don't trust their input structurally either. The till CSV header is found by column names rather than position, so a preamble, reordered or extra columns, or a different delimiter don't break it. Encoding is tried as strict UTF-8 first and only then as cp1250. The footer's row count is checked against the rows actually read to catch truncated exports. Webshop and wholesale readers report fields they don't recognise, which is usually the first sign an upstream system changed its schema. Files that no adapter claims are reported, not ignored.

## Build log

- [x] Step 1: ground-truth world and messy data generator with defect ledger
- [x] Step 2: ingestion adapters
- [ ] Step 3: validation rules and issue log
- [ ] Step 4: cleaning
- [ ] Step 5: reconciliation
- [ ] Step 6: report and scoring
- [ ] Step 7: tests, CI, polish
