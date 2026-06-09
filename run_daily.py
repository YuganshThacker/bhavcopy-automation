#!/usr/bin/env python3
"""Daily entrypoint — run by the Render cron job after market close.

Determines the target trading date (today in IST by default, or --date), runs
download -> ingest -> indicators, and records the run in public.bhavcopy_runs.

Exit codes:
    0  success, or no data published (holiday/weekend)
    1  an error occurred (Render marks the run failed)

Usage:
    python run_daily.py                 # today (IST)
    python run_daily.py --date 2026-06-05
    python run_daily.py --no-compute    # ingest prices only, skip indicators
"""
from __future__ import annotations

import argparse
import logging
import sys

from bhavcopy_pipeline import calendar_utils, config
from bhavcopy_pipeline.db import ensure_run_log, finish_run, make_conn, start_run
from bhavcopy_pipeline.pipeline import run_for_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_daily")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily bhavcopy ingest + indicators")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: today IST)")
    parser.add_argument("--no-compute", action="store_true", help="Skip indicator computation")
    args = parser.parse_args()

    config.require_db_password()

    if args.date:
        target = calendar_utils.parse_date(args.date)
    else:
        target = calendar_utils.most_recent_weekday(calendar_utils.today_ist())

    logger.info("=== Bhavcopy daily run for %s ===", target)

    conn = make_conn()
    ensure_run_log(conn)
    run_id = start_run(conn, target)

    try:
        result = run_for_date(conn, target, compute=not args.no_compute)
    except Exception as e:  # noqa: BLE001 — top-level guard so we always log status
        logger.exception("Run failed for %s", target)
        try:
            finish_run(conn, run_id, "error", error=str(e))
        finally:
            conn.close()
        return 1

    status = "success" if result.had_data else "no_data"
    finish_run(conn, run_id, status,
               nse_rows=result.nse_rows, bse_rows=result.bse_rows,
               indicator_rows=result.indicator_rows)
    conn.close()

    logger.info("=== Done %s: status=%s nse=%d bse=%d indicators=%d ===",
                target, status, result.nse_rows, result.bse_rows, result.indicator_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
