# Bhavcopy Automation

An automated daily market-data pipeline that downloads NSE and BSE end-of-day bhavcopy files, ingests them into PostgreSQL, and computes technical indicators.

## Why I Built It

Financial AI systems are only as reliable as the data underneath them. This project focuses on **repeatable ingestion, normalization, idempotency, and derived-feature generation**.

## Pipeline

```
Scheduled Job
     ↓
NSE + BSE Downloads
     ↓
UDiFF Parsing / Normalization
     ↓
PostgreSQL Upsert
     ↓
Technical Indicators
     ↓
Run Audit Record
```

For each trading date, the pipeline:

1. downloads the latest exchange files
2. parses and normalizes the data
3. upserts price history
4. computes EMA, RSI, ATR, VWAP, and MACD
5. records run status and row counts

## Reliability

The pipeline is designed to be **idempotent**. Re-running a date uses the key:

```
(symbol, as_of_date, series)
```

This makes historical backfills and retries safer.

The system also treats weekends and exchange holidays as expected conditions instead of pipeline failures when source files are unavailable.

## Project Structure

| File | Purpose |
|---|---|
| `run_daily.py` | Daily entry point |
| `backfill.py` | Historical backfill |
| `bhavcopy_pipeline/download.py` | NSE/BSE download layer |
| `bhavcopy_pipeline/ingest.py` | Parse and database upsert |
| `bhavcopy_pipeline/indicators.py` | Indicator orchestration |
| `bhavcopy_pipeline/indicators_math.py` | Pure indicator calculations |
| `bhavcopy_pipeline/pipeline.py` | Pipeline orchestration |
| `bhavcopy_pipeline/db.py` | Database connection and run logging |
| `render.yaml` | Scheduled deployment definition |
| `Dockerfile` | Container image |

## Indicators

The current daily calculation includes:

- EMA
- RSI
- ATR
- VWAP
- MACD

Calculations are seeded from historical data and only the required daily rows are written, keeping the scheduled job lightweight.

## Data Layer

The pipeline targets PostgreSQL-compatible infrastructure. Database credentials are supplied through environment variables and are intentionally not stored in the repository.

Example:

```env
DB_HOST=your-database-host
DB_PORT=5432
DB_NAME=your-database
DB_USER=your-user
DB_PASSWORD=your-password
```

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# configure database credentials

python run_daily.py
```

Historical backfill:

```bash
python backfill.py --start 2026-05-01 --end 2026-06-05
```

## Deployment

The repository includes a container definition and a scheduled-job configuration for running the pipeline on a recurring basis.

## Engineering Takeaways

This project is primarily about production-style data engineering:

**scheduled execution → deterministic transformations → idempotent persistence → observable runs**

That pattern is reusable for market data, ML feature generation, RAG ingestion, and other AI data pipelines.
