# Bhavcopy Automation

Automated daily pipeline that **downloads** NSE + BSE end-of-day bhavcopy,
**ingests** it into Supabase (`price_history`), and **computes technical
indicators** (`technical_indicators`) — every trading day, plus a one-shot
historical backfill.

## How it works

```
run_daily.py  (Render cron, 20:15 IST Mon–Fri)
      │
      ▼
download ─ NSE UDiFF .zip  +  BSE UDiFF .csv      (skip weekends; missing file = holiday)
      │
      ▼
ingest  ─ parse UDiFF → upsert price_history
          • NSE: native series EQ, BE, BZ, SM, ST
          • BSE: traded equity, 1 line/symbol, series='BSE'
      │
      ▼
indicators ─ for each symbol that traded today, seed EMA/RSI/ATR/VWAP/MACD from
             full history, write only today's row  (exact + cheap)
      │
      ▼
   record run in public.bhavcopy_runs
```

Everything is **idempotent** (upsert on `(symbol, as_of_date, series)`), so any
date can be safely re-run.

## Project layout

| File | Purpose |
|------|---------|
| `run_daily.py` | Daily entrypoint (cron target) |
| `backfill.py` | Backfill a date range |
| `bhavcopy_pipeline/download.py` | NSE + BSE download |
| `bhavcopy_pipeline/ingest.py` | UDiFF parse → `price_history` upsert |
| `bhavcopy_pipeline/indicators.py` | indicator compute → `technical_indicators` |
| `bhavcopy_pipeline/indicators_math.py` | pure EMA/RSI/ATR/VWAP/MACD math |
| `bhavcopy_pipeline/pipeline.py` | orchestrates one date |
| `bhavcopy_pipeline/db.py` | connection + `bhavcopy_runs` log |
| `render.yaml` | Render Blueprint (cron job) |
| `Dockerfile` | portable image (Railway/any host) |

## Local usage

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in DB_PASSWORD
set -a; source .env; set +a

# one day (defaults to today, IST)
python run_daily.py
python run_daily.py --date 2026-06-05

# backfill a range
python backfill.py --start 2026-05-01 --end 2026-06-05
# long ranges: ingest all days, compute indicators once at the end
python backfill.py --start 2015-01-01 --end 2026-06-05 --compute-at-end
```

## Deploy to Render (cron job)

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → connect the repo. It reads `render.yaml`
   and creates the `bhavcopy-daily` cron job (schedule `45 14 * * 1-5` UTC =
   20:15 IST, Mon–Fri).
3. In the service's **Environment**, set `DB_PASSWORD` (the only secret; the
   rest come from `render.yaml`).
4. Use **Trigger Run** once to verify, then it runs automatically each weekday.

To change the time, edit `schedule` in `render.yaml` (UTC) and redeploy.

## Database

Target tables (Supabase project `xqutgxdwmsvabwaioszq`, "KuberAI - UAT"):

- `price_history` — PK `(symbol, as_of_date, series)`
- `technical_indicators` — PK `(symbol, as_of_date, series)`
- `bhavcopy_runs` — created automatically; one row per run with status + counts

Check recent runs:

```sql
SELECT trade_date, status, nse_price_rows, bse_price_rows, indicator_rows,
       finished_at - started_at AS duration
FROM public.bhavcopy_runs ORDER BY id DESC LIMIT 10;
```
