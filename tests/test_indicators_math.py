"""Tests for the pure indicator math.

Reference values are computed independently in the test (by hand or with a
direct formula), not by calling the function under test a second time.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bhavcopy_pipeline import indicators_math as im


def _frame(close, high=None, low=None, volume=None) -> pd.DataFrame:
    n = len(close)
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({
        "as_of_date": pd.date_range("2026-01-01", periods=n, freq="B"),
        "close": close,
        "high": close + 1 if high is None else high,
        "low": close - 1 if low is None else low,
        "volume": np.full(n, 1000.0) if volume is None else volume,
    })


class TestEma:
    def test_seed_is_sma_of_first_n_then_recursive(self) -> None:
        close = np.array([10, 11, 12, 13, 14, 15], dtype=float)
        out = im._ema_series(close, 3)
        assert np.isnan(out[:2]).all()
        assert out[2] == pytest.approx(11.0)            # mean(10, 11, 12)
        alpha = 2 / (3 + 1)
        expected = 11.0
        for i in range(3, 6):
            expected = close[i] * alpha + expected * (1 - alpha)
            assert out[i] == pytest.approx(expected)

    def test_not_enough_history_yields_no_values(self) -> None:
        assert np.isnan(im._ema_series(np.array([1.0, 2.0]), 3)).all()

    def test_constant_series_ema_equals_the_constant(self) -> None:
        out = im._ema_series(np.full(30, 42.0), 9)
        assert np.allclose(out[8:], 42.0)


class TestRsi:
    def test_only_gains_is_100(self) -> None:
        out = im._rsi_series(np.arange(1, 31, dtype=float), 14)
        assert np.allclose(out[15:], 100.0)

    def test_only_losses_is_0(self) -> None:
        out = im._rsi_series(np.arange(30, 0, -1, dtype=float), 14)
        assert np.allclose(out[15:], 0.0)

    def test_values_stay_within_0_and_100(self) -> None:
        rng = np.random.default_rng(7)
        close = 100 + rng.normal(0, 2, 200).cumsum()
        out = im._rsi_series(close, 14)
        valid = out[~np.isnan(out)]
        assert valid.size > 0
        assert ((valid >= 0) & (valid <= 100)).all()

    def test_short_series_yields_no_values(self) -> None:
        assert np.isnan(im._rsi_series(np.arange(10, dtype=float), 14)).all()


class TestAtr:
    def test_constant_range_with_no_gaps_equals_the_range(self) -> None:
        close = np.full(20, 100.0)
        out = im._atr_series(close + 2, close - 2, close, 14)
        assert np.allclose(out[14:], 4.0)
        assert np.isnan(out[:14]).all()

    def test_true_range_uses_gap_from_previous_close(self) -> None:
        # Bar 1 gaps up: prev close 100, high 111, low 109 -> TR = 11, not 2.
        close = np.array([100.0, 110.0])
        high = np.array([101.0, 111.0])
        low = np.array([99.0, 109.0])
        out = im._atr_series(high, low, close, 1)
        assert out[1] == pytest.approx(11.0)


class TestVwap:
    def test_constant_price_vwap_is_that_price(self) -> None:
        c = np.full(25, 50.0)
        out = im._vwap_rolling(c, c, c, np.full(25, 10.0), 20)
        assert np.allclose(out[19:], 50.0)

    def test_volume_weights_the_typical_price(self) -> None:
        c = np.array([10.0, 20.0])
        out = im._vwap_rolling(c, c, c, np.array([1.0, 3.0]), 2)
        assert out[1] == pytest.approx((10 * 1 + 20 * 3) / 4)

    def test_zero_volume_window_is_left_empty(self) -> None:
        c = np.full(3, 10.0)
        out = im._vwap_rolling(c, c, c, np.zeros(3), 2)
        assert np.isnan(out).all()


class TestComputeIndicators:
    def test_macd_histogram_is_line_minus_signal(self) -> None:
        rng = np.random.default_rng(1)
        df = im.compute_indicators(_frame(100 + rng.normal(0, 1, 120).cumsum()))
        valid = df.dropna(subset=["macd_histogram"])
        assert len(valid) > 0
        assert np.allclose(valid["macd_histogram"], valid["macd_line"] - valid["macd_signal"])

    def test_sorts_by_date_before_computing(self) -> None:
        df = _frame(np.arange(1, 41, dtype=float))
        shuffled = df.sample(frac=1, random_state=3)
        a = im.compute_indicators(df)
        b = im.compute_indicators(shuffled)
        pd.testing.assert_series_equal(a["ema_9"], b["ema_9"])

    def test_adds_every_documented_column(self) -> None:
        df = im.compute_indicators(_frame(np.arange(1, 30, dtype=float)))
        for col in [*(f"ema_{p}" for p in im.EMA_PERIODS), "rsi_14", "atr_14",
                    "vwap_20", "macd_line", "macd_signal", "macd_histogram"]:
            assert col in df.columns


class TestNanToNone:
    @pytest.mark.parametrize("value", [float("nan"), np.nan, float("inf"), None])
    def test_missing_values_become_none(self, value) -> None:
        assert im.nan_to_none(value) is None

    def test_numpy_floats_become_python_floats(self) -> None:
        out = im.nan_to_none(np.float64(1.5))
        assert out == 1.5 and type(out) is float

    def test_non_numeric_passes_through(self) -> None:
        assert im.nan_to_none("NSE") == "NSE"
        assert not math.isnan(im.nan_to_none(3))
