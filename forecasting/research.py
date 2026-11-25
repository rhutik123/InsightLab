"""
Controlled Research Experiment Lab engine covering all 6 academic research areas:
1. Algorithm comparison
2. Effect of historical dataset size
3. Effect of seasonality features
4. Effect of missing data & imputation strategies
5. Effect of sales volatility / fluctuations
6. Product-wise forecasting comparison
"""

import numpy as np
import pandas as pd

from analytics.analysis import detect_sales_anomalies
from .forecasting_models import MODEL_DISPLAY_NAMES, run_forecast_experiment
from .preprocessing import chronological_split, prepare_time_series

ALL_MODELS = ['naive', 'moving_average', 'linear_regression', 'random_forest', 'arima']


def run_research_experiment_suite(
    df,
    experiment_type,
    product_name='__ALL__',
    model_name='random_forest',
    target_col='revenue',
    freq='D',
    missing_method='zero_fill',
    test_ratio=0.2,
    horizon=14,
    random_seed=42,
):
    """
    Execute a reproducible research experiment suite on the standardized DataFrame.
    Returns (records: list[dict], summary_note: str) where each record contains
    label, model_name, metrics, and experiment output ready to store in ForecastExperiment.
    """
    records = []

    if experiment_type == 'algorithm_comparison':
        series, _ = prepare_time_series(df, product_name, target_col, freq, missing_method)
        for m in ALL_MODELS:
            res = run_forecast_experiment(
                series, m, freq=freq, test_ratio=test_ratio, forecast_horizon=horizon, random_seed=random_seed
            )
            res['experiment_label'] = MODEL_DISPLAY_NAMES[m]
            records.append(res)
        note = (
            'Evaluated all 5 algorithms on the identical chronological test split. '
            'Differences reflect empirical error on this dataset rather than universal model superiority.'
        )
        return records, note

    if experiment_type == 'dataset_size':
        series, _ = prepare_time_series(df, product_name, target_col, freq, missing_method)
        full_train, fixed_test = chronological_split(series, test_ratio=test_ratio)
        fractions = [0.25, 0.50, 0.75, 1.0]
        for frac in fractions:
            sub_len = max(6, int(round(len(full_train) * frac)))
            sub_train = full_train.iloc[-sub_len:]
            res = run_forecast_experiment(
                series,
                model_name,
                freq=freq,
                forecast_horizon=horizon,
                random_seed=random_seed,
                override_train=sub_train,
                override_test=fixed_test,
            )
            pct = int(frac * 100)
            res['experiment_label'] = f'{pct}% Train History ({sub_len} periods)'
            records.append(res)
        note = (
            'Compared progressive training window sizes against the exact same held-out test period '
            'to observe how historical depth influences accuracy.'
        )
        return records, note

    if experiment_type == 'seasonality':
        series, _ = prepare_time_series(df, product_name, target_col, freq, missing_method)
        for m in ('linear_regression', 'random_forest'):
            for use_cal in (False, True):
                res = run_forecast_experiment(
                    series,
                    m,
                    freq=freq,
                    test_ratio=test_ratio,
                    forecast_horizon=horizon,
                    random_seed=random_seed,
                    include_calendar=use_cal,
                )
                cal_tag = 'With Calendar Features' if use_cal else 'Lag-Only (No Calendar)'
                res['experiment_label'] = f'{MODEL_DISPLAY_NAMES[m]} — {cal_tag}'
                records.append(res)
        note = (
            'Tested whether adding calendar seasonality features (day of week, month, day of month) '
            'improves predictive accuracy over lag-only features.'
        )
        return records, note

    if experiment_type == 'missing_data':
        series, _ = prepare_time_series(df, product_name, target_col, freq, missing_method)
        base_train, fixed_test = chronological_split(series, test_ratio=test_ratio)
        rng = np.random.default_rng(random_seed)
        drop_rates = [0.0, 0.10, 0.20, 0.30]
        for rate in drop_rates:
            corrupted_train = base_train.copy()
            if rate > 0 and len(corrupted_train) > 5:
                n_drop = max(1, int(round(len(corrupted_train) * rate)))
                # Keep first and last training points so boundaries stay intact
                candidate_idx = np.arange(1, len(corrupted_train) - 1)
                if len(candidate_idx) >= n_drop:
                    chosen = rng.choice(candidate_idx, size=n_drop, replace=False)
                    corrupted_train.iloc[chosen] = np.nan

            if missing_method == 'ffill':
                imputed_train = corrupted_train.ffill().bfill().fillna(0.0)
            elif missing_method == 'interpolate':
                imputed_train = corrupted_train.interpolate(method='linear').ffill().bfill().fillna(0.0)
            else:
                imputed_train = corrupted_train.fillna(0.0)

            res = run_forecast_experiment(
                series,
                model_name,
                freq=freq,
                forecast_horizon=horizon,
                random_seed=random_seed,
                override_train=imputed_train,
                override_test=fixed_test,
            )
            res['experiment_label'] = f'{int(rate * 100)}% Missing ({missing_method})'
            records.append(res)
        note = (
            f'Simulated controlled training data gaps (seeded={random_seed}) repaired via "{missing_method}" '
            'and evaluated against the uncorrupted chronological test period.'
        )
        return records, note

    if experiment_type == 'volatility':
        products = sorted(df['product'].dropna().unique().tolist())
        if len(products) < 2:
            # Single-series fallback: compare Naive vs ML on aggregated data with anomaly count
            series, _ = prepare_time_series(df, '__ALL__', target_col, freq, missing_method)
            anom_info = detect_sales_anomalies(df, '__ALL__', target_col=target_col)
            cv = float(series.std() / series.mean()) if series.mean() > 0 else 0.0
            for m in ('naive', model_name):
                res = run_forecast_experiment(
                    series, m, freq=freq, test_ratio=test_ratio, forecast_horizon=horizon, random_seed=random_seed
                )
                res['experiment_label'] = (
                    f'{MODEL_DISPLAY_NAMES[m]} (CV={cv:.2f}, {anom_info.get("anomaly_count", 0)} anomalies)'
                )
                records.append(res)
            note = 'Single-product dataset: reported coefficient of variation (CV) and detected anomalies alongside errors.'
            return records, note

        for prod in products[:6]:
            series, _ = prepare_time_series(df, prod, target_col, freq, missing_method)
            if len(series) < 8:
                continue
            cv = float(series.std() / series.mean()) if series.mean() > 0 else 0.0
            anom_info = detect_sales_anomalies(df, prod, target_col=target_col)
            res = run_forecast_experiment(
                series, model_name, freq=freq, test_ratio=test_ratio, forecast_horizon=horizon, random_seed=random_seed
            )
            res['product_override'] = prod
            res['experiment_label'] = (
                f'{prod} (CV={cv:.2f}, {anom_info.get("anomaly_count", 0)} anomalies)'
            )
            records.append(res)
        note = (
            'Compared forecasting errors across products with differing demand volatility (Coefficient of Variation) '
            'and statistical anomaly counts.'
        )
        return records, note

    if experiment_type == 'product_comparison':
        products = sorted(df['product'].dropna().unique().tolist())
        if not products:
            raise ValueError('No products found in this dataset for product-wise comparison.')
        for prod in products[:8]:
            series, _ = prepare_time_series(df, prod, target_col, freq, missing_method)
            if len(series) < 8:
                continue
            res = run_forecast_experiment(
                series, model_name, freq=freq, test_ratio=test_ratio, forecast_horizon=horizon, random_seed=random_seed
            )
            res['product_override'] = prod
            res['experiment_label'] = f'{prod} ({MODEL_DISPLAY_NAMES[model_name]})'
            records.append(res)
        note = (
            f'Evaluated {MODEL_DISPLAY_NAMES[model_name]} independently across up to 8 products using identical split rules.'
        )
        return records, note

    raise ValueError(f'Unsupported experiment type: {experiment_type}')
