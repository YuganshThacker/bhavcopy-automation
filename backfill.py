#!/usr/bin/env python3
"""Historical backfill — run the pipeline over a date range.

Walks every weekday from --start to --end, downloading and ingesting each day's
bhavcopy and computing indicators. Holidays (missing files) are skipped cleanly.
Everything is idempotent (upsert), so re-running a range is safe.

Usage:
    python backfill.py --start 2026-05-01 --end 2026-06-05
    python backfill.py --start 2026-05-01 --end 2026-06-05 --no-compute
    # ingest a range first, then compute indicators once at the end:
    python backfill.py --start 2026-05-01 --end 2026-06-05 --compute-at-end
"""
from __future__ import annotations

import argparse
import logging
import sys

from bhavcopy_pipeline import calendar_utils, config, indicators
from bhavcopy_pipeline.db import ensure_run_log, finish_run, make_conn, start_run
from bhavcopy_pipeline.pipeline import run_for_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill bhavcopy over a date range")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--no-compute", action="store_true",
                        help="Ingest prices only, never compute indicators")
    parser.add_argument("--compute-at-end", action="store_true",
                        help="Ingest all dates first, then compute indicators once for all "
                             "touched symbols (faster for long ranges)")
    args = parser.parse_args()

    config.require_db_password()
    start = calendar_utils.parse_date(args.start)
    end = calendar_utils.parse_date(args.end)
    if start > end:
        logger.error("--start must be <= --end")
        return 1

    per_day_compute = not args.no_compute and not args.compute_at_end

    conn = make_conn()
    ensure_run_log(conn)

    days = list(calendar_utils.weekdays_in_range(start, end))
    logger.info("Backfilling %d weekdays from %s to %s (compute=%s)",
                len(days), start, end, "per-day" if per_day_compute
                else ("at-end" if args.compute_at_end else "off"))

    touched: dict[str, set[str]] = {}
    tot_nse = tot_bse = tot_ind = 0

    for d in days:
        run_id = start_run(conn, d)
        try:
            res = run_for_date(conn, d, compute=per_day_compute)
        except Exception as e:  # noqa: BLE001
            logger.exception("Backfill failed on %s", d)
            finish_run(conn, run_id, "error", error=str(e))
            conn.close()
            return 1

        tot_nse += res.nse_rows
        tot_bse += res.bse_rows
        tot_ind += res.indicator_rows
        status = "success" if res.had_data else "no_data"
        finish_run(conn, run_id, status, nse_rows=res.nse_rows,
                   bse_rows=res.bse_rows, indicator_rows=res.indicator_rows)

    if args.compute_at_end:
        # Compute indicators once for every symbol that appears on/after start,
        # seeding from full history and writing rows on/after start.
        logger.info("compute-at-end: computing indicators for all symbols touched since %s", start)
        cur = conn.cursor()
        for series in list(config.NSE_SERIES) + [config.BSE_SERIES_TAG]:
            cur.execute(
                "SELECT DISTINCT symbol FROM public.price_history "
                "WHERE series = %s AND as_of_date >= %s ORDER BY symbol",
                (series, start),
            )
            syms = [r[0] for r in cur.fetchall()]
            if syms:
                tot_ind += indicators.compute_series(conn, series, syms, since=start)
        cur.close()

    conn.close()
    logger.info("=== Backfill done: nse=%d bse=%d indicators=%d over %d days ===",
                tot_nse, tot_bse, tot_ind, len(days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
