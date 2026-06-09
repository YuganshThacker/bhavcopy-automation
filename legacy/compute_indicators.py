"""
Compute technical indicators from price_history and store in technical_indicators.

Indicators (per the CORE INPUTS spec):
  1. EMA   — periods 9, 12, 20, 26, 50, 200  (alpha = 2/(n+1), seed = SMA of first n closes)
  2. RSI   — 14-period  (gains/losses seeded with SMA, then exact formula)
  3. ATR   — 14-period SMA of True Range
  4. VWAP  — rolling 20-day  (TP = (H+L+C)/3, VWAP = sum(TP*V)/sum(V))
  5. MACD  — EMA12 - EMA26  |  Signal = EMA9(MACD)  |  Histogram = MACD - Signal

Run:
    python -m app.ingestion.compute_indicators
    python -m app.ingestion.compute_indicators --series EQ
    python -m app.ingestion.compute_indicators --symbols RELIANCE INFY TCS
    python -m app.ingestion.compute_indicators --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "6543"))
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

FETCH_BATCH = 20          # symbols fetched per DB round-trip
UPSERT_BATCH = 50         # rows per executemany call (small to avoid pooler statement timeout)

EMA_PERIODS = [9, 12, 20, 26, 50, 200]
RSI_PERIOD   = 14
ATR_PERIOD   = 14
VWAP_WINDOW  = 20


# ─── Indicator maths ──────────────────────────────────────────────────────────

def _ema_series(close: np.ndarray, n: int) -> np.ndarray:
    """EMA with SMA seed. EMA_t = C_t * α + EMA_{t-1} * (1-α), α = 2/(n+1).
    Handles arrays with a leading NaN prefix (e.g. MACD values)."""
    result = np.full(len(close), np.nan)
    # Find first non-NaN index so we can seed correctly even if input has NaN prefix
    first_valid = int(np.argmax(~np.isnan(close)))
    if np.isnan(close[first_valid]):
        return result                          # all NaN
    available = len(close) - first_valid
    if available < n:
        return result
    alpha = 2.0 / (n + 1)
    seed_end = first_valid + n
    result[seed_end - 1] = close[first_valid:seed_end].mean()   # SMA seed
    for i in range(seed_end, len(close)):
        if np.isnan(close[i]):
            continue
        result[i] = close[i] * alpha + result[i - 1] * (1 - alpha)
    return result


def _rsi_series(close: np.ndarray, n: int = 14) -> np.ndarray:
    """RSI with SMA-seeded avg gain/loss, then rolling from that seed."""
    result = np.full(len(close), np.nan)
    if len(close) < n + 1:
        return result

    delta = np.diff(close)                     # ΔC_t = C_t - C_{t-1}
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    # Seed: SMA of first n gains/losses
    avg_gain = gains[:n].mean()
    avg_loss = losses[:n].mean()

    for i in range(n, len(delta)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - 100.0 / (1.0 + rs)

    return result


def _atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    """ATR = SMA(True Range, n).  TR_t = max(H-L, |H-C_{t-1}|, |L-C_{t-1}|)."""
    result = np.full(len(close), np.nan)
    if len(close) < n + 1:
        return result

    prev_close = close[:-1]
    h = high[1:]
    l = low[1:]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))

    # SMA(TR, n) — index offset: tr[i] corresponds to price bar i+1
    for i in range(n - 1, len(tr)):
        result[i + 1] = tr[i - n + 1 : i + 1].mean()

    return result


def _vwap_rolling(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                  volume: np.ndarray, window: int = 20) -> np.ndarray:
    """Rolling VWAP over `window` days.  TP = (H+L+C)/3, VWAP = Σ(TP*V)/ΣV."""
    tp = (high + low + close) / 3.0
    tpv = tp * volume
    result = np.full(len(close), np.nan)
    for i in range(window - 1, len(close)):
        sv = volume[i - window + 1 : i + 1].sum()
        if sv > 0:
            result[i] = tpv[i - window + 1 : i + 1].sum() / sv
    return result


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Given a per-symbol DataFrame sorted by as_of_date, return a new df with indicators."""
    df = df.sort_values("as_of_date").reset_index(drop=True)

    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)

    # EMAs
    for period in EMA_PERIODS:
        df[f"ema_{period}"] = _ema_series(c, period)

    # RSI
    df["rsi_14"] = _rsi_series(c, RSI_PERIOD)

    # ATR
    df["atr_14"] = _atr_series(h, l, c, ATR_PERIOD)

    # Rolling VWAP
    df["vwap_20"] = _vwap_rolling(h, l, c, v, VWAP_WINDOW)

    # MACD = EMA12 - EMA26
    df["macd_line"] = df["ema_12"] - df["ema_26"]

    # MACD Signal = EMA9 of MACD line
    macd_vals = df["macd_line"].to_numpy(dtype=float)
    df["macd_signal"] = _ema_series(macd_vals, 9)

    # Histogram
    df["macd_histogram"] = df["macd_line"] - df["macd_signal"]

    return df


# ─── DB helpers ───────────────────────────────────────────────────────────────

UPSERT_SQL = """
INSERT INTO public.technical_indicators
    (symbol, as_of_date, series,
     ema_9, ema_12, ema_20, ema_26, ema_50, ema_200,
     rsi_14, atr_14, vwap_20,
     macd_line, macd_signal, macd_histogram)
VALUES %s
ON CONFLICT (symbol, as_of_date, series) DO UPDATE SET
    ema_9          = EXCLUDED.ema_9,
    ema_12         = EXCLUDED.ema_12,
    ema_20         = EXCLUDED.ema_20,
    ema_26         = EXCLUDED.ema_26,
    ema_50         = EXCLUDED.ema_50,
    ema_200        = EXCLUDED.ema_200,
    rsi_14         = EXCLUDED.rsi_14,
    atr_14         = EXCLUDED.atr_14,
    vwap_20        = EXCLUDED.vwap_20,
    macd_line      = EXCLUDED.macd_line,
    macd_signal    = EXCLUDED.macd_signal,
    macd_histogram = EXCLUDED.macd_histogram,
    computed_at    = NOW()
"""

INDICATOR_COLS = [
    "symbol", "as_of_date", "series",
    "ema_9", "ema_12", "ema_20", "ema_26", "ema_50", "ema_200",
    "rsi_14", "atr_14", "vwap_20",
    "macd_line", "macd_signal", "macd_histogram",
]


def nan_to_none(val):
    """Convert numpy nan/inf to None for psycopg2."""
    if val is None:
        return None
    try:
        if np.isnan(val) or np.isinf(val):
            return None
    except (TypeError, ValueError):
        pass
    return float(val) if isinstance(val, (np.floating, float)) else val


def rows_to_tuples(df: pd.DataFrame) -> list[tuple]:
    tuples = []
    for _, row in df.iterrows():
        tuples.append((
            row["symbol"],
            row["as_of_date"],
            row["series"],
            nan_to_none(row.get("ema_9")),
            nan_to_none(row.get("ema_12")),
            nan_to_none(row.get("ema_20")),
            nan_to_none(row.get("ema_26")),
            nan_to_none(row.get("ema_50")),
            nan_to_none(row.get("ema_200")),
            nan_to_none(row.get("rsi_14")),
            nan_to_none(row.get("atr_14")),
            nan_to_none(row.get("vwap_20")),
            nan_to_none(row.get("macd_line")),
            nan_to_none(row.get("macd_signal")),
            nan_to_none(row.get("macd_histogram")),
        ))
    return tuples


def upsert_batch(cur, rows: list[tuple]) -> None:
    """Upsert rows in chunks of UPSERT_BATCH."""
    for start in range(0, len(rows), UPSERT_BATCH):
        chunk = rows[start: start + UPSERT_BATCH]
        psycopg2.extras.execute_values(cur, UPSERT_SQL, chunk, page_size=UPSERT_BATCH)


# ─── Main ─────────────────────────────────────────────────────────────────────

def make_conn(password: str):
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=password, sslmode="require",
        connect_timeout=30,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
        options="-c statement_timeout=0",  # no timeout for bulk ops
    )


def run(password: str, series_filter: str, symbol_filter: list[str], dry_run: bool, resume: bool,
        since: Optional[str] = None) -> None:
    conn = make_conn(password)
    conn.autocommit = False
    cur = conn.cursor()

    # Fetch symbol list
    if symbol_filter:
        cur.execute(
            "SELECT DISTINCT symbol FROM public.price_history WHERE series = %s AND symbol = ANY(%s) ORDER BY symbol",
            (series_filter, symbol_filter)
        )
    else:
        cur.execute(
            "SELECT DISTINCT symbol FROM public.price_history WHERE series = %s ORDER BY symbol",
            (series_filter,)
        )
    all_symbols = [r[0] for r in cur.fetchall()]
    logger.info("Found %d symbols in price_history (series=%s)", len(all_symbols), series_filter)

    if resume:
        # Skip symbols where max(technical_indicators.as_of_date) >= max(price_history.as_of_date)
        cur.execute("""
            SELECT ti.symbol
            FROM (
                SELECT symbol, MAX(as_of_date) AS last_indicator
                FROM public.technical_indicators WHERE series = %s GROUP BY symbol
            ) ti
            JOIN (
                SELECT symbol, MAX(as_of_date) AS last_price
                FROM public.price_history WHERE series = %s GROUP BY symbol
            ) ph ON ti.symbol = ph.symbol
            WHERE ti.last_indicator >= ph.last_price
        """, (series_filter, series_filter))
        already_done = {r[0] for r in cur.fetchall()}
        before = len(all_symbols)
        all_symbols = [s for s in all_symbols if s not in already_done]
        logger.info("--resume: skipping %d up-to-date symbols, %d remaining", before - len(all_symbols), len(all_symbols))

    logger.info("Processing %d symbols (series=%s)", len(all_symbols), series_filter)

    total_rows = 0
    total_symbols = len(all_symbols)
    errors = []

    for batch_start in range(0, total_symbols, FETCH_BATCH):
        batch_symbols = all_symbols[batch_start: batch_start + FETCH_BATCH]

        try:
            cur.execute("""
                SELECT symbol, as_of_date, series, open, high, low, close, volume
                FROM public.price_history
                WHERE series = %s AND symbol = ANY(%s)
                ORDER BY symbol, as_of_date
            """, (series_filter, batch_symbols))
            rows = cur.fetchall()
        except psycopg2.OperationalError as e:
            logger.warning("Connection lost fetching batch %d, reconnecting: %s", batch_start, e)
            conn = make_conn(password)
            conn.autocommit = False
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol, as_of_date, series, open, high, low, close, volume
                FROM public.price_history
                WHERE series = %s AND symbol = ANY(%s)
                ORDER BY symbol, as_of_date
            """, (series_filter, batch_symbols))
            rows = cur.fetchall()

        if not rows:
            continue

        df_raw = pd.DataFrame(rows, columns=["symbol", "as_of_date", "series", "open", "high", "low", "close", "volume"])
        df_raw[["open", "high", "low", "close"]] = df_raw[["open", "high", "low", "close"]].astype(float)
        df_raw["volume"] = df_raw["volume"].astype(float)

        # Commit per-symbol to keep transactions small and avoid pooler timeouts
        for symbol, sym_df in df_raw.groupby("symbol", sort=False):
            computed = compute_indicators(sym_df.copy())
            if since:
                # Seed indicators from full history, but only write rows on/after `since`.
                computed = computed[computed["as_of_date"].astype(str) >= since]
                if computed.empty:
                    continue
            indicator_rows = rows_to_tuples(computed)

            if dry_run:
                total_rows += len(indicator_rows)
                continue

            try:
                upsert_batch(cur, indicator_rows)
                conn.commit()
                total_rows += len(indicator_rows)
            except psycopg2.OperationalError as e:
                logger.warning("Upsert failed for %s, reconnecting: %s", symbol, e)
                try:
                    conn.rollback()
                except Exception:
                    pass
                conn = make_conn(password)
                conn.autocommit = False
                cur = conn.cursor()
                try:
                    upsert_batch(cur, indicator_rows)
                    conn.commit()
                    total_rows += len(indicator_rows)
                except Exception as e2:
                    conn.rollback()
                    errors.append((symbol, str(e2)))
                    logger.error("Failed %s after retry: %s", symbol, e2)

        done = batch_start + len(batch_symbols)
        if done % 200 == 0 or done >= total_symbols:
            logger.info("[%d/%d symbols] Total indicator rows upserted: %d  errors: %d",
                        done, total_symbols, total_rows, len(errors))

    cur.close()
    conn.close()

    if dry_run:
        logger.info("Dry run complete. Would upsert %d rows.", total_rows)
    else:
        logger.info("Done. %d indicator rows upserted for %d symbols. Errors: %d",
                    total_rows, total_symbols, len(errors))
        if errors:
            for sym, msg in errors:
                logger.error("  FAILED %s: %s", sym, msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute technical indicators from price_history")
    parser.add_argument("--series", default="EQ", help="Series to process (default: EQ)")
    parser.add_argument("--symbols", nargs="+", help="Limit to specific symbols (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--resume", action="store_true",
                        help="Skip symbols that already have indicator rows before 2024 (full-history recompute already done)")
    parser.add_argument("--since", default=None,
                        help="Only write indicator rows on/after this date (YYYY-MM-DD); still seeds from full history")
    parser.add_argument("--password", default=DB_PASSWORD, help="DB password (or set DB_PASSWORD env)")
    args = parser.parse_args()

    if not args.dry_run and not args.password:
        logger.error("DB_PASSWORD env or --password required")
        sys.exit(1)

    run(args.password, args.series, args.symbols or [], args.dry_run, args.resume, args.since)


if __name__ == "__main__":
    main()
