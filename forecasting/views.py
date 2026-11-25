"""
Views for running general time-series forecasts, comparing models side by side,
and conducting reproducible academic research experiments.
"""

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from analytics.analysis import detect_anomalies
from analytics.decision_support import generate_business_insights
from analytics.views import _load_active_general_df
from datasets.data_processing import load_standardized_dataframe
from datasets.models import SalesDataset, WorkspaceActivity

from .forecasting_models import run_forecast_experiment
from .forms import ForecastRunForm, ResearchExperimentForm
from .models import ForecastExperiment
from .preprocessing import prepare_time_series
from .research import ALL_MODELS, run_research_experiment_suite


def _resolve_forecast_inputs(dataset, requested_version='cleaned', requested_target=''):
    """
    Load any mapped dataset cleanly for time-series forecasting.
    Validates that the dataset has a valid date column and numerical target column.
    Supports both arbitrary column names (e.g. 'Visitors', 'Amount') and legacy
    sales target aliases ('revenue', 'quantity').
    Returns (standardized_df, used_version, resolved_target_name, date_col, group_col).
    """
    df, schema, used_version = _load_active_general_df(dataset, requested_version)
    date_col = schema.get('primary_date_column') or ''
    if not date_col or date_col not in df.columns:
        raise ValueError(
            f'Dataset "{dataset.name}" does not have a mapped Date/Time column. '
            'Time-series forecasting requires a chronological date column—you can still explore and profile this dataset in Data Explorer and Data Analysis.'
        )

    numeric_cols = [c for c in (schema.get('numeric_columns') or []) if c in df.columns]
    if not numeric_cols:
        numeric_cols = [str(c) for c in df.select_dtypes(include='number').columns]
    if not numeric_cols:
        raise ValueError(
            f'Dataset "{dataset.name}" has no numerical columns available for forecasting.'
        )

    # Resolve target column
    target_col = (requested_target or '').strip()
    if target_col in numeric_cols:
        resolved_target = target_col
    elif target_col == 'quantity' and dataset.quantity_column in numeric_cols:
        resolved_target = dataset.quantity_column
    elif target_col == 'revenue' and (schema.get('primary_target_column') or dataset.revenue_column) in numeric_cols:
        resolved_target = schema.get('primary_target_column') or dataset.revenue_column
    else:
        resolved_target = schema.get('primary_target_column') or numeric_cols[0]

    group_col = schema.get('primary_group_column') or ''
    if group_col not in df.columns:
        group_col = ''

    # Build standardized 4-column DataFrame for preprocessing & models
    file_path = dataset.cleaned_file.path if used_version == 'cleaned' else dataset.source_file_path
    mapping = {
        'date': date_col,
        'product': group_col,
        'quantity': dataset.quantity_column if dataset.quantity_column in df.columns else resolved_target,
        'revenue': resolved_target,
    }
    std_df = load_standardized_dataframe(file_path, mapping)
    return std_df, used_version, resolved_target, date_col, group_col


def _get_dataset_metadata(user_datasets):
    """Build per-dataset metadata (segments, numeric columns, has_date) for form dropdowns."""
    meta = {}
    for ds in user_datasets:
        try:
            df, schema, _ = _load_active_general_df(ds, 'cleaned')
            date_col = schema.get('primary_date_column') or ''
            group_col = schema.get('primary_group_column') or ''
            numeric_cols = [c for c in (schema.get('numeric_columns') or []) if c in df.columns]
            if not numeric_cols:
                numeric_cols = [str(c) for c in df.select_dtypes(include='number').columns]
            groups = []
            if group_col and group_col in df.columns:
                groups = sorted(df[group_col].dropna().astype(str).str.strip().unique().tolist())
            meta[str(ds.pk)] = {
                'products': groups,
                'numeric_cols': numeric_cols,
                'default_target': schema.get('primary_target_column') or (numeric_cols[0] if numeric_cols else ''),
                'has_date': bool(date_col and date_col in df.columns),
            }
        except Exception:
            meta[str(ds.pk)] = {'products': [], 'numeric_cols': [], 'default_target': '', 'has_date': False}
    return meta


def _create_experiment_record(
    user,
    dataset,
    used_version,
    product_name,
    target_metric,
    freq,
    preprocessing_method,
    test_ratio,
    horizon,
    random_seed,
    exp_result,
    insights,
    experiment_type='forecast',
    experiment_group='',
    experiment_label='',
):
    """Persist a ForecastExperiment row from a run_forecast_experiment dictionary."""
    metrics = exp_result['metrics']
    return ForecastExperiment.objects.create(
        user=user,
        dataset=dataset,
        dataset_version=used_version,
        product_name=product_name,
        target_metric=target_metric,
        aggregation_freq=freq,
        model_name=exp_result['model_name'],
        preprocessing_method=preprocessing_method,
        test_size_ratio=test_ratio,
        forecast_horizon=horizon,
        random_seed=random_seed,
        train_start_date=exp_result['train_start_date'],
        train_end_date=exp_result['train_end_date'],
        test_start_date=exp_result['test_start_date'],
        test_end_date=exp_result['test_end_date'],
        train_rows=exp_result['train_rows'],
        test_rows=exp_result['test_rows'],
        mae=metrics['mae'],
        rmse=metrics['rmse'],
        mape=metrics['mape'],
        r2=metrics['r2'],
        mape_note=metrics['mape_note'],
        experiment_type=experiment_type,
        experiment_group=experiment_group,
        experiment_label=experiment_label or exp_result.get('experiment_label', ''),
        results_json=exp_result['results'],
        insights_json=insights,
    )


@login_required
def forecasting_home_view(request):
    """Run single or 5-model time-series forecasts on any dataset with a date column."""
    mapped_datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)
    saved_experiments = ForecastExperiment.objects.filter(
        user=request.user, experiment_type='forecast'
    ).select_related('dataset')[:20]

    initial_data = {}
    ds_param = request.GET.get('dataset')
    if ds_param and mapped_datasets.filter(pk=ds_param).exists():
        initial_data['dataset'] = ds_param
    elif mapped_datasets.exists():
        initial_data['dataset'] = mapped_datasets.first().pk

    latest_runs = []
    best_run = None
    chart_payload = None
    active_insights = []
    error_message = None

    if request.method == 'POST':
        form = ForecastRunForm(request.user, request.POST)
        if form.is_valid():
            dataset = form.cleaned_data['dataset']
            req_version = form.cleaned_data['dataset_version']
            product_name = (form.cleaned_data['product_name'] or '__ALL__').strip() or '__ALL__'
            req_target = form.cleaned_data['target_metric']
            freq = form.cleaned_data['aggregation_freq']
            selected_model = form.cleaned_data['model_name']
            missing_method = form.cleaned_data['preprocessing_method']
            test_ratio = form.cleaned_data['test_size_ratio']
            horizon = form.cleaned_data['forecast_horizon']
            seed = form.cleaned_data['random_seed']

            try:
                std_df, used_version, resolved_target, _, _ = _resolve_forecast_inputs(
                    dataset, req_version, req_target
                )
                series, prep_info = prepare_time_series(
                    std_df,
                    product_name=product_name,
                    target_col='revenue',
                    freq=freq,
                    missing_method=missing_method,
                )
                models_to_run = ALL_MODELS if selected_model == 'compare_all' else [selected_model]
                group_id = uuid.uuid4().hex[:12]

                anom_summary = detect_anomalies(
                    std_df, target_col='revenue', date_col='date', group_col='product', group_filter=product_name
                )
                scope_label = 'All Records' if product_name == '__ALL__' else product_name

                for m in models_to_run:
                    exp_res = run_forecast_experiment(
                        series,
                        m,
                        freq=freq,
                        test_ratio=test_ratio,
                        forecast_horizon=horizon,
                        random_seed=seed,
                    )
                    insights = generate_business_insights(
                        series,
                        exp_res['results']['future_predictions'],
                        target_col=resolved_target,
                        product_label=scope_label,
                        anomalies=anom_summary.get('anomalies', []),
                    )
                    exp_obj = _create_experiment_record(
                        user=request.user,
                        dataset=dataset,
                        used_version=used_version,
                        product_name=product_name,
                        target_metric=resolved_target,
                        freq=freq,
                        preprocessing_method=missing_method,
                        test_ratio=test_ratio,
                        horizon=horizon,
                        random_seed=seed,
                        exp_result=exp_res,
                        insights=insights,
                        experiment_type='forecast',
                        experiment_group=group_id,
                    )
                    latest_runs.append(exp_obj)

                valid_runs = [r for r in latest_runs if r.rmse is not None]
                best_run = min(valid_runs, key=lambda r: r.rmse) if valid_runs else latest_runs[0]
                active_insights = best_run.insights_json

                best_res = best_run.results_json
                chart_payload = {
                    'history_dates': best_res['train_dates'] + best_res['test_dates'],
                    'history_actuals': best_res['train_values'] + best_res['test_actuals'],
                    'test_dates': best_res['test_dates'],
                    'test_predictions': best_res['test_predictions'],
                    'future_dates': best_res['future_dates'],
                    'future_predictions': best_res['future_predictions'],
                    'best_model_label': best_run.get_model_name_display(),
                    'target_label': resolved_target,
                    'comparison_labels': [r.get_model_name_display() for r in latest_runs],
                    'comparison_rmse': [r.rmse for r in latest_runs],
                    'comparison_mae': [r.mae for r in latest_runs],
                    'missing_dates_filled': prep_info['missing_dates_filled'],
                }

                WorkspaceActivity.objects.create(
                    user=request.user,
                    action_type='forecast',
                    message=(
                        f'Ran {len(latest_runs)} forecast model(s) on "{dataset.name}" ({resolved_target}, {scope_label}).'
                    ),
                )
                messages.success(
                    request,
                    f'Forecast completed ({len(latest_runs)} model(s) evaluated on chronological test data).',
                )
            except ValueError as exc:
                error_message = str(exc)
            except FileNotFoundError:
                error_message = 'Dataset CSV file could not be found on disk.'
            except Exception as exc:
                error_message = f'Forecasting failed: {exc}'
    else:
        form = ForecastRunForm(request.user, initial=initial_data)

    context = {
        'active_nav': 'forecasting',
        'page_title': 'Forecasting',
        'form': form,
        'has_datasets': mapped_datasets.exists(),
        'dataset_meta': _get_dataset_metadata(mapped_datasets),
        'latest_runs': latest_runs,
        'best_run': best_run,
        'chart_payload': chart_payload,
        'active_insights': active_insights,
        'saved_experiments': saved_experiments,
        'error_message': error_message,
    }
    return render(request, 'forecasting/forecasting.html', context)


@login_required
def model_comparison_view(request):
    """Dedicated Model Comparison view comparing saved forecasting runs across models."""
    mapped_datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)
    selected_dataset = None
    ds_id = request.GET.get('dataset')
    if ds_id:
        selected_dataset = mapped_datasets.filter(pk=ds_id).first()

    runs_qs = ForecastExperiment.objects.filter(user=request.user).select_related('dataset')
    if selected_dataset:
        runs_qs = runs_qs.filter(dataset=selected_dataset)

    runs = list(runs_qs[:40])
    valid_runs = [r for r in runs if r.rmse is not None]
    best_overall = min(valid_runs, key=lambda r: r.rmse) if valid_runs else None

    chart_data = None
    if valid_runs:
        chart_runs = valid_runs[:12]
        chart_data = {
            'labels': [
                f'{r.get_model_name_display()} ({r.target_metric})' for r in chart_runs
            ],
            'rmse': [r.rmse for r in chart_runs],
            'mae': [r.mae for r in chart_runs],
        }

    context = {
        'active_nav': 'compare',
        'page_title': 'Model Comparison',
        'datasets': mapped_datasets,
        'selected_dataset': selected_dataset,
        'runs': runs,
        'best_overall': best_overall,
        'chart_data': chart_data,
    }
    return render(request, 'forecasting/compare.html', context)


@login_required
def experiment_detail_view(request, pk):
    """Display full details of a saved ForecastExperiment."""
    exp = get_object_or_404(ForecastExperiment, pk=pk, user=request.user)
    res = exp.results_json or {}

    chart_payload = {
        'history_dates': res.get('train_dates', []) + res.get('test_dates', []),
        'history_actuals': res.get('train_values', []) + res.get('test_actuals', []),
        'test_dates': res.get('test_dates', []),
        'test_predictions': res.get('test_predictions', []),
        'future_dates': res.get('future_dates', []),
        'future_predictions': res.get('future_predictions', []),
        'best_model_label': exp.get_model_name_display(),
        'target_label': exp.target_metric,
    }

    future_rows = list(zip(res.get('future_dates', []), res.get('future_predictions', [])))
    test_rows = list(
        zip(
            res.get('test_dates', []),
            res.get('test_actuals', []),
            res.get('test_predictions', []),
        )
    )

    context = {
        'active_nav': 'forecasting',
        'page_title': f'Forecast Result — {exp.get_model_name_display()}',
        'exp': exp,
        'chart_payload': chart_payload,
        'future_rows': future_rows,
        'test_rows': test_rows,
    }
    return render(request, 'forecasting/forecast_detail.html', context)


@login_required
@require_POST
def experiment_delete_view(request, pk):
    """Delete an owned forecast experiment."""
    exp = get_object_or_404(ForecastExperiment, pk=pk, user=request.user)
    exp.delete()
    messages.success(request, 'Experiment deleted.')
    return redirect('forecasting:home')


@login_required
def experiment_lab_view(request):
    """
    Controlled Research Experiment Lab (MSc IT Academic Research Suite).
    Supports all 6 controlled forecasting protocols on sales and general time-series datasets.
    """
    mapped_datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)

    initial_data = {}
    ds_param = request.GET.get('dataset')
    if ds_param and mapped_datasets.filter(pk=ds_param).exists():
        initial_data['dataset'] = ds_param
    elif mapped_datasets.exists():
        initial_data['dataset'] = mapped_datasets.first().pk

    active_batch = []
    batch_note = None
    chart_payload = None
    error_message = None

    if request.method == 'POST':
        form = ResearchExperimentForm(request.user, request.POST)
        if form.is_valid():
            dataset = form.cleaned_data['dataset']
            exp_type = form.cleaned_data['experiment_type']
            model_name = form.cleaned_data['model_name']
            product_name = (form.cleaned_data['product_name'] or '__ALL__').strip() or '__ALL__'
            req_target = form.cleaned_data['target_metric']
            freq = form.cleaned_data['aggregation_freq']
            missing_method = form.cleaned_data['preprocessing_method']
            horizon = form.cleaned_data['forecast_horizon']
            seed = form.cleaned_data['random_seed']

            try:
                std_df, used_version, resolved_target, _, _ = _resolve_forecast_inputs(
                    dataset, 'cleaned', req_target
                )
                suite_records, batch_note = run_research_experiment_suite(
                    df=std_df,
                    experiment_type=exp_type,
                    product_name=product_name,
                    model_name=model_name,
                    target_col='revenue',
                    freq=freq,
                    missing_method=missing_method,
                    horizon=horizon,
                    random_seed=seed,
                )
                group_id = uuid.uuid4().hex[:12]

                for rec in suite_records:
                    prod_used = rec.get('product_override', product_name)
                    exp_obj = _create_experiment_record(
                        user=request.user,
                        dataset=dataset,
                        used_version=used_version,
                        product_name=prod_used,
                        target_metric=resolved_target,
                        freq=freq,
                        preprocessing_method=missing_method,
                        test_ratio=0.2,
                        horizon=horizon,
                        random_seed=seed,
                        exp_result=rec,
                        insights=[],
                        experiment_type=exp_type,
                        experiment_group=group_id,
                        experiment_label=rec['experiment_label'],
                    )
                    active_batch.append(exp_obj)

                chart_payload = {
                    'labels': [e.experiment_label or e.get_model_name_display() for e in active_batch],
                    'mae': [e.mae for e in active_batch],
                    'rmse': [e.rmse for e in active_batch],
                    'mape': [e.mape for e in active_batch],
                }

                WorkspaceActivity.objects.create(
                    user=request.user,
                    action_type='experiment',
                    message=f'Conducted research experiment ({exp_type}) on "{dataset.name}".',
                )
                messages.success(
                    request,
                    f'Research experiment completed ({len(active_batch)} reproducible configurations saved).',
                )
            except ValueError as exc:
                error_message = str(exc)
            except FileNotFoundError:
                error_message = 'Dataset CSV file could not be found on disk.'
            except Exception as exc:
                error_message = f'Experiment failed: {exc}'
    else:
        form = ResearchExperimentForm(request.user, initial=initial_data)
        group_param = request.GET.get('group')
        if group_param:
            active_batch = list(
                ForecastExperiment.objects.filter(
                    user=request.user, experiment_group=group_param
                ).order_by('id')
            )
            if active_batch:
                chart_payload = {
                    'labels': [e.experiment_label or e.get_model_name_display() for e in active_batch],
                    'mae': [e.mae for e in active_batch],
                    'rmse': [e.rmse for e in active_batch],
                    'mape': [e.mape for e in active_batch],
                }
                batch_note = f'Loaded saved experiment batch "{group_param}".'

    past_research = (
        ForecastExperiment.objects.filter(user=request.user)
        .exclude(experiment_type='forecast')
        .select_related('dataset')[:30]
    )

    context = {
        'active_nav': 'experiments',
        'page_title': 'Experiment Lab',
        'form': form,
        'has_datasets': mapped_datasets.exists(),
        'dataset_meta': _get_dataset_metadata(mapped_datasets),
        'active_batch': active_batch,
        'batch_note': batch_note,
        'chart_payload': chart_payload,
        'past_research': past_research,
        'error_message': error_message,
    }
    return render(request, 'forecasting/experiments.html', context)
