"""Compute technical indicators for a set of symbols and write the new day's rows.

Strategy for daily runs: for each symbol that traded today we fetch its FULL
price history (so EMA-200 / MACD seed correctly), compute the indicator series,
then upsert only the rows on/after `since` (normally just today). This is exact
and cheap — work is bounded by the number of symbols that actually traded.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Optional

import pandas as pd
import psycopg2
import psycopg2.extras

from .db import make_conn
from .indicators_math import compute_indicators, nan_to_none

logger = logging.getLogger(__name__)

FETCH_BATCH = 20
UPSERT_BATCH = 50

UPSERT_SQL = """
INSERT INTO public.technical_indicators
    (symbol, as_of_date, series,
     ema_9, ema_12, ema_20, ema_26, ema_50, ema_200,
     rsi_14, atr_14, vwap_20,
     macd_line, macd_signal, macd_histogram)
VALUES %s
ON CONFLICT (symbol, as_of_date, series) DO UPDATE SET
    ema_9 = EXCLUDED.ema_9, ema_12 = EXCLUDED.ema_12, ema_20 = EXCLUDED.ema_20,
    ema_26 = EXCLUDED.ema_26, ema_50 = EXCLUDED.ema_50, ema_200 = EXCLUDED.ema_200,
    rsi_14 = EXCLUDED.rsi_14, atr_14 = EXCLUDED.atr_14, vwap_20 = EXCLUDED.vwap_20,
    macd_line = EXCLUDED.macd_line, macd_signal = EXCLUDED.macd_signal,
    macd_histogram = EXCLUDED.macd_histogram, computed_at = NOW()
"""

_FETCH_SQL = """
    SELECT symbol, as_of_date, series, open, high, low, close, volume
    FROM public.price_history
    WHERE series = %s AND symbol = ANY(%s)
    ORDER BY symbol, as_of_date
"""


def _to_tuples(df: pd.DataFrame) -> list[tuple]:
    out = []
    for _, r in df.iterrows():
        out.append((
            r["symbol"], r["as_of_date"], r["series"],
            nan_to_none(r.get("ema_9")), nan_to_none(r.get("ema_12")),
            nan_to_none(r.get("ema_20")), nan_to_none(r.get("ema_26")),
            nan_to_none(r.get("ema_50")), nan_to_none(r.get("ema_200")),
            nan_to_none(r.get("rsi_14")), nan_to_none(r.get("atr_14")),
            nan_to_none(r.get("vwap_20")), nan_to_none(r.get("macd_line")),
            nan_to_none(r.get("macd_signal")), nan_to_none(r.get("macd_histogram")),
        ))
    return out


def compute_series(conn, series: str, symbols: list[str],
                   since: Optional[date]) -> int:
    """Compute indicators for `symbols` in `series`; write rows on/after `since`.

    If `since` is None, write the full computed history (used by backfill).
    Returns the number of indicator rows upserted.
    """
    if not symbols:
        return 0

    cur = conn.cursor()
    total = 0
    since_str = since.isoformat() if since else None

    for start in range(0, len(symbols), FETCH_BATCH):
        batch = symbols[start:start + FETCH_BATCH]
        cur.execute(_FETCH_SQL, (series, batch))
        rows = cur.fetchall()
        if not rows:
            continue

        df = pd.DataFrame(rows, columns=["symbol", "as_of_date", "series",
                                         "open", "high", "low", "close", "volume"])
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
        df["volume"] = df["volume"].astype(float)

        for symbol, sym_df in df.groupby("symbol", sort=False):
            computed = compute_indicators(sym_df.copy())
            if since_str is not None:
                computed = computed[computed["as_of_date"].astype(str) >= since_str]
            if computed.empty:
                continue
            tuples = _to_tuples(computed)
            for i in range(0, len(tuples), UPSERT_BATCH):
                psycopg2.extras.execute_values(
                    cur, UPSERT_SQL, tuples[i:i + UPSERT_BATCH], page_size=UPSERT_BATCH)
            conn.commit()
            total += len(tuples)

    cur.close()
    logger.info("Indicators series=%s: %d rows upserted for %d symbols",
                series, total, len(symbols))
    return total


def compute_all(conn, symbols_by_series: dict[str, list[str]],
                since: Optional[date]) -> int:
    total = 0
    for series, symbols in symbols_by_series.items():
        total += compute_series(conn, series, symbols, since)
    return total
