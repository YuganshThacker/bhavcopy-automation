"""Pure indicator math — EMA, RSI, ATR, VWAP, MACD.

Lifted verbatim from the project's compute_indicators.py so the daily and
historical paths produce numbers identical to the existing technical_indicators
rows. No DB or IO here — just numpy/pandas.

  EMA  — periods 9,12,20,26,50,200 (alpha=2/(n+1), seed=SMA of first n closes)
  RSI  — 14, SMA-seeded average gain/loss
  ATR  — 14-period SMA of True Range
  VWAP — rolling 20-day, TP=(H+L+C)/3
  MACD — EMA12-EMA26, signal=EMA9(MACD), histogram=MACD-signal
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EMA_PERIODS = [9, 12, 20, 26, 50, 200]
RSI_PERIOD = 14
ATR_PERIOD = 14
VWAP_WINDOW = 20


def _ema_series(close: np.ndarray, n: int) -> np.ndarray:
    result = np.full(len(close), np.nan)
    first_valid = int(np.argmax(~np.isnan(close)))
    if np.isnan(close[first_valid]):
        return result
    available = len(close) - first_valid
    if available < n:
        return result
    alpha = 2.0 / (n + 1)
    seed_end = first_valid + n
    result[seed_end - 1] = close[first_valid:seed_end].mean()
    for i in range(seed_end, len(close)):
        if np.isnan(close[i]):
            continue
        result[i] = close[i] * alpha + result[i - 1] * (1 - alpha)
    return result


def _rsi_series(close: np.ndarray, n: int = 14) -> np.ndarray:
    result = np.full(len(close), np.nan)
    if len(close) < n + 1:
        return result
    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
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
    result = np.full(len(close), np.nan)
    if len(close) < n + 1:
        return result
    prev_close = close[:-1]
    h = high[1:]
    l = low[1:]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    for i in range(n - 1, len(tr)):
        result[i + 1] = tr[i - n + 1: i + 1].mean()
    return result


def _vwap_rolling(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                  volume: np.ndarray, window: int = 20) -> np.ndarray:
    tp = (high + low + close) / 3.0
    tpv = tp * volume
    result = np.full(len(close), np.nan)
    for i in range(window - 1, len(close)):
        sv = volume[i - window + 1: i + 1].sum()
        if sv > 0:
            result[i] = tpv[i - window + 1: i + 1].sum() / sv
    return result


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Given a per-symbol DataFrame sorted by as_of_date, return df with indicators."""
    df = df.sort_values("as_of_date").reset_index(drop=True)
    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)

    for period in EMA_PERIODS:
        df[f"ema_{period}"] = _ema_series(c, period)
    df["rsi_14"] = _rsi_series(c, RSI_PERIOD)
    df["atr_14"] = _atr_series(h, l, c, ATR_PERIOD)
    df["vwap_20"] = _vwap_rolling(h, l, c, v, VWAP_WINDOW)
    df["macd_line"] = df["ema_12"] - df["ema_26"]
    df["macd_signal"] = _ema_series(df["macd_line"].to_numpy(dtype=float), 9)
    df["macd_histogram"] = df["macd_line"] - df["macd_signal"]
    return df


def nan_to_none(val):
    if val is None:
        return None
    try:
        if np.isnan(val) or np.isinf(val):
            return None
    except (TypeError, ValueError):
        pass
    return float(val) if isinstance(val, (np.floating, float)) else val
