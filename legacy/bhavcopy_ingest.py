"""
Ingest NSE CM Bhavcopy CSV files into public.price_history.

Source: NSE_BHAVCOPY/ folder — files named BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv
Each file has ~2700 rows covering all NSE CM segment instruments for that trading day.

Run:
    python -m app.ingestion.bhavcopy_ingest --data-dir ../../NSE_BHAVCOPY
    python -m app.ingestion.bhavcopy_ingest --data-dir ../../NSE_BHAVCOPY --dry-run
    python -m app.ingestion.bhavcopy_ingest --data-dir ../../NSE_BHAVCOPY --series EQ BE
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "6543"))
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

BATCH_SIZE = 2000

# Series to keep. EQ = regular equity, BE = trade-for-trade, SM = SME, IL = institutional.
# Default: EQ + BE cover the vast majority of tradeable stocks.
DEFAULT_SERIES = {"EQ", "BE", "BZ", "SM", "ST"}

UPSERT_SQL = """
INSERT INTO public.price_history
    (symbol, as_of_date, series, open, high, low, close, prev_close, last_price,
     volume, value, num_trades, isin, source)
VALUES %s
ON CONFLICT (symbol, as_of_date, series) DO UPDATE SET
    open       = EXCLUDED.open,
    high       = EXCLUDED.high,
    low        = EXCLUDED.low,
    close      = EXCLUDED.close,
    prev_close = EXCLUDED.prev_close,
    last_price = EXCLUDED.last_price,
    volume     = EXCLUDED.volume,
    value      = EXCLUDED.value,
    num_trades = EXCLUDED.num_trades,
    isin       = EXCLUDED.isin,
    source     = EXCLUDED.source
"""


def clean_float(val: str) -> Optional[float]:
    v = val.strip()
    if v in ("", "-", "N/A", "nan"):
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def clean_int(val: str) -> Optional[int]:
    v = val.strip()
    if v in ("", "-", "N/A", "nan"):
        return None
    try:
        return int(float(v.replace(",", "")))
    except ValueError:
        return None


def parse_date(val: str) -> Optional[date]:
    v = val.strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def parse_csv(path: Path, allowed_series: set[str]) -> list[tuple]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            series = row.get("SctySrs", "").strip()
            if series not in allowed_series:
                continue

            trade_date = parse_date(row.get("TradDt", "") or row.get("BizDt", ""))
            if trade_date is None:
                continue

            symbol = row.get("TckrSymb", "").strip()
            if not symbol:
                continue

            rows.append((
                symbol,
                trade_date,
                series,
                clean_float(row.get("OpnPric", "")),
                clean_float(row.get("HghPric", "")),
                clean_float(row.get("LwPric", "")),
                clean_float(row.get("LastPric", "")),
                clean_float(row.get("PrvsClsgPric", "")),
                clean_float(row.get("LastPric", "")),
                clean_int(row.get("TtlTradgVol", "")),
                clean_float(row.get("TtlTrfVal", "")),
                clean_int(row.get("TtlNbOfTxsExctd", "")),
                row.get("ISIN", "").strip() or None,
                "nse_bhavcopy",
            ))
    return rows


def ingest(data_dir: Path, dry_run: bool, allowed_series: set[str], password: str) -> None:
    csv_files = sorted(data_dir.glob("BhavCopy_NSE_CM_0_0_0_*_F_0000.csv"))
    if not csv_files:
        logger.error("No bhavcopy CSV files found in %s", data_dir)
        sys.exit(1)

    logger.info("Found %d CSV files to process", len(csv_files))
    logger.info("Filtering series: %s", sorted(allowed_series))

    if dry_run:
        logger.info("DRY RUN — parsing first 3 files, no DB writes")
        for f in csv_files[:3]:
            rows = parse_csv(f, allowed_series)
            logger.info("  %s → %d rows", f.name, len(rows))
            if rows:
                logger.info("  Sample: %s", rows[0])
        return

    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=password, sslmode="require"
    )
    conn.autocommit = False
    cur = conn.cursor()

    total_inserted = 0
    total_files = len(csv_files)

    for i, csv_path in enumerate(csv_files, 1):
        rows = parse_csv(csv_path, allowed_series)
        if not rows:
            logger.warning("[%d/%d] %s — 0 rows after filter, skipping", i, total_files, csv_path.name)
            continue

        # Batch upsert
        for batch_start in range(0, len(rows), BATCH_SIZE):
            batch = rows[batch_start: batch_start + BATCH_SIZE]
            psycopg2.extras.execute_values(cur, UPSERT_SQL, batch, page_size=BATCH_SIZE)

        conn.commit()
        total_inserted += len(rows)

        if i % 50 == 0 or i == total_files:
            logger.info("[%d/%d] Cumulative rows upserted: %d", i, total_files, total_inserted)

    cur.close()
    conn.close()
    logger.info("Done. Total rows upserted: %d across %d files", total_inserted, total_files)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NSE Bhavcopy CSVs into price_history")
    parser.add_argument("--data-dir", required=True, help="Path to NSE_BHAVCOPY folder")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument(
        "--series", nargs="+", default=sorted(DEFAULT_SERIES),
        help="Series codes to include (default: EQ BE BZ SM ST)"
    )
    parser.add_argument("--password", default=DB_PASSWORD, help="DB password (or set DB_PASSWORD env)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    if not data_dir.is_dir():
        logger.error("data-dir not found: %s", data_dir)
        sys.exit(1)

    if not args.dry_run and not args.password:
        logger.error("DB_PASSWORD env var or --password required")
        sys.exit(1)

    ingest(data_dir, args.dry_run, set(args.series), args.password)


if __name__ == "__main__":
    main()
