"""
InsightLab Decision Support generator.
Translates statistical trends, seasonal patterns, anomalies, and predictive forecasts
into cautious, quantitative planning insights for any numerical target metric.
"""

import numpy as np
import pandas as pd


def generate_business_insights(
    historical_series,
    forecast_values,
    target_col='value',
    product_label='All Records',
    anomalies=None,
):
    """
    Generate structured decision-support insights from actual historical data
    and model forecast outputs.
    Explicitly labels recommendations as planning / demand-planning suggestions when
    operational capacity or inventory tables are not present in the dataset.
    """
    insights = []
    if historical_series is None or len(historical_series) < 4:
        insights.append(
            {
                'category': 'Data Sufficiency',
                'severity': 'warning',
                'title': 'Limited historical observations',
                'detail': (
                    'Fewer than 4 historical periods are available. Collect more historical records '
                    'before relying on automated planning suggestions.'
                ),
            }
        )
        return insights

    values = np.asarray(historical_series, dtype=float)
    n = len(values)

    # 1. Recent vs. earlier historical momentum
    split_point = max(1, n // 2)
    earlier_mean = float(np.mean(values[:split_point]))
    recent_mean = float(np.mean(values[split_point:]))
    if earlier_mean > 0:
        hist_pct = ((recent_mean - earlier_mean) / earlier_mean) * 100.0
        if hist_pct >= 8.0:
            insights.append(
                {
                    'category': 'Historical Trend',
                    'severity': 'positive',
                    'title': f'{product_label}: "{target_col}" shows upward momentum (+{hist_pct:.1f}%)',
                    'detail': (
                        f'Average "{target_col}" in the second half of the observed history ({recent_mean:.2f}) '
                        f'exceeded the first half ({earlier_mean:.2f}). '
                        'Demand-planning suggestion (capacity/inventory stock data not included): prepare resources for higher expected activity.'
                    ),
                }
            )
        elif hist_pct <= -8.0:
            insights.append(
                {
                    'category': 'Historical Trend',
                    'severity': 'warning',
                    'title': f'{product_label}: "{target_col}" shows declining trend ({hist_pct:.1f}%)',
                    'detail': (
                        f'Average "{target_col}" decreased from {earlier_mean:.2f} to {recent_mean:.2f}. '
                        'Demand-planning suggestion (capacity/inventory stock data not included): review drivers and adjust resource allocation.'
                    ),
                }
            )
        else:
            insights.append(
                {
                    'category': 'Historical Trend',
                    'severity': 'neutral',
                    'title': f'{product_label}: "{target_col}" remained relatively stable ({hist_pct:+.1f}%)',
                    'detail': (
                        f'Recent period average ({recent_mean:.2f}) is close to the earlier baseline ({earlier_mean:.2f}).'
                    ),
                }
            )

    # 2. Forecast horizon vs recent baseline
    if forecast_values is not None and len(forecast_values) > 0:
        f_vals = np.asarray(forecast_values, dtype=float)
        forecast_mean = float(np.mean(f_vals))
        recent_window = values[-min(len(values), len(f_vals)):]
        recent_window_mean = float(np.mean(recent_window)) if len(recent_window) > 0 else 0.0

        if recent_window_mean > 0:
            f_pct = ((forecast_mean - recent_window_mean) / recent_window_mean) * 100.0
            direction = 'higher' if f_pct >= 0 else 'lower'
            insights.append(
                {
                    'category': 'Forecast Outlook',
                    'severity': 'info' if abs(f_pct) < 10 else ('positive' if f_pct > 0 else 'warning'),
                    'title': f'Forecasted "{target_col}" is {abs(f_pct):.1f}% {direction} than recent baseline',
                    'detail': (
                        f'Model predicts an average of {forecast_mean:.2f} per period over the next {len(f_vals)} period(s) '
                        f'compared to {recent_window_mean:.2f} recently. '
                        'Demand-planning suggestion (external operational constraints unavailable): use this alongside domain context.'
                    ),
                }
            )

    # 3. Day-of-week seasonal pattern (if datetime index exists)
    if isinstance(historical_series, pd.Series) and isinstance(historical_series.index, pd.DatetimeIndex):
        if len(historical_series) >= 14:
            weekday_means = historical_series.groupby(historical_series.index.day_name()).mean()
            if not weekday_means.empty and weekday_means.mean() > 0:
                peak_day = weekday_means.idxmax()
                low_day = weekday_means.idxmin()
                spread_pct = ((weekday_means.max() - weekday_means.min()) / weekday_means.mean()) * 100.0
                if spread_pct >= 15.0:
                    insights.append(
                        {
                            'category': 'Seasonality',
                            'severity': 'info',
                            'title': f'Recurring weekly pattern detected (peak on {peak_day})',
                            'detail': (
                                f'Average "{target_col}" peaks on {peak_day} ({weekday_means.max():.2f}) and dips on '
                                f'{low_day} ({weekday_means.min():.2f}). Planning suggestion: align scheduling ahead of {peak_day}.'
                            ),
                        }
                    )

    # 4. Statistical anomaly summary
    if anomalies:
        latest = anomalies[0]
        insights.append(
            {
                'category': 'Statistical Anomaly',
                'severity': 'warning',
                'title': f'{len(anomalies)} unusual statistical observation(s) flagged',
                'detail': (
                    f'Strongest statistical deviation occurred at {latest["date"]} ({latest["direction"]}, '
                    f'Z = {latest["z_score"]:+.2f}, actual {latest["actual"]} vs baseline {latest["baseline"]}). '
                    'Labeled as a statistical observation only; verify source records before acting on it.'
                ),
            }
        )

    return insights
