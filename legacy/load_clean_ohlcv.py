"""
Load clean OHLCV CSVs (Symbol,Date,Open,High,Low,Close,Volume) into public.price_history.

Unlike bhavcopy_ingest.py (which parses the raw NSE bhavcopy column layout), this loader
handles the simplified monthly export format used by nse_1month.csv / bse_1month.csv.

Mapping:
  Symbol -> symbol      Date -> as_of_date     Open/High/Low/Close -> open/high/low/close
  Close  -> last_price  Volume -> volume       series/source supplied via flags
  prev_close, value, num_trades, isin, market_cap -> NULL (not present in source)

Connection: pass the full pooler URI via --dsn or the DB_DSN env var.

Run:
    DB_DSN="postgresql://..." python scripts/load_clean_ohlcv.py \
        --csv NSE_BHAVCOPY/nse_1month.csv --series EQ  --source nse_1month
    DB_DSN="postgresql://..." python scripts/load_clean_ohlcv.py \
        --csv NSE_BHAVCOPY/bse_1month.csv --series BSE --source bse_1month
    ... --dry-run   (parse + report, no DB writes)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 2000

UPSERT_SQL = """
INSERT INTO public.price_history
    (symbol, as_of_date, series, open, high, low, close, last_price, volume, source)
VALUES %s
ON CONFLICT (symbol, as_of_date, series) DO UPDATE SET
    open       = EXCLUDED.open,
    high       = EXCLUDED.high,
    low        = EXCLUDED.low,
    close      = EXCLUDED.close,
    last_price = EXCLUDED.last_price,
    volume     = EXCLUDED.volume,
    source     = EXCLUDED.source
"""


def load_rows(csv_path: Path, series: str, source: str) -> list[tuple]:
    df = pd.read_csv(csv_path)
    expected = {"Symbol", "Date", "Open", "High", "Low", "Close", "Volume"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"{csv_path.name}: missing columns {missing}")

    df = df.dropna(subset=["Symbol", "Date", "Close"])
    df["Symbol"] = df["Symbol"].astype(str).str.strip()
    df = df[df["Symbol"] != ""]
    # Date is ISO (YYYY-MM-DD) in the source; keep as string, Postgres casts to date.
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    # Volume -> int (bigint column); coerce floats like 40674.0
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0).astype("int64")

    rows = []
    for r in df.itertuples(index=False):
        rows.append((
            r.Symbol, r.Date, series,
            float(r.Open), float(r.High), float(r.Low), float(r.Close),
            float(r.Close),          # last_price = close
            int(r.Volume),
            source,
        ))
    return rows


def ingest(csv_path: Path, series: str, source: str, dsn: str, dry_run: bool) -> None:
    rows = load_rows(csv_path, series, source)
    logger.info("%s -> %d rows (series=%s, source=%s)", csv_path.name, len(rows), series, source)
    if rows:
        logger.info("Sample: %s", rows[0])

    if dry_run:
        logger.info("DRY RUN — no DB writes")
        return

    conn = psycopg2.connect(dsn, sslmode="require", connect_timeout=30)
    conn.autocommit = False
    cur = conn.cursor()
    total = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        psycopg2.extras.execute_values(cur, UPSERT_SQL, batch, page_size=BATCH_SIZE)
        conn.commit()
        total += len(batch)
        if (start // BATCH_SIZE) % 5 == 0 or total == len(rows):
            logger.info("  upserted %d/%d", total, len(rows))
    cur.close()
    conn.close()
    logger.info("Done. %d rows upserted into price_history (series=%s)", total, series)


def main() -> None:
    p = argparse.ArgumentParser(description="Load clean OHLCV CSV into price_history")
    p.add_argument("--csv", required=True, help="Path to clean OHLCV CSV")
    p.add_argument("--series", required=True, help="Series tag to store (e.g. EQ, BSE)")
    p.add_argument("--source", required=True, help="source column value (e.g. nse_1month)")
    p.add_argument("--dsn", default=os.environ.get("DB_DSN", ""), help="Postgres URI (or DB_DSN env)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    csv_path = Path(args.csv).expanduser().resolve()
    if not csv_path.is_file():
        logger.error("CSV not found: %s", csv_path)
        sys.exit(1)
    if not args.dry_run and not args.dsn:
        logger.error("--dsn or DB_DSN env required")
        sys.exit(1)

    ingest(csv_path, args.series, args.source, args.dsn, args.dry_run)


if __name__ == "__main__":
    main()
