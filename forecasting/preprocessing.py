"""
Time-series preparation, missing-date handling, chronological splitting,
and leakage-free lag/calendar feature generation.
"""

import numpy as np
import pandas as pd


FREQ_PANDAS_MAP = {
    'D': 'D',
    'W': 'W-MON',
    'M': 'ME',
}


def prepare_time_series(
    df,
    product_name='__ALL__',
    target_col='revenue',
    freq='D',
    missing_method='zero_fill',
):
    """
    Aggregate historical sales into a continuous chronological pandas Series.
    Handles missing dates explicitly using the requested missing_method:
      - 'zero_fill': unobserved dates are treated as 0 sales
      - 'ffill': forward-fill from previous observed period
      - 'interpolate': linear time interpolation across gaps
    Returns (series: pd.Series with DatetimeIndex, info: dict).
    """
    if df.empty:
        return pd.Series(dtype=float), {'missing_dates_filled': 0, 'raw_points': 0}

    work_df = df.copy()
    work_df['date'] = pd.to_datetime(work_df['date'])
    work_df[target_col] = pd.to_numeric(work_df[target_col], errors='coerce').fillna(0.0)

    if product_name and product_name != '__ALL__':
        work_df = work_df[work_df['product'] == product_name]

    if work_df.empty:
        return pd.Series(dtype=float), {'missing_dates_filled': 0, 'raw_points': 0}

    pandas_freq = FREQ_PANDAS_MAP.get(freq, 'D')
    daily_or_agg = (
        work_df.set_index('date')[target_col]
        .resample(pandas_freq)
        .sum(min_count=1)
        .sort_index()
    )

    missing_dates_filled = int(daily_or_agg.isna().sum())

    if missing_method == 'ffill':
        series = daily_or_agg.ffill().bfill().fillna(0.0)
    elif missing_method == 'interpolate':
        series = daily_or_agg.interpolate(method='linear').ffill().bfill().fillna(0.0)
    else:
        series = daily_or_agg.fillna(0.0)

    series = series.astype(float)
    return series, {
        'missing_dates_filled': missing_dates_filled,
        'total_points': int(len(series)),
        'freq': pandas_freq,
    }


def chronological_split(series, test_ratio=0.2, min_train=6, min_test=2):
    """
    Split a time-series strictly chronologically into training and test subsets.
    Never randomly shuffles observations.
    """
    n = len(series)
    if n < min_train + min_test:
        raise ValueError(
            f'Insufficient observations ({n}) for chronological train/test evaluation. '
            f'At least {min_train + min_test} continuous periods are required.'
        )

    test_count = max(min_test, int(round(n * test_ratio)))
    if n - test_count < min_train:
        test_count = n - min_train

    split_idx = n - test_count
    train = series.iloc[:split_idx]
    test = series.iloc[split_idx:]
    return train, test


def build_supervised_features(series, lags=(1, 2, 3, 7), include_calendar=True):
    """
    Construct leakage-free tabular features from a continuous historical series.
    All lag and rolling features use strictly past observations (.shift(1) or greater).
    """
    frame = pd.DataFrame({'y': series.values}, index=series.index)
    frame['time_idx'] = np.arange(len(frame), dtype=float)

    if include_calendar:
        frame['day_of_week'] = frame.index.dayofweek.astype(float)
        frame['month'] = frame.index.month.astype(float)
        frame['day_of_month'] = frame.index.day.astype(float)

    for lag in lags:
        if lag < len(frame):
            frame[f'lag_{lag}'] = frame['y'].shift(lag)

    rolling_win = min(7, max(2, len(frame) // 4))
    frame['rolling_mean_past'] = frame['y'].shift(1).rolling(window=rolling_win, min_periods=1).mean()

    frame = frame.dropna()
    feature_cols = [c for c in frame.columns if c != 'y']
    return frame[feature_cols], frame['y'], feature_cols, rolling_win


def build_step_features(
    history_values,
    next_date,
    step_time_idx,
    lags,
    feature_cols,
    include_calendar=True,
    rolling_win=7,
):
    """
    Build a single feature row for recursive multi-step prediction without peeking at future actuals.
    """
    row = {'time_idx': float(step_time_idx)}
    if include_calendar:
        row['day_of_week'] = float(next_date.dayofweek)
        row['month'] = float(next_date.month)
        row['day_of_month'] = float(next_date.day)

    hist_len = len(history_values)
    for lag in lags:
        col = f'lag_{lag}'
        if col in feature_cols:
            row[col] = float(history_values[-lag]) if hist_len >= lag else float(history_values[0])

    if 'rolling_mean_past' in feature_cols:
        recent_slice = history_values[-min(rolling_win, hist_len):]
        row['rolling_mean_past'] = float(np.mean(recent_slice))

    return pd.DataFrame([[row[col] for col in feature_cols]], columns=feature_cols)


def next_dates_index(last_date, periods, freq='D'):
    """Generate future pandas DatetimeIndex starting immediately after last_date."""
    pandas_freq = FREQ_PANDAS_MAP.get(freq, 'D')
    offset = pd.tseries.frequencies.to_offset(pandas_freq)
    return pd.date_range(start=last_date + offset, periods=periods, freq=pandas_freq)
