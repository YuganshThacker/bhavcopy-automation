"""
Ingest legacy NSE CM Bhavcopy zip files (2015-2024) into public.price_history.

Source: 'BhavCopy 1 Jan 2015 to 28 March 2024/' folder
Files named: cm01APR2015bhav.csv.zip  (each zip contains one CSV)
Old column format: SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, LAST, PREVCLOSE,
                   TOTTRDQTY, TOTTRDVAL, TIMESTAMP, TOTALTRADES, ISIN

Run:
    cd KuberAI-backend
    python -m app.ingestion.bhavcopy_ingest_legacy \
        --data-dir "../../NSE_bhavcopy/BhavCopy 1 Jan 2015 to 28 March 2024"
    python -m app.ingestion.bhavcopy_ingest_legacy \
        --data-dir "../../NSE_bhavcopy/BhavCopy 1 Jan 2015 to 28 March 2024" --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
import zipfile
from datetime import date, datetime
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


def parse_legacy_date(val: str) -> Optional[date]:
    """Parse dates like '01-APR-2015' or '28-MAR-2024'."""
    v = val.strip()
    if not v:
        return None
    try:
        return datetime.strptime(v, "%d-%b-%Y").date()
    except ValueError:
        return None


def date_from_filename(path: Path) -> Optional[date]:
    """Extract date from filename like cm13JUL2020bhav.csv.zip → 2020-07-13."""
    name = path.stem  # e.g. cm13JUL2020bhav.csv
    name = name.replace("bhav.csv", "").lstrip("cm")  # e.g. 13JUL2020
    try:
        return datetime.strptime(name, "%d%b%Y").date()
    except ValueError:
        return None


def parse_zip(path: Path, allowed_series: set[str]) -> list[tuple]:
    rows = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                logger.warning("No CSV found inside %s", path.name)
                return rows
            with zf.open(csv_names[0]) as raw:
                content = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace")
                reader = csv.DictReader(content)
                for row in reader:
                    series = row.get("SERIES", "").strip()
                    if series not in allowed_series:
                        continue

                    trade_date = parse_legacy_date(row.get("TIMESTAMP", ""))
                    if trade_date is None:
                        continue

                    symbol = row.get("SYMBOL", "").strip()
                    if not symbol:
                        continue

                    rows.append((
                        symbol,
                        trade_date,
                        series,
                        clean_float(row.get("OPEN", "")),
                        clean_float(row.get("HIGH", "")),
                        clean_float(row.get("LOW", "")),
                        clean_float(row.get("CLOSE", "")),
                        clean_float(row.get("PREVCLOSE", "")),
                        clean_float(row.get("LAST", "")),
                        clean_int(row.get("TOTTRDQTY", "")),
                        clean_float(row.get("TOTTRDVAL", "")),
                        clean_int(row.get("TOTALTRADES", "")),
                        row.get("ISIN", "").strip() or None,
                        "nse_bhavcopy_legacy",
                    ))
    except zipfile.BadZipFile:
        logger.warning("Skipping corrupt zip: %s", path.name)
    except Exception as e:
        logger.warning("Failed to parse %s: %s", path.name, e)
    return rows


def ingest(data_dir: Path, dry_run: bool, allowed_series: set[str], password: str) -> None:
    zip_files = sorted(data_dir.glob("cm*.csv.zip"))
    if not zip_files:
        logger.error("No legacy bhavcopy zip files found in %s", data_dir)
        sys.exit(1)

    logger.info("Found %d zip files to process", len(zip_files))
    logger.info("Filtering series: %s", sorted(allowed_series))

    if dry_run:
        logger.info("DRY RUN — parsing first 3 files, no DB writes")
        for f in zip_files[:3]:
            rows = parse_zip(f, allowed_series)
            logger.info("  %s → %d rows", f.name, len(rows))
            if rows:
                logger.info("  Sample: %s", rows[0])
        return

    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=password, sslmode="require",
        connect_timeout=30,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
        options="-c statement_timeout=120000"  # 2 min max per statement
    )
    conn.autocommit = False
    cur = conn.cursor()

    # Fetch already-ingested dates so we can skip them
    cur.execute("SELECT DISTINCT as_of_date FROM public.price_history WHERE source = 'nse_bhavcopy_legacy'")
    already_done = {r[0] for r in cur.fetchall()}
    logger.info("Skipping %d already-ingested trading days", len(already_done))

    total_inserted = 0
    total_files = len(zip_files)
    skipped = 0

    for i, zip_path in enumerate(zip_files, 1):
        # Fast skip: check date from filename before opening zip
        file_date = date_from_filename(zip_path)
        if file_date and file_date in already_done:
            skipped += 1
            continue

        rows = parse_zip(zip_path, allowed_series)
        if not rows:
            logger.warning("[%d/%d] %s — 0 rows after filter, skipping", i, total_files, zip_path.name)
            continue

        for batch_start in range(0, len(rows), BATCH_SIZE):
            batch = rows[batch_start: batch_start + BATCH_SIZE]
            psycopg2.extras.execute_values(cur, UPSERT_SQL, batch, page_size=BATCH_SIZE)

        conn.commit()
        total_inserted += len(rows)

        if i % 100 == 0 or i == total_files:
            logger.info("[%d/%d] Cumulative rows upserted: %d (skipped %d already-done)", i, total_files, total_inserted, skipped)

    cur.close()
    conn.close()
    logger.info("Done. Rows upserted: %d | Files skipped (already done): %d", total_inserted, skipped)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest legacy NSE Bhavcopy zips into price_history")
    parser.add_argument("--data-dir", required=True, help="Path to legacy bhavcopy folder")
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
        logger.error("DB_PASSWORD env or --password required")
        sys.exit(1)

    ingest(data_dir, args.dry_run, set(args.series), args.password)


if __name__ == "__main__":
    main()
