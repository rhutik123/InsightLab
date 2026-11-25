"""
Views for the InsightLab main dashboard, general exploratory data analysis,
custom interactive visualizations, and statistical anomaly detection.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from datasets.data_processing import detect_column_schema, load_general_dataframe, read_raw_csv
from datasets.models import SalesDataset, WorkspaceActivity
from forecasting.models import ForecastExperiment
from reports.models import GeneratedReport

from .analysis import build_custom_visualization_data, compute_general_eda, detect_anomalies


def _load_active_general_df(dataset, version='cleaned'):
    """
    Load the dataset's full DataFrame preserving all original column names,
    returning (df, schema, used_version).
    """
    use_cleaned = version == 'cleaned' and dataset.has_cleaned_version and dataset.cleaned_file
    file_path = dataset.cleaned_file.path if use_cleaned else dataset.source_file_path
    used_version = 'cleaned' if use_cleaned else 'original'

    raw_df = read_raw_csv(file_path)
    detected = detect_column_schema(raw_df)
    stored_schema = dataset.column_schema or {}
    schema = {
        'column_types': stored_schema.get('column_types') or detected['column_types'],
        'date_columns': stored_schema.get('date_columns') or detected['date_columns'],
        'numeric_columns': stored_schema.get('numeric_columns') or detected['numeric_columns'],
        'categorical_columns': stored_schema.get('categorical_columns') or detected['categorical_columns'],
        'primary_date_column': (
            stored_schema['primary_date_column']
            if 'primary_date_column' in stored_schema
            else dataset.date_column
        ),
        'primary_group_column': (
            stored_schema['primary_group_column']
            if 'primary_group_column' in stored_schema
            else dataset.product_column
        ),
        'primary_target_column': (
            stored_schema.get('primary_target_column') or dataset.revenue_column or detected['primary_target_column']
        ),
        'secondary_numeric_column': (
            stored_schema.get('secondary_numeric_column')
            if 'secondary_numeric_column' in stored_schema
            else dataset.quantity_column
        ),
    }

    # If an older cleaned CSV normalized headers to date/product/quantity/revenue, align cleanly
    if use_cleaned and set(['date', 'product', 'quantity', 'revenue']).issubset(raw_df.columns):
        if schema['primary_target_column'] not in raw_df.columns:
            schema = {
                'column_types': {'date': 'datetime', 'product': 'categorical', 'quantity': 'numeric', 'revenue': 'numeric'},
                'date_columns': ['date'],
                'numeric_columns': ['revenue', 'quantity'],
                'categorical_columns': ['product'],
                'primary_date_column': 'date',
                'primary_group_column': 'product',
                'primary_target_column': 'revenue',
                'secondary_numeric_column': 'quantity',
            }

    df = load_general_dataframe(file_path, schema)
    return df, schema, used_version


@login_required
def dashboard_view(request):
    """
    InsightLab main analytical workspace dashboard.
    Displays general workspace KPIs and dataset-aware visualizations from real user data.
    """
    user_datasets = SalesDataset.objects.filter(user=request.user)
    mapped_datasets = user_datasets.filter(is_mapped=True)

    total_datasets = user_datasets.count()
    total_records = 0
    total_numeric_columns = 0
    total_categorical_columns = 0

    for ds in mapped_datasets:
        prof = ds.active_profile
        total_records += int(prof.get('row_count') or 0)
        num_count = prof.get('numeric_col_count')
        if num_count is None:
            num_count = len((ds.column_schema or {}).get('numeric_columns') or []) or (2 if ds.revenue_column else 0)
        total_numeric_columns += int(num_count)
        total_categorical_columns += int(prof.get('categorical_col_count') or 0)

    recent_experiments = ForecastExperiment.objects.filter(user=request.user).select_related('dataset')[:6]
    total_analyses = (
        ForecastExperiment.objects.filter(user=request.user).count()
        + GeneratedReport.objects.filter(user=request.user).count()
    )
    recent_activity = WorkspaceActivity.objects.filter(user=request.user)[:8]

    dashboard_charts = None
    latest_ds = mapped_datasets.first()
    if latest_ds:
        try:
            df, schema, _ = _load_active_general_df(latest_ds, 'cleaned')
            target_col = schema.get('primary_target_column') or ''
            date_col = schema.get('primary_date_column') or ''
            group_col = schema.get('primary_group_column') or ''
            secondary_col = schema.get('secondary_numeric_column') or ''

            eda = compute_general_eda(
                df,
                date_col=date_col,
                target_col=target_col,
                group_col=group_col,
                secondary_col=secondary_col,
            )
            if eda.get('has_data'):
                used_target = eda['target_col']
                if eda['has_date'] and eda['daily_trend']['available']:
                    primary_chart = {
                        'type': 'line',
                        'title': f'{used_target} Trend — {latest_ds.name}',
                        'subtitle': f'Time-series along "{eda["date_col"]}"',
                        'labels': eda['daily_trend']['labels'],
                        'values': eda['daily_trend']['values'],
                        'series_label': used_target,
                    }
                elif eda['distribution']['has_chart']:
                    primary_chart = {
                        'type': 'bar',
                        'title': f'{used_target} Distribution — {latest_ds.name}',
                        'subtitle': 'Frequency distribution across records',
                        'labels': eda['distribution']['labels'],
                        'values': eda['distribution']['counts'],
                        'series_label': 'Record Count',
                    }
                else:
                    primary_chart = None

                if eda['group_comparison']['available']:
                    secondary_chart = {
                        'type': 'bar',
                        'title': f'{used_target} by {eda["group_col"]}',
                        'subtitle': 'Top categorical segments',
                        'labels': eda['group_comparison']['labels'][:8],
                        'values': eda['group_comparison']['target_sum'][:8],
                        'series_label': used_target,
                    }
                else:
                    prof = latest_ds.active_profile
                    num_stats = prof.get('numeric_columns_stats') or {}
                    if num_stats:
                        secondary_chart = {
                            'type': 'bar',
                            'title': f'Numerical Column Means — {latest_ds.name}',
                            'subtitle': 'Average values across numerical columns',
                            'labels': list(num_stats.keys())[:8],
                            'values': [float(v.get('mean') or 0) for v in list(num_stats.values())[:8]],
                            'series_label': 'Column Mean',
                        }
                    else:
                        secondary_chart = None

                dashboard_charts = {
                    'dataset_name': latest_ds.name,
                    'primary_chart': primary_chart,
                    'secondary_chart': secondary_chart,
                }
        except Exception:
            dashboard_charts = None

    context = {
        'active_nav': 'dashboard',
        'page_title': 'Dashboard',
        'total_datasets': total_datasets,
        'total_records': total_records,
        'total_numeric_columns': total_numeric_columns,
        'total_categorical_columns': total_categorical_columns,
        'total_analyses': total_analyses,
        'recent_experiments': recent_experiments,
        'recent_activity': recent_activity,
        'has_datasets': mapped_datasets.exists(),
        'dashboard_charts': dashboard_charts,
    }
    return render(request, 'dashboard.html', context)


@login_required
def analysis_view(request):
    """Dataset-independent Exploratory Data Analysis with interactive Chart.js visuals."""
    mapped_datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)
    selected_dataset = None
    ds_id = request.GET.get('dataset')
    if ds_id:
        selected_dataset = mapped_datasets.filter(pk=ds_id).first()
    if not selected_dataset:
        selected_dataset = mapped_datasets.first()

    selected_version = request.GET.get('version', 'cleaned')
    selected_target = request.GET.get('target', '').strip()
    selected_group_col = request.GET.get('group_col', '').strip()
    selected_group_val = request.GET.get('product', '__ALL__').strip() or '__ALL__'

    eda_result = None
    used_version = 'original'
    numeric_cols = []
    categorical_cols = []

    if selected_dataset:
        try:
            df, schema, used_version = _load_active_general_df(selected_dataset, selected_version)
            numeric_cols = [c for c in (schema.get('numeric_columns') or []) if c in df.columns]
            if not numeric_cols:
                numeric_cols = [str(c) for c in df.select_dtypes(include='number').columns]
            categorical_cols = [c for c in (schema.get('categorical_columns') or []) if c in df.columns]

            if not selected_target or selected_target not in numeric_cols:
                selected_target = schema.get('primary_target_column') or (numeric_cols[0] if numeric_cols else '')

            if 'group_col' not in request.GET:
                selected_group_col = schema.get('primary_group_column') or (categorical_cols[0] if categorical_cols else '')
            elif selected_group_col and selected_group_col not in df.columns:
                selected_group_col = ''

            eda_result = compute_general_eda(
                df,
                date_col=schema.get('primary_date_column') or '',
                target_col=selected_target,
                group_col=selected_group_col,
                group_filter=selected_group_val,
                secondary_col=schema.get('secondary_numeric_column') or '',
            )
            if not eda_result.get('has_data') and selected_group_val != '__ALL__':
                selected_group_val = '__ALL__'
                eda_result = compute_general_eda(
                    df,
                    date_col=schema.get('primary_date_column') or '',
                    target_col=selected_target,
                    group_col=selected_group_col,
                    group_filter='__ALL__',
                    secondary_col=schema.get('secondary_numeric_column') or '',
                )
        except FileNotFoundError:
            messages.error(request, 'Dataset CSV file could not be found on disk.')
        except Exception as exc:
            messages.error(request, f'Analysis failed: {exc}')

    context = {
        'active_nav': 'analysis',
        'page_title': 'Data Analysis',
        'datasets': mapped_datasets,
        'selected_dataset': selected_dataset,
        'numeric_cols': numeric_cols,
        'categorical_cols': categorical_cols,
        'selected_target': selected_target,
        'selected_group_col': selected_group_col,
        'selected_product': selected_group_val,
        'used_version': used_version,
        'eda': eda_result,
    }
    return render(request, 'analytics/analysis.html', context)


@login_required
def visualizations_view(request):
    """Interactive custom Visualization Studio for any uploaded dataset and column pair."""
    mapped_datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)
    selected_dataset = None
    ds_id = request.GET.get('dataset')
    if ds_id:
        selected_dataset = mapped_datasets.filter(pk=ds_id).first()
    if not selected_dataset:
        selected_dataset = mapped_datasets.first()

    chart_type = request.GET.get('chart_type', 'bar')
    if chart_type not in ('line', 'bar', 'histogram', 'scatter'):
        chart_type = 'bar'
    agg_func = request.GET.get('agg', 'sum')
    if agg_func not in ('sum', 'mean', 'count'):
        agg_func = 'sum'
    x_col = request.GET.get('x_col', '').strip()
    y_col = request.GET.get('y_col', '').strip()

    all_cols = []
    numeric_cols = []
    viz_data = None

    if selected_dataset:
        try:
            df, schema, _ = _load_active_general_df(selected_dataset, 'cleaned')
            all_cols = [str(c) for c in df.columns]
            numeric_cols = [c for c in (schema.get('numeric_columns') or []) if c in df.columns]
            if not numeric_cols:
                numeric_cols = [str(c) for c in df.select_dtypes(include='number').columns]

            if not y_col or y_col not in numeric_cols:
                y_col = schema.get('primary_target_column') or (numeric_cols[0] if numeric_cols else '')
            if not x_col or x_col not in all_cols:
                x_col = (
                    schema.get('primary_date_column')
                    or schema.get('primary_group_column')
                    or (all_cols[0] if all_cols else '')
                )
                if 'chart_type' not in request.GET:
                    chart_type = 'line' if schema.get('primary_date_column') else 'bar'

            viz_data = build_custom_visualization_data(
                df, chart_type=chart_type, x_col=x_col, y_col=y_col, agg_func=agg_func
            )
        except FileNotFoundError:
            messages.error(request, 'Dataset CSV file could not be found on disk.')
        except Exception as exc:
            messages.error(request, f'Visualization failed: {exc}')

    context = {
        'active_nav': 'visualizations',
        'page_title': 'Visualizations',
        'datasets': mapped_datasets,
        'selected_dataset': selected_dataset,
        'all_cols': all_cols,
        'numeric_cols': numeric_cols,
        'chart_type': chart_type,
        'agg_func': agg_func,
        'x_col': x_col,
        'y_col': y_col,
        'viz_data': viz_data,
    }
    return render(request, 'analytics/visualizations.html', context)


@login_required
def anomaly_detection_view(request):
    """Dataset-independent Z-score anomaly detection (time-series or cross-sectional)."""
    mapped_datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)
    selected_dataset = None
    ds_id = request.GET.get('dataset')
    if ds_id:
        selected_dataset = mapped_datasets.filter(pk=ds_id).first()
    if not selected_dataset:
        selected_dataset = mapped_datasets.first()

    product_filter = request.GET.get('product', '__ALL__').strip() or '__ALL__'
    target_col = request.GET.get('target', '').strip()

    try:
        window = min(30, max(3, int(request.GET.get('window', 7))))
    except ValueError:
        window = 7

    try:
        z_threshold = min(4.0, max(1.0, float(request.GET.get('z', 2.0))))
    except ValueError:
        z_threshold = 2.0

    anomaly_result = None
    all_groups = []
    numeric_cols = []
    has_date_col = False

    if selected_dataset:
        try:
            df, schema, _ = _load_active_general_df(selected_dataset, 'cleaned')
            numeric_cols = [c for c in (schema.get('numeric_columns') or []) if c in df.columns]
            if not numeric_cols:
                numeric_cols = [str(c) for c in df.select_dtypes(include='number').columns]

            if not target_col or target_col not in numeric_cols:
                target_col = schema.get('primary_target_column') or (numeric_cols[0] if numeric_cols else '')

            date_col = schema.get('primary_date_column') or ''
            has_date_col = bool(date_col and date_col in df.columns)
            group_col = schema.get('primary_group_column') or ''

            if group_col and group_col in df.columns:
                all_groups = sorted(df[group_col].dropna().astype(str).str.strip().unique().tolist())

            if product_filter != '__ALL__' and product_filter not in all_groups:
                product_filter = '__ALL__'

            anomaly_result = detect_anomalies(
                df,
                target_col=target_col,
                date_col=date_col,
                group_col=group_col,
                group_filter=product_filter,
                window=window,
                z_threshold=z_threshold,
            )
        except FileNotFoundError:
            messages.error(request, 'Dataset CSV file could not be found on disk.')
        except Exception as exc:
            messages.error(request, f'Anomaly detection failed: {exc}')

    context = {
        'active_nav': 'anomalies',
        'page_title': 'Anomaly Detection',
        'datasets': mapped_datasets,
        'selected_dataset': selected_dataset,
        'numeric_cols': numeric_cols,
        'all_products': all_groups,
        'product_filter': product_filter,
        'target_col': target_col,
        'has_date_col': has_date_col,
        'window': window,
        'z_threshold': z_threshold,
        'anomaly_result': anomaly_result,
    }
    return render(request, 'analytics/anomalies.html', context)
