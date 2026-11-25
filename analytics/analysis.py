"""
Dataset-independent Exploratory Data Analysis (EDA), custom visualization builder,
and statistical anomaly detection functions.
Works with time-series datasets (sales, web traffic, expenses, sensor metrics) as well
as cross-sectional tabular datasets without a date column.
"""

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import seasonal_decompose


def compute_general_eda(
    df,
    date_col='date',
    target_col='revenue',
    group_col='product',
    group_filter='__ALL__',
    secondary_col='',
):
    """
    Compute statistical summaries, trends (when date_col exists), categorical
    breakdowns (when group_col exists), distribution histogram, numeric correlations,
    and seasonal patterns without forcing a fixed sales schema.
    """
    if df.empty:
        return {'has_data': False, 'message': 'No valid records available for analysis.'}

    work_df = df.copy()
    has_date = bool(date_col and date_col in work_df.columns)
    has_group = bool(group_col and group_col in work_df.columns)
    has_target = bool(target_col and target_col in work_df.columns)
    has_secondary = bool(secondary_col and secondary_col in work_df.columns and secondary_col != target_col)

    if not has_target:
        numeric_candidates = work_df.select_dtypes(include=[np.number]).columns.tolist()
        if numeric_candidates:
            target_col = str(numeric_candidates[0])
            has_target = True
        else:
            return {
                'has_data': False,
                'message': 'No numerical target column found in this dataset for quantitative EDA.',
            }

    work_df[target_col] = pd.to_numeric(work_df[target_col], errors='coerce')
    work_df = work_df.dropna(subset=[target_col])
    if work_df.empty:
        return {'has_data': False, 'message': f'Column "{target_col}" contains no valid numerical rows.'}

    if has_secondary:
        work_df[secondary_col] = pd.to_numeric(work_df[secondary_col], errors='coerce').fillna(0.0)

    if has_date:
        work_df[date_col] = pd.to_datetime(work_df[date_col], errors='coerce')
        work_df = work_df.dropna(subset=[date_col]).sort_values(date_col)
        if work_df.empty:
            has_date = False

    # Group comparison before filtering to one group
    all_groups = []
    group_comparison = {'available': False, 'labels': [], 'target_sum': [], 'target_mean': []}
    best_groups = []
    lowest_groups = []

    if has_group:
        work_df[group_col] = work_df[group_col].fillna('Unknown').astype(str).str.strip().replace('', 'Unknown')
        all_groups = sorted(work_df[group_col].unique().tolist())

        agg_dict = {
            'total_target': (target_col, 'sum'),
            'avg_target': (target_col, 'mean'),
            'record_count': (target_col, 'count'),
        }
        if has_secondary:
            agg_dict['total_secondary'] = (secondary_col, 'sum')

        group_df = (
            work_df.groupby(group_col, as_index=False)
            .agg(**agg_dict)
            .sort_values('total_target', ascending=False)
            .reset_index(drop=True)
        )
        group_df['total_target'] = group_df['total_target'].round(2)
        group_df['avg_target'] = group_df['avg_target'].round(2)
        if has_secondary:
            group_df['total_secondary'] = group_df['total_secondary'].round(2)

        # Normalize keys for both general and legacy templates
        records = []
        for _, r in group_df.iterrows():
            item = {
                'group': r[group_col],
                'product': r[group_col],
                'total_target': float(r['total_target']),
                'total_revenue': float(r['total_target']),
                'avg_target': float(r['avg_target']),
                'avg_daily_revenue': float(r['avg_target']),
                'record_count': int(r['record_count']),
                'total_secondary': float(r['total_secondary']) if has_secondary else None,
                'total_quantity': float(r['total_secondary']) if has_secondary else None,
            }
            records.append(item)

        best_groups = records[:5]
        lowest_groups = sorted(records, key=lambda x: x['total_target'])[:5]
        group_comparison = {
            'available': len(records) >= 1,
            'labels': [r['group'] for r in records[:15]],
            'target_sum': [r['total_target'] for r in records[:15]],
            'target_mean': [r['avg_target'] for r in records[:15]],
        }

    if has_group and group_filter and group_filter != '__ALL__':
        filtered_df = work_df[work_df[group_col] == group_filter].copy()
    else:
        filtered_df = work_df.copy()

    if filtered_df.empty:
        return {'has_data': False, 'message': f'No records found for filter "{group_filter}".'}

    # Numeric correlations across numeric columns
    num_cols = filtered_df.select_dtypes(include=[np.number]).columns.tolist()
    correlations = []
    if len(num_cols) >= 2:
        corr_matrix = filtered_df[num_cols].corr()
        for i in range(len(num_cols)):
            for j in range(i + 1, len(num_cols)):
                val = corr_matrix.iloc[i, j]
                if not pd.isna(val):
                    correlations.append({
                        'col_a': str(num_cols[i]),
                        'col_b': str(num_cols[j]),
                        'pearson_r': round(float(val), 4),
                    })

    # Time-series branches (only when date_col exists)
    daily_trend = {'available': False, 'labels': [], 'values': [], 'revenue': [], 'quantity': []}
    weekly_trend = {'available': False, 'labels': [], 'values': [], 'revenue': [], 'quantity': []}
    monthly_trend = {'available': False, 'labels': [], 'values': [], 'revenue': [], 'quantity': []}
    yearly_trend = {'available': False, 'labels': [], 'values': [], 'revenue': []}
    weekday_pattern = {'available': False, 'labels': [], 'avg_values': [], 'avg_revenue': []}
    seasonal_decomposition = {
        'available': False,
        'note': 'A mapped date column with at least 14 distinct days is required for weekly seasonal decomposition.',
        'dates': [],
        'trend': [],
        'seasonal': [],
        'residual': [],
    }

    if has_date:
        agg_cols = [target_col] + ([secondary_col] if has_secondary else [])
        daily_df = filtered_df.groupby(date_col, as_index=False)[agg_cols].sum().sort_values(date_col)
        daily_vals = [round(float(v), 2) for v in daily_df[target_col]]
        daily_sec = [round(float(v), 2) for v in daily_df[secondary_col]] if has_secondary else daily_vals
        daily_trend = {
            'available': len(daily_df) >= 2,
            'labels': [d.strftime('%Y-%m-%d') for d in daily_df[date_col]],
            'values': daily_vals,
            'revenue': daily_vals,
            'quantity': daily_sec,
        }

        weekly_df = daily_df.set_index(date_col)[agg_cols].resample('W').sum().reset_index()
        if len(weekly_df) >= 2:
            w_vals = [round(float(v), 2) for v in weekly_df[target_col]]
            w_sec = [round(float(v), 2) for v in weekly_df[secondary_col]] if has_secondary else w_vals
            weekly_trend = {
                'available': True,
                'labels': [d.strftime('%Y-%m-%d') for d in weekly_df[date_col]],
                'values': w_vals,
                'revenue': w_vals,
                'quantity': w_sec,
            }

        monthly_df = daily_df.set_index(date_col)[agg_cols].resample('ME').sum().reset_index()
        if len(monthly_df) >= 2:
            m_vals = [round(float(v), 2) for v in monthly_df[target_col]]
            m_sec = [round(float(v), 2) for v in monthly_df[secondary_col]] if has_secondary else m_vals
            monthly_trend = {
                'available': True,
                'labels': [d.strftime('%Y-%m') for d in monthly_df[date_col]],
                'values': m_vals,
                'revenue': m_vals,
                'quantity': m_sec,
            }

        yearly_df = daily_df.set_index(date_col)[agg_cols].resample('YE').sum().reset_index()
        if len(yearly_df) >= 2:
            y_vals = [round(float(v), 2) for v in yearly_df[target_col]]
            yearly_trend = {
                'available': True,
                'labels': [d.strftime('%Y') for d in yearly_df[date_col]],
                'values': y_vals,
                'revenue': y_vals,
            }

        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        daily_df['weekday'] = daily_df[date_col].dt.day_name()
        weekday_avg = daily_df.groupby('weekday')[target_col].mean().reindex(day_order).fillna(0.0).round(2)
        if daily_df[date_col].nunique() >= 14:
            weekday_pattern = {
                'available': True,
                'labels': day_order,
                'avg_values': [float(v) for v in weekday_avg.values],
                'avg_revenue': [float(v) for v in weekday_avg.values],
            }

        if len(daily_df) >= 14:
            full_idx = pd.date_range(daily_df[date_col].min(), daily_df[date_col].max(), freq='D')
            continuous = daily_df.set_index(date_col)[target_col].reindex(full_idx).fillna(0.0)
            if len(continuous) >= 14 and continuous.std() > 0:
                try:
                    decomp = seasonal_decompose(continuous, model='additive', period=7)
                    seasonal_decomposition = {
                        'available': True,
                        'note': f'Additive 7-day seasonal decomposition computed on continuous daily "{target_col}".',
                        'dates': [d.strftime('%Y-%m-%d') for d in continuous.index],
                        'trend': [None if pd.isna(v) else round(float(v), 2) for v in decomp.trend],
                        'seasonal': [round(float(v), 2) for v in decomp.seasonal],
                        'residual': [None if pd.isna(v) else round(float(v), 2) for v in decomp.resid],
                    }
                except Exception:
                    pass

        dist_source = daily_df[target_col].values
        summary_block = {
            'total_target': round(float(daily_df[target_col].sum()), 2),
            'total_revenue': round(float(daily_df[target_col].sum()), 2),
            'total_quantity': round(float(daily_df[secondary_col].sum()), 2) if has_secondary else 0.0,
            'avg_target': round(float(daily_df[target_col].mean()), 2),
            'avg_daily_revenue': round(float(daily_df[target_col].mean()), 2),
            'avg_daily_quantity': round(float(daily_df[secondary_col].mean()), 2) if has_secondary else 0.0,
            'median_target': round(float(daily_df[target_col].median()), 2),
            'median_daily_revenue': round(float(daily_df[target_col].median()), 2),
            'std_target': round(float(daily_df[target_col].std(ddof=1)), 2) if len(daily_df) > 1 else 0.0,
            'active_days': int(len(daily_df)),
            'record_count': int(len(filtered_df)),
            'date_start': daily_df[date_col].min().strftime('%Y-%m-%d'),
            'date_end': daily_df[date_col].max().strftime('%Y-%m-%d'),
        }
    else:
        dist_source = filtered_df[target_col].values
        summary_block = {
            'total_target': round(float(filtered_df[target_col].sum()), 2),
            'total_revenue': round(float(filtered_df[target_col].sum()), 2),
            'total_quantity': round(float(filtered_df[secondary_col].sum()), 2) if has_secondary else 0.0,
            'avg_target': round(float(filtered_df[target_col].mean()), 2),
            'avg_daily_revenue': round(float(filtered_df[target_col].mean()), 2),
            'avg_daily_quantity': round(float(filtered_df[secondary_col].mean()), 2) if has_secondary else 0.0,
            'median_target': round(float(filtered_df[target_col].median()), 2),
            'median_daily_revenue': round(float(filtered_df[target_col].median()), 2),
            'std_target': round(float(filtered_df[target_col].std(ddof=1)), 2) if len(filtered_df) > 1 else 0.0,
            'active_days': int(len(filtered_df)),
            'record_count': int(len(filtered_df)),
            'date_start': None,
            'date_end': None,
        }

    distribution = {'has_chart': False, 'labels': [], 'counts': []}
    if len(dist_source) >= 5 and np.ptp(dist_source) > 0:
        bin_count = min(10, max(4, int(np.sqrt(len(dist_source)))))
        counts, bin_edges = np.histogram(dist_source, bins=bin_count)
        distribution = {
            'has_chart': True,
            'labels': [f'{bin_edges[i]:.1f}–{bin_edges[i + 1]:.1f}' for i in range(len(counts))],
            'counts': [int(c) for c in counts],
        }

    return {
        'has_data': True,
        'has_date': has_date,
        'has_group': has_group,
        'has_secondary': has_secondary,
        'date_col': date_col if has_date else '',
        'target_col': target_col,
        'group_col': group_col if has_group else '',
        'secondary_col': secondary_col if has_secondary else '',
        'all_products': all_groups,
        'all_groups': all_groups,
        'selected_product': group_filter,
        'selected_group': group_filter,
        'summary': summary_block,
        'daily_trend': daily_trend,
        'weekly_trend': weekly_trend,
        'monthly_trend': monthly_trend,
        'yearly_trend': yearly_trend,
        'product_comparison': {
            'available': group_comparison['available'],
            'labels': group_comparison['labels'],
            'revenue': group_comparison['target_sum'],
            'values': group_comparison['target_sum'],
        },
        'group_comparison': group_comparison,
        'best_products': best_groups,
        'lowest_products': lowest_groups,
        'best_groups': best_groups,
        'lowest_groups': lowest_groups,
        'distribution': distribution,
        'weekday_pattern': weekday_pattern,
        'seasonal_decomposition': seasonal_decomposition,
        'correlations': correlations,
    }


def compute_eda(df, product_filter='__ALL__'):
    """Backward-compatible EDA entry point for standardized ['date','product','quantity','revenue'] DataFrames."""
    return compute_general_eda(
        df,
        date_col='date',
        target_col='revenue',
        group_col='product',
        group_filter=product_filter,
        secondary_col='quantity',
    )


def build_custom_visualization_data(df, chart_type, x_col, y_col, agg_func='sum'):
    """
    Build Chart.js payload dynamically for the Visualizations workspace page.
    Supports 'line', 'bar', 'histogram', and 'scatter'.
    """
    if df.empty or not y_col or y_col not in df.columns:
        return {'has_chart': False, 'message': 'Select a valid numerical column to visualize.'}

    work = df.copy()
    work[y_col] = pd.to_numeric(work[y_col], errors='coerce')
    work = work.dropna(subset=[y_col])
    if work.empty:
        return {'has_chart': False, 'message': f'Column "{y_col}" has no valid numerical values.'}

    if chart_type == 'histogram':
        vals = work[y_col].values
        if len(vals) < 3 or np.ptp(vals) == 0:
            return {'has_chart': False, 'message': f'Not enough spread in "{y_col}" for a distribution histogram.'}
        bins = min(12, max(4, int(np.sqrt(len(vals)))))
        counts, edges = np.histogram(vals, bins=bins)
        return {
            'has_chart': True,
            'chart_type': 'bar',
            'title': f'Distribution of {y_col}',
            'labels': [f'{edges[i]:.1f}–{edges[i + 1]:.1f}' for i in range(len(counts))],
            'values': [int(c) for c in counts],
            'y_label': 'Record Count',
        }

    if not x_col or x_col not in work.columns:
        return {'has_chart': False, 'message': 'Select an X-axis / grouping column for this chart type.'}

    if chart_type == 'scatter':
        work[x_col] = pd.to_numeric(work[x_col], errors='coerce')
        work = work.dropna(subset=[x_col])
        if len(work) < 2:
            return {'has_chart': False, 'message': f'Column "{x_col}" must be numerical for scatter plots.'}
        sampled = work.head(400)
        points = [{'x': round(float(r[x_col]), 3), 'y': round(float(r[y_col]), 3)} for _, r in sampled.iterrows()]
        return {
            'has_chart': True,
            'chart_type': 'scatter',
            'title': f'{y_col} vs. {x_col}',
            'points': points,
            'x_label': x_col,
            'y_label': y_col,
        }

    # Line or Bar aggregated by x_col
    if agg_func not in ('sum', 'mean', 'count'):
        agg_func = 'sum'

    if chart_type == 'line':
        parsed_dt = pd.to_datetime(work[x_col], errors='coerce')
        if parsed_dt.notna().mean() >= 0.7:
            work['_x_dt'] = parsed_dt
            work = work.dropna(subset=['_x_dt'])
            grouped = getattr(work.groupby('_x_dt')[y_col], agg_func)().sort_index()
            labels = [d.strftime('%Y-%m-%d') for d in grouped.index]
        else:
            grouped = getattr(work.groupby(x_col)[y_col], agg_func)().sort_index()
            labels = [str(k) for k in grouped.index]
        return {
            'has_chart': True,
            'chart_type': 'line',
            'title': f'{y_col} ({agg_func.title()}) Over {x_col}',
            'labels': labels[:180],
            'values': [round(float(v), 2) for v in grouped.values[:180]],
            'y_label': f'{y_col} ({agg_func})',
        }

    # Default Bar chart by category
    work[x_col] = work[x_col].fillna('Unknown').astype(str).str.strip()
    grouped = getattr(work.groupby(x_col)[y_col], agg_func)().sort_values(ascending=False).head(20)
    return {
        'has_chart': True,
        'chart_type': 'bar',
        'title': f'{y_col} ({agg_func.title()}) by {x_col}',
        'labels': [str(k) for k in grouped.index],
        'values': [round(float(v), 2) for v in grouped.values],
        'y_label': f'{y_col} ({agg_func})',
    }


def detect_anomalies(
    df,
    target_col='revenue',
    date_col='date',
    group_col='product',
    group_filter='__ALL__',
    window=7,
    z_threshold=2.0,
):
    """
    Detect statistically unusual values using Z-score:
      - If date_col exists: rolling baseline & rolling std over time (shifted by 1 period).
      - If date_col is absent: global mean & std across all records.
    Always labels results as statistical observations, not confirmed root causes.
    """
    if df.empty or not target_col or target_col not in df.columns:
        return {'has_data': False, 'message': 'No valid target numerical column for anomaly detection.', 'anomalies': [], 'series': {}}

    work_df = df.copy()
    work_df[target_col] = pd.to_numeric(work_df[target_col], errors='coerce')
    work_df = work_df.dropna(subset=[target_col])
    if work_df.empty:
        return {'has_data': False, 'message': f'Column "{target_col}" has no valid numbers.', 'anomalies': [], 'series': {}}

    has_group = bool(group_col and group_col in work_df.columns)
    if has_group and group_filter and group_filter != '__ALL__':
        work_df = work_df[work_df[group_col].astype(str).str.strip() == group_filter]
        scope_label = group_filter
    else:
        scope_label = 'All Records (Aggregated)'

    has_date = bool(date_col and date_col in work_df.columns)
    if has_date:
        work_df[date_col] = pd.to_datetime(work_df[date_col], errors='coerce')
        work_df = work_df.dropna(subset=[date_col])
        if work_df.empty:
            has_date = False

    if has_date:
        daily = (
            work_df.groupby(date_col, as_index=False)[target_col]
            .sum()
            .sort_values(date_col)
            .reset_index(drop=True)
        )
        if len(daily) < max(5, window):
            return {
                'has_data': False,
                'message': f'Need at least {max(5, window)} chronological periods to compute a {window}-period rolling baseline.',
                'anomalies': [],
                'series': {},
            }

        daily['baseline'] = daily[target_col].rolling(window=window, min_periods=max(3, window // 2)).mean().shift(1)
        daily['rolling_std'] = daily[target_col].rolling(window=window, min_periods=max(3, window // 2)).std(ddof=1).shift(1)
        daily['upper_band'] = daily['baseline'] + z_threshold * daily['rolling_std']
        daily['lower_band'] = (daily['baseline'] - z_threshold * daily['rolling_std']).clip(lower=0.0)

        anomalies = []
        for _, row in daily.iterrows():
            baseline, std, actual = row['baseline'], row['rolling_std'], float(row[target_col])
            if pd.isna(baseline) or pd.isna(std) or std <= 1e-9:
                continue
            z_score = (actual - float(baseline)) / float(std)
            if abs(z_score) >= z_threshold:
                diff = actual - float(baseline)
                direction = 'Unusual Increase' if diff > 0 else 'Unusual Decrease'
                anomalies.append({
                    'date': row[date_col].strftime('%Y-%m-%d'),
                    'product': scope_label,
                    'actual': round(actual, 2),
                    'baseline': round(float(baseline), 2),
                    'difference': round(diff, 2),
                    'z_score': round(float(z_score), 2),
                    'direction': direction,
                    'observation_note': (
                        f'Statistical observation: "{target_col}" is {abs(z_score):.2f} standard deviations '
                        f'{"above" if diff > 0 else "below"} the prior {window}-period rolling average '
                        '(verify operational events, seasonality, or data entry).'
                    ),
                })

        anomalies.sort(key=lambda item: abs(item['z_score']), reverse=True)
        return {
            'has_data': True,
            'mode': 'time_series',
            'product_label': scope_label,
            'target_col': target_col,
            'window': window,
            'z_threshold': z_threshold,
            'total_points': int(len(daily)),
            'anomaly_count': int(len(anomalies)),
            'anomalies': anomalies,
            'series': {
                'dates': [d.strftime('%Y-%m-%d') for d in daily[date_col]],
                'actual': [round(float(v), 2) for v in daily[target_col]],
                'baseline': [None if pd.isna(v) else round(float(v), 2) for v in daily['baseline']],
                'upper_band': [None if pd.isna(v) else round(float(v), 2) for v in daily['upper_band']],
                'lower_band': [None if pd.isna(v) else round(float(v), 2) for v in daily['lower_band']],
                'anomaly_dates': {a['date']: a['actual'] for a in anomalies},
            },
        }

    # Cross-sectional record mode (dataset has no date column)
    vals = work_df[target_col].astype(float)
    if len(vals) < 5 or vals.std(ddof=1) <= 1e-9:
        return {
            'has_data': False,
            'message': 'Need at least 5 non-constant records to compute statistical Z-score deviations.',
            'anomalies': [],
            'series': {},
        }

    mean_val = float(vals.mean())
    std_val = float(vals.std(ddof=1))
    upper = mean_val + z_threshold * std_val
    lower = mean_val - z_threshold * std_val

    anomalies = []
    record_labels = []
    anomaly_map = {}
    for idx, actual in enumerate(vals):
        label = f'Row {idx + 1}'
        record_labels.append(label)
        z_score = (float(actual) - mean_val) / std_val
        if abs(z_score) >= z_threshold:
            diff = float(actual) - mean_val
            direction = 'Unusual High Value' if diff > 0 else 'Unusual Low Value'
            anomaly_map[label] = round(float(actual), 2)
            anomalies.append({
                'date': label,
                'product': scope_label,
                'actual': round(float(actual), 2),
                'baseline': round(mean_val, 2),
                'difference': round(diff, 2),
                'z_score': round(z_score, 2),
                'direction': direction,
                'observation_note': (
                    f'Statistical observation: "{target_col}" at {label} is {abs(z_score):.2f} standard deviations '
                    f'{"above" if diff > 0 else "below"} the dataset mean ({mean_val:.2f}).'
                ),
            })

    anomalies.sort(key=lambda item: abs(item['z_score']), reverse=True)
    return {
        'has_data': True,
        'mode': 'cross_sectional',
        'product_label': scope_label,
        'target_col': target_col,
        'window': 0,
        'z_threshold': z_threshold,
        'total_points': int(len(vals)),
        'anomaly_count': int(len(anomalies)),
        'anomalies': anomalies,
        'series': {
            'dates': record_labels[:250],
            'actual': [round(float(v), 2) for v in vals[:250]],
            'baseline': [round(mean_val, 2)] * min(250, len(vals)),
            'upper_band': [round(upper, 2)] * min(250, len(vals)),
            'lower_band': [round(lower, 2)] * min(250, len(vals)),
            'anomaly_dates': anomaly_map,
        },
    }


def detect_sales_anomalies(df, product_filter='__ALL__', target_col='revenue', window=7, z_threshold=2.0):
    """Backward-compatible wrapper for standardized sales DataFrames."""
    return detect_anomalies(
        df,
        target_col=target_col,
        date_col='date',
        group_col='product',
        group_filter=product_filter,
        window=window,
        z_threshold=z_threshold,
    )
