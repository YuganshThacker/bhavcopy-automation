"""Parse a UDiFF bhavcopy CSV and upsert rows into public.price_history.

NSE and BSE share the identical UDiFF column schema, so one parser serves both.
The only differences:
  * NSE  — keep native security series (EQ, BE, BZ, SM, ST).
  * BSE  — keep traded equity (FinInstrmTp=STK, volume>0), one line per symbol,
           stored under the single series tag 'BSE' to match existing data.

`close` is mapped from LastPric to stay consistent with the historical rows
already in price_history (so EMA/MACD continuity is preserved).
"""
from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import psycopg2.extras

from . import config

logger = logging.getLogger(__name__)

BATCH_SIZE = 2000

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


def _f(val: str) -> Optional[float]:
    v = (val or "").strip()
    if v in ("", "-", "N/A", "nan"):
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def _i(val: str) -> Optional[int]:
    v = (val or "").strip()
    if v in ("", "-", "N/A", "nan"):
        return None
    try:
        return int(float(v.replace(",", "")))
    except ValueError:
        return None


def _row_tuple(row: dict, series: str, source: str) -> tuple:
    return (
        row.get("TckrSymb", "").strip(),
        date.fromisoformat(row["TradDt"].strip()),
        series,
        _f(row.get("OpnPric", "")),
        _f(row.get("HghPric", "")),
        _f(row.get("LwPric", "")),
        _f(row.get("LastPric", "")),       # close (matches existing history)
        _f(row.get("PrvsClsgPric", "")),
        _f(row.get("LastPric", "")),       # last_price
        _i(row.get("TtlTradgVol", "")),
        _f(row.get("TtlTrfVal", "")),
        _i(row.get("TtlNbOfTxsExctd", "")),
        row.get("ISIN", "").strip() or None,
        source,
    )


def parse_nse(csv_path: Path) -> list[tuple]:
    rows: list[tuple] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            series = row.get("SctySrs", "").strip()
            if series not in config.NSE_SERIES:
                continue
            if not row.get("TckrSymb", "").strip() or not row.get("TradDt", "").strip():
                continue
            rows.append(_row_tuple(row, series, "nse_bhavcopy"))
    return rows


def parse_bse(csv_path: Path) -> list[tuple]:
    """Keep traded equity, dedupe to the highest-volume line per symbol, tag 'BSE'."""
    best: dict[str, tuple] = {}      # symbol -> row tuple
    best_vol: dict[str, int] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("FinInstrmTp", "").strip() != "STK":
                continue
            symbol = row.get("TckrSymb", "").strip()
            if not symbol or not row.get("TradDt", "").strip():
                continue
            vol = _i(row.get("TtlTradgVol", "")) or 0
            if vol <= 0:
                continue
            if symbol not in best or vol > best_vol[symbol]:
                best[symbol] = _row_tuple(row, config.BSE_SERIES_TAG, "bse_bhavcopy")
                best_vol[symbol] = vol
    return list(best.values())


def upsert(conn, rows: list[tuple]) -> int:
    cur = conn.cursor()
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        psycopg2.extras.execute_values(cur, UPSERT_SQL, batch, page_size=BATCH_SIZE)
        conn.commit()
    cur.close()
    return len(rows)


def symbols_by_series(rows: list[tuple]) -> dict[str, list[str]]:
    """Group symbols by their series tag (index 2 = series, index 0 = symbol)."""
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r[2], set()).add(r[0])
    return {s: sorted(syms) for s, syms in out.items()}
