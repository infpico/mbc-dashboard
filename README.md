# NordPack Procurement Analytics

Portfolio project: procurement & supply chain analytics for a **fictional** packaging plant
(NordPack Manufacturing s.r.o.). All data is **synthetic** – no real company data.

**Work in progress** – the Power BI dashboard is being built; full README with screenshots coming soon.

## What's inside
| Folder | Content |
|---|---|
| `generator/` | Python generator of a reproducible SAP-style dataset (fixed seed) + SQL pattern checks |
| `data/` | Generated tables as CSV (for Power BI) and SQLite (`nordpack.db`) |
| `docs/` | Data dictionary – tables, columns and their SAP equivalents, known data quality issues |

## Data model (SAP-style)
Vendors (LFA1), materials (MARA), standard prices (MBEW), purchase orders (EKKO/EKPO),
goods receipts and consumption (MATDOC 101/261), invoices (RBKP/RSEG), stock snapshots (MARD),
FX rates (TCURR) – 25 months, ~4,100 PO items, planted business patterns and data quality issues.

## KPIs
Spend (direct/indirect, Pareto) · PPV with price vs. FX effect · IPV · OTIF · stock value,
coverage, turnover, slow movers · batch size vs. EOQ · data quality.

## Run
```bash
pip install pandas numpy pyyaml
python generator/generate_data.py
python generator/check_patterns.py
```

Built with the help of an AI assistant (Claude) as a pair programmer; business logic,
data model decisions and validation of all numbers against SQL are my own.
