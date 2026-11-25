"""
Views for CSV upload, flexible schema mapping, profiling, data exploration,
non-destructive cleaning, deletion, and synthetic sample downloads.
"""

from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .data_processing import (
    clean_general_dataframe,
    detect_column_schema,
    generate_sample_expenses_csv_bytes,
    generate_sample_sales_csv_bytes,
    generate_sample_traffic_csv_bytes,
    inspect_csv_headers,
    load_general_dataframe,
    profile_general_dataset,
    read_raw_csv,
)
from .forms import ColumnMappingForm, DataCleaningForm, DatasetUploadForm
from .models import SalesDataset, WorkspaceActivity


def _log_activity(user, action_type, message):
    """Record a workspace activity item for the dashboard feed."""
    WorkspaceActivity.objects.create(user=user, action_type=action_type, message=message)


@login_required
def dataset_list_view(request):
    """Display all datasets owned by the signed-in user."""
    datasets = SalesDataset.objects.filter(user=request.user)
    context = {
        'active_nav': 'datasets',
        'page_title': 'My Datasets',
        'datasets': datasets,
    }
    return render(request, 'datasets/dataset_list.html', context)


@login_required
def dataset_upload_view(request):
    """Handle CSV upload and redirect to the schema/column-mapping step."""
    if request.method == 'POST':
        form = DatasetUploadForm(request.POST, request.FILES)
        if form.is_valid():
            dataset = form.save(commit=False)
            dataset.user = request.user
            dataset.file_size_bytes = request.FILES['original_file'].size
            dataset.save()
            _log_activity(request.user, 'upload', f'Uploaded dataset "{dataset.name}".')
            messages.success(
                request,
                'CSV uploaded. Review the automatically detected column types and role mapping below.',
            )
            return redirect('datasets:map_columns', pk=dataset.pk)
    else:
        form = DatasetUploadForm()

    context = {
        'active_nav': 'datasets',
        'page_title': 'Upload CSV Dataset',
        'form': form,
    }
    return render(request, 'datasets/upload.html', context)


@login_required
def dataset_map_columns_view(request, pk):
    """Inspect detected column types, map optional/target roles, and run automated profiling."""
    dataset = get_object_or_404(SalesDataset, pk=pk, user=request.user)
    try:
        inspection = inspect_csv_headers(dataset.original_file.path)
    except FileNotFoundError:
        messages.error(request, 'The uploaded CSV file could not be found on disk.')
        return redirect('datasets:list')
    except Exception as exc:
        messages.error(request, f'Unable to parse CSV file: {exc}')
        return redirect('datasets:list')

    columns = inspection['columns']
    detected_schema = inspection['detected_schema']

    initial_mapping = {
        'dataset_type': (
            dataset.dataset_type
            if dataset.dataset_type != 'general'
            else detected_schema.get('dataset_type', 'general')
        ),
        'date_column': dataset.date_column or detected_schema.get('primary_date_column', ''),
        'product_column': dataset.product_column or detected_schema.get('primary_group_column', ''),
        'revenue_column': dataset.revenue_column or detected_schema.get('primary_target_column', ''),
        'quantity_column': dataset.quantity_column or detected_schema.get('secondary_numeric_column', ''),
    }

    if request.method == 'POST':
        form = ColumnMappingForm(columns, request.POST)
        if form.is_valid():
            dataset.dataset_type = form.cleaned_data.get('dataset_type') or 'general'
            dataset.date_column = form.cleaned_data.get('date_column') or ''
            dataset.product_column = form.cleaned_data.get('product_column') or ''
            dataset.revenue_column = form.cleaned_data.get('revenue_column') or ''
            dataset.quantity_column = form.cleaned_data.get('quantity_column') or ''

            # Persist full generalized schema metadata
            schema = dict(detected_schema)
            schema['primary_date_column'] = dataset.date_column
            schema['primary_group_column'] = dataset.product_column
            schema['primary_target_column'] = dataset.revenue_column
            schema['secondary_numeric_column'] = dataset.quantity_column
            schema['dataset_type'] = dataset.dataset_type
            dataset.column_schema = schema
            dataset.is_mapped = True

            dataset.profile_summary = profile_general_dataset(dataset.original_file.path, schema)
            dataset.save()

            _log_activity(
                request.user,
                'profile',
                f'Profiled "{dataset.name}" ({dataset.profile_summary.get("row_count", 0)} rows, '
                f'{dataset.profile_summary.get("col_count", 0)} columns).',
            )
            messages.success(request, 'Schema confirmed and automated dataset profiling completed.')
            return redirect('datasets:detail', pk=dataset.pk)
    else:
        form = ColumnMappingForm(columns, initial=initial_mapping)

    context = {
        'active_nav': 'datasets',
        'page_title': f'Configure Schema — {dataset.name}',
        'dataset': dataset,
        'form': form,
        'columns': columns,
        'column_types': detected_schema.get('column_types', {}),
        'preview_rows': inspection['preview_rows'],
        'total_raw_rows': inspection['total_raw_rows'],
    }
    return render(request, 'datasets/map_columns.html', context)


@login_required
def dataset_detail_view(request, pk):
    """Display automated profiling cards, per-column stats, quality checks, and sample rows."""
    dataset = get_object_or_404(SalesDataset, pk=pk, user=request.user)
    if not dataset.is_mapped:
        return redirect('datasets:map_columns', pk=dataset.pk)

    version = request.GET.get('version', 'cleaned' if dataset.has_cleaned_version else 'original')
    if version == 'cleaned' and dataset.has_cleaned_version and dataset.cleaned_profile_summary:
        profile = dataset.cleaned_profile_summary
        showing_cleaned = True
    else:
        profile = dataset.profile_summary or {}
        showing_cleaned = False

    context = {
        'active_nav': 'datasets',
        'page_title': dataset.name,
        'dataset': dataset,
        'profile': profile,
        'showing_cleaned': showing_cleaned,
    }
    return render(request, 'datasets/detail.html', context)


@login_required
@require_POST
def dataset_delete_view(request, pk):
    """Delete an owned dataset and remove stored CSV files from disk."""
    dataset = get_object_or_404(SalesDataset, pk=pk, user=request.user)
    dataset_name = dataset.name

    for file_field in (dataset.original_file, dataset.cleaned_file):
        if file_field:
            file_field.delete(save=False)

    dataset.delete()
    _log_activity(request.user, 'delete', f'Deleted dataset "{dataset_name}".')
    messages.success(request, f'Dataset "{dataset_name}" was deleted.')
    return redirect('datasets:list')


@login_required
def download_sample_csv_view(request):
    """Download a clearly labeled synthetic sample CSV (sales, traffic, or expenses)."""
    sample_type = request.GET.get('type', 'sales')
    if sample_type == 'traffic':
        csv_bytes = generate_sample_traffic_csv_bytes()
        filename = 'sample_website_traffic.csv'
    elif sample_type == 'expenses':
        csv_bytes = generate_sample_expenses_csv_bytes()
        filename = 'sample_business_expenses.csv'
    else:
        csv_bytes = generate_sample_sales_csv_bytes()
        filename = 'sample_retail_sales_test.csv'

    response = HttpResponse(csv_bytes, content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def data_explorer_view(request):
    """Interactive Data Explorer for inspecting columns, types, filters, and records of any CSV."""
    mapped_datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)
    selected_dataset = None
    ds_id = request.GET.get('dataset')
    if ds_id:
        selected_dataset = mapped_datasets.filter(pk=ds_id).first()
    if not selected_dataset:
        selected_dataset = mapped_datasets.first()

    search_query = request.GET.get('q', '').strip()
    selected_version = request.GET.get('version', 'cleaned')
    explorer_data = None

    if selected_dataset:
        try:
            use_cleaned = (
                selected_version == 'cleaned'
                and selected_dataset.has_cleaned_version
                and selected_dataset.cleaned_file
            )
            file_path = selected_dataset.cleaned_file.path if use_cleaned else selected_dataset.original_file.path
            raw_df = read_raw_csv(file_path)
            schema = selected_dataset.column_schema or detect_column_schema(raw_df)

            if search_query:
                mask = raw_df.astype(str).apply(
                    lambda col: col.str.contains(search_query, case=False, na=False)
                ).any(axis=1)
                filtered_df = raw_df[mask]
            else:
                filtered_df = raw_df

            profile = selected_dataset.active_profile
            col_meta = []
            for col in raw_df.columns:
                col_str = str(col)
                col_meta.append({
                    'name': col_str,
                    'type': (schema.get('column_types') or {}).get(col_str, 'unknown'),
                    'missing': int(raw_df[col].isna().sum()),
                    'unique': int(raw_df[col].nunique(dropna=True)),
                    'numeric_stats': (profile.get('numeric_columns_stats') or {}).get(col_str),
                    'cat_stats': (profile.get('categorical_columns_stats') or {}).get(col_str),
                })

            preview_rows = filtered_df.head(50).fillna('').astype(str).to_dict(orient='records')
            explorer_data = {
                'used_version': 'cleaned' if use_cleaned else 'original',
                'total_rows': int(len(raw_df)),
                'filtered_rows': int(len(filtered_df)),
                'columns': [str(c) for c in raw_df.columns],
                'col_meta': col_meta,
                'preview_rows': preview_rows,
            }
        except FileNotFoundError:
            messages.error(request, 'Dataset CSV file could not be found on disk.')
        except Exception as exc:
            messages.error(request, f'Error exploring dataset: {exc}')

    context = {
        'active_nav': 'explorer',
        'page_title': 'Data Explorer',
        'datasets': mapped_datasets,
        'selected_dataset': selected_dataset,
        'search_query': search_query,
        'selected_version': selected_version,
        'explorer': explorer_data,
    }
    return render(request, 'datasets/explorer.html', context)


@login_required
def data_cleaning_view(request):
    """
    Preview cleaning results before applying changes, and save a separate cleaned CSV
    while keeping the original uploaded CSV intact.
    """
    mapped_datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)
    selected_dataset = None
    preview_summary = None

    initial_dataset_id = request.GET.get('dataset')
    initial_data = {}
    if initial_dataset_id and mapped_datasets.filter(pk=initial_dataset_id).exists():
        initial_data['dataset'] = initial_dataset_id
    elif mapped_datasets.exists():
        initial_data['dataset'] = mapped_datasets.first().pk

    if request.method == 'POST':
        form = DataCleaningForm(request.user, request.POST)
        if form.is_valid():
            selected_dataset = form.cleaned_data['dataset']
            options = {
                'remove_duplicates': form.cleaned_data['remove_duplicates'],
                'remove_invalid_dates': form.cleaned_data['remove_invalid_dates'],
                'remove_negative_values': form.cleaned_data['remove_negative_values'],
                'missing_strategy': form.cleaned_data['missing_strategy'],
                'categorical_missing_strategy': form.cleaned_data['categorical_missing_strategy'],
                'aggregate_daily': form.cleaned_data['aggregate_daily'],
            }
            schema = selected_dataset.column_schema or {
                'primary_date_column': selected_dataset.date_column,
                'primary_group_column': selected_dataset.product_column,
                'primary_target_column': selected_dataset.revenue_column,
                'secondary_numeric_column': selected_dataset.quantity_column,
            }

            try:
                cleaned_df, summary = clean_general_dataframe(
                    selected_dataset.original_file.path, schema, options
                )
            except FileNotFoundError:
                messages.error(request, 'Original CSV file was not found on disk.')
                return redirect('datasets:list')
            except Exception as exc:
                messages.error(request, f'Data cleaning failed: {exc}')
                return redirect('datasets:cleaning')

            action = request.POST.get('action', 'preview')
            if action == 'apply':
                if selected_dataset.cleaned_file:
                    selected_dataset.cleaned_file.delete(save=False)

                csv_content = cleaned_df.to_csv(index=False).encode('utf-8')
                base_stem = Path(selected_dataset.original_file.name).stem
                cleaned_filename = f'{base_stem}_cleaned.csv'
                selected_dataset.cleaned_file.save(
                    cleaned_filename, ContentFile(csv_content), save=False
                )
                selected_dataset.has_cleaned_version = True
                selected_dataset.cleaning_log = summary['operations']
                selected_dataset.cleaned_profile_summary = profile_general_dataset(
                    selected_dataset.cleaned_file.path, schema
                )
                selected_dataset.save()
                _log_activity(
                    request.user,
                    'clean',
                    f'Cleaned "{selected_dataset.name}": {summary["rows_before"]} -> {summary["rows_after"]} rows.',
                )
                messages.success(
                    request,
                    f'Cleaned dataset saved ({summary["rows_after"]} rows). Original uploaded file remains unchanged.',
                )
                return redirect('datasets:detail', pk=selected_dataset.pk)
            else:
                preview_summary = summary
    else:
        form = DataCleaningForm(request.user, initial=initial_data)
        if 'dataset' in initial_data:
            selected_dataset = mapped_datasets.filter(pk=initial_data['dataset']).first()

    context = {
        'active_nav': 'cleaning',
        'page_title': 'Data Cleaning',
        'form': form,
        'has_datasets': mapped_datasets.exists(),
        'selected_dataset': selected_dataset,
        'preview_summary': preview_summary,
    }
    return render(request, 'datasets/cleaning.html', context)
