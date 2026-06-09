"""End-to-end pipeline for a single trading date: download -> ingest -> indicators."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import NamedTuple

from . import config, download, ingest, indicators

logger = logging.getLogger(__name__)


class DateResult(NamedTuple):
    trade_date: date
    nse_rows: int
    bse_rows: int
    indicator_rows: int
    had_data: bool


def run_for_date(conn, d: date, *, compute: bool = True) -> DateResult:
    """Run the full pipeline for date d using an open DB connection.

    Returns counts. `had_data` is False when neither exchange published a file
    (holiday / not yet available) — the caller treats that as a no-op, not error.
    """
    data_dir = Path(config.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. Download
    nse_csv = download.download_nse(d, data_dir)
    bse_csv = download.download_bse(d, data_dir)

    if not nse_csv and not bse_csv:
        logger.info("No bhavcopy published for %s (holiday/weekend or not yet available)", d)
        return DateResult(d, 0, 0, 0, had_data=False)

    # 2. Parse + ingest price rows
    symbols_by_series: dict[str, list[str]] = {}
    nse_rows = bse_rows = 0

    if nse_csv:
        rows = ingest.parse_nse(nse_csv)
        nse_rows = ingest.upsert(conn, rows)
        for s, syms in ingest.symbols_by_series(rows).items():
            symbols_by_series.setdefault(s, []).extend(syms)
        logger.info("NSE %s: %d price rows across series %s",
                    d, nse_rows, sorted(ingest.symbols_by_series(rows).keys()))

    if bse_csv:
        rows = ingest.parse_bse(bse_csv)
        bse_rows = ingest.upsert(conn, rows)
        for s, syms in ingest.symbols_by_series(rows).items():
            symbols_by_series.setdefault(s, []).extend(syms)
        logger.info("BSE %s: %d price rows", d, bse_rows)

    # 3. Compute indicators for symbols that traded today, writing only today's rows
    indicator_rows = 0
    if compute and symbols_by_series:
        indicator_rows = indicators.compute_all(conn, symbols_by_series, since=d)

    return DateResult(d, nse_rows, bse_rows, indicator_rows, had_data=True)
