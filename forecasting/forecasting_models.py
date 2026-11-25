"""
Forecasting algorithms:
1. Naive Forecast (Baseline)
2. Moving Average
3. Linear Regression (lag + calendar features, recursive multi-step)
4. Random Forest Regressor (lag + calendar features, recursive multi-step)
5. ARIMA (Statsmodels)

All models train strictly on historical training observations and generate
out-of-sample predictions with non-negative clipping for sales quantities/revenue.
"""

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from statsmodels.tsa.arima.model import ARIMA

from .evaluation import evaluate_predictions
from .preprocessing import (
    build_step_features,
    build_supervised_features,
    chronological_split,
    next_dates_index,
)

MODEL_DISPLAY_NAMES = {
    'naive': 'Naive Forecast (Baseline)',
    'moving_average': 'Moving Average (7-Period)',
    'linear_regression': 'Linear Regression',
    'random_forest': 'Random Forest Regressor',
    'arima': 'ARIMA (Statsmodels)',
}


def _clip_non_negative(values):
    """Sales quantities and revenue forecasts are clipped at zero."""
    return [round(max(0.0, float(v)), 2) for v in values]


def naive_forecast(train_series, horizon):
    """Repeat the last observed training value across the forecast horizon."""
    last_val = float(train_series.iloc[-1])
    return _clip_non_negative([last_val] * horizon), 'Repeated last training observation.'


def moving_average_forecast(train_series, horizon, window=7):
    """Recursive moving average over the most recent `window` observations."""
    effective_window = min(window, len(train_series))
    history = list(train_series.values.astype(float))
    preds = []
    for _ in range(horizon):
        next_val = max(0.0, float(np.mean(history[-effective_window:])))
        preds.append(round(next_val, 2))
        history.append(next_val)
    return preds, f'Recursive {effective_window}-period moving average.'


def _select_lags(train_len):
    """Choose lag depths appropriate for the available training length."""
    candidate_lags = [1, 2, 3, 7]
    valid_lags = [lag for lag in candidate_lags if lag < max(2, train_len // 2)]
    return tuple(valid_lags) if valid_lags else (1,)


def _recursive_ml_forecast(
    train_series,
    future_dates,
    estimator,
    include_calendar=True,
):
    """Fit a scikit-learn regressor on training lags/calendar and predict recursively."""
    lags = _select_lags(len(train_series))
    X_train, y_train, feature_cols, rolling_win = build_supervised_features(
        train_series, lags=lags, include_calendar=include_calendar
    )
    if len(X_train) < 3:
        # Fallback if series is too short for lagged supervision
        return naive_forecast(train_series, len(future_dates))

    estimator.fit(X_train, y_train)

    history = list(train_series.values.astype(float))
    base_idx = len(train_series)
    preds = []
    for step, dt in enumerate(future_dates):
        step_X = build_step_features(
            history,
            next_date=dt,
            step_time_idx=base_idx + step,
            lags=lags,
            feature_cols=feature_cols,
            include_calendar=include_calendar,
            rolling_win=rolling_win,
        )
        pred_val = max(0.0, float(estimator.predict(step_X)[0]))
        preds.append(round(pred_val, 2))
        history.append(pred_val)

    return preds, f'Trained with lags {lags} (calendar={include_calendar}).'


def linear_regression_forecast(train_series, future_dates, include_calendar=True):
    """Linear Regression forecast using lag and calendar features."""
    return _recursive_ml_forecast(
        train_series, future_dates, LinearRegression(), include_calendar=include_calendar
    )


def random_forest_forecast(train_series, future_dates, random_seed=42, include_calendar=True):
    """Random Forest Regressor forecast using lag and calendar features."""
    rf = RandomForestRegressor(n_estimators=100, random_state=random_seed, max_depth=8)
    return _recursive_ml_forecast(
        train_series, future_dates, rf, include_calendar=include_calendar
    )


def arima_forecast(train_series, horizon):
    """
    Fit a Statsmodels ARIMA model on the training series with sensible fallbacks
    when series length or variance causes non-convergence.
    """
    values = train_series.values.astype(float)
    if len(values) < 8 or np.std(values) < 1e-6:
        return naive_forecast(train_series, horizon)[0], (
            'Series too short or constant for ARIMA; fell back to Naive baseline.'
        )

    candidate_orders = [(1, 1, 1), (1, 0, 0), (0, 1, 1)]
    for order in candidate_orders:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                model = ARIMA(values, order=order)
                fitted = model.fit()
                forecast_arr = fitted.forecast(steps=horizon)
            return _clip_non_negative(forecast_arr), f'Statsmodels ARIMA{order}.'
        except Exception:
            continue

    return naive_forecast(train_series, horizon)[0], (
        'ARIMA failed to converge on this series; fell back to Naive baseline.'
    )


def _predict_for_dates(model_name, train_series, target_dates, random_seed=42, include_calendar=True):
    """Dispatch prediction to the requested model for a specific future DatetimeIndex."""
    horizon = len(target_dates)
    if model_name == 'naive':
        return naive_forecast(train_series, horizon)
    if model_name == 'moving_average':
        return moving_average_forecast(train_series, horizon, window=7)
    if model_name == 'linear_regression':
        return linear_regression_forecast(
            train_series, target_dates, include_calendar=include_calendar
        )
    if model_name == 'random_forest':
        return random_forest_forecast(
            train_series, target_dates, random_seed=random_seed, include_calendar=include_calendar
        )
    if model_name == 'arima':
        return arima_forecast(train_series, horizon)
    raise ValueError(f'Unknown forecasting model: {model_name}')


def run_forecast_experiment(
    series,
    model_name,
    freq='D',
    test_ratio=0.2,
    forecast_horizon=14,
    random_seed=42,
    include_calendar=True,
    override_train=None,
    override_test=None,
):
    """
    Evaluate a model on a chronological train/test split and then refit on the full
    available history to generate future forecasts for `forecast_horizon` periods.
    """
    if override_train is not None and override_test is not None:
        train_series = override_train
        test_series = override_test
    else:
        train_series, test_series = chronological_split(series, test_ratio=test_ratio)

    test_preds, test_note = _predict_for_dates(
        model_name,
        train_series,
        test_series.index,
        random_seed=random_seed,
        include_calendar=include_calendar,
    )
    metrics = evaluate_predictions(test_series.values, test_preds)

    # Refit on the full series to project into the future
    full_series = pd.concat([train_series, test_series])
    future_dates = next_dates_index(full_series.index[-1], forecast_horizon, freq=freq)
    future_preds, future_note = _predict_for_dates(
        model_name,
        full_series,
        future_dates,
        random_seed=random_seed,
        include_calendar=include_calendar,
    )

    limitations = []
    if len(full_series) < 30:
        limitations.append(
            f'Only {len(full_series)} periods are available; ML and ARIMA accuracy may be limited on short histories.'
        )
    if 'fell back' in test_note.lower():
        limitations.append(test_note)

    return {
        'model_name': model_name,
        'model_display': MODEL_DISPLAY_NAMES.get(model_name, model_name),
        'train_start_date': train_series.index[0].date(),
        'train_end_date': train_series.index[-1].date(),
        'test_start_date': test_series.index[0].date(),
        'test_end_date': test_series.index[-1].date(),
        'train_rows': int(len(train_series)),
        'test_rows': int(len(test_series)),
        'metrics': metrics,
        'results': {
            'train_dates': [d.strftime('%Y-%m-%d') for d in train_series.index],
            'train_values': [round(float(v), 2) for v in train_series.values],
            'test_dates': [d.strftime('%Y-%m-%d') for d in test_series.index],
            'test_actuals': [round(float(v), 2) for v in test_series.values],
            'test_predictions': test_preds,
            'future_dates': [d.strftime('%Y-%m-%d') for d in future_dates],
            'future_predictions': future_preds,
            'model_note': future_note,
            'limitations': limitations,
        },
    }
