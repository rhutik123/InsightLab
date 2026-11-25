"""
Views for multi-format dataset upload (CSV, XLSX, XLS, JSON), worksheet/JSON path
selection, flexible schema mapping, profiling, data exploration, non-destructive
cleaning, deletion, and synthetic sample downloads.
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
    inspect_excel_sheets,
    inspect_json_candidates,
    normalize_dataset_to_internal_format,
    profile_general_dataset,
    read_excel_file,
    read_json_file,
    read_raw_csv,
    sanitize_original_filename,
)
from .forms import (
    ColumnMappingForm,
    DataCleaningForm,
    DatasetUploadForm,
    JsonPathSelectionForm,
    SheetSelectionForm,
)
from .models import SalesDataset, WorkspaceActivity


def _log_activity(user, action_type, message):
    """Record a workspace activity item for the dashboard feed."""
    WorkspaceActivity.objects.create(user=user, action_type=action_type, message=message)


def _save_normalized_dataframe(dataset, df, notes):
    """Normalize ingested DataFrame into UTF-8 CSV stored in dataset.normalized_file."""
    csv_bytes, normalized_filename = normalize_dataset_to_internal_format(
        df, dataset.original_filename or dataset.name
    )
    if dataset.normalized_file:
        dataset.normalized_file.delete(save=False)
    dataset.normalized_file.save(normalized_filename, ContentFile(csv_bytes), save=False)
    dataset.ingestion_notes = list(notes or [])
    dataset.save()


def _redirect_if_selection_pending(dataset):
    """Redirect to worksheet or JSON path selection if normalization is still pending."""
    if dataset.needs_structure_selection:
        if dataset.source_format in ('xlsx', 'xls'):
            return redirect('datasets:select_sheet', pk=dataset.pk)
        if dataset.source_format == 'json':
            return redirect('datasets:select_json', pk=dataset.pk)
    return None


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
    """Handle CSV, Excel (.xlsx/.xls), and JSON uploads and route into normalization/mapping."""
    if request.method == 'POST':
        form = DatasetUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = request.FILES['original_file']
            dataset = form.save(commit=False)
            dataset.user = request.user
            dataset.source_format = form.detected_format or 'csv'
            dataset.original_filename = sanitize_original_filename(uploaded.name)
            dataset.file_size_bytes = uploaded.size
            dataset.save()

            try:
                if dataset.source_format == 'csv':
                    _log_activity(request.user, 'upload', f'Uploaded CSV dataset "{dataset.name}".')
                    messages.success(
                        request,
                        'CSV uploaded. Review the automatically detected column types and role mapping below.',
                    )
                    return redirect('datasets:map_columns', pk=dataset.pk)

                if dataset.source_format in ('xlsx', 'xls'):
                    sheets = inspect_excel_sheets(dataset.original_file.path, source_format=dataset.source_format)
                    usable_sheets = [s for s in sheets if s['is_usable']]
                    if not usable_sheets:
                        raise ValueError('No usable tabular worksheets were found in this Excel workbook.')
                    if len(usable_sheets) > 1:
                        _log_activity(
                            request.user,
                            'upload',
                            f'Uploaded multi-sheet {dataset.source_format.upper()} workbook "{dataset.name}".',
                        )
                        messages.info(
                            request,
                            f'Workbook contains {len(usable_sheets)} usable worksheets. Select which sheet to import.',
                        )
                        return redirect('datasets:select_sheet', pk=dataset.pk)

                    only_sheet = usable_sheets[0]['name']
                    df, notes = read_excel_file(
                        dataset.original_file.path,
                        sheet_name=only_sheet,
                        source_format=dataset.source_format,
                    )
                    if len(sheets) > 1:
                        notes.append(
                            f'Skipped {len(sheets) - 1} empty or non-tabular worksheet(s) and auto-selected "{only_sheet}".'
                        )
                    dataset.selected_sheet = only_sheet
                    _save_normalized_dataframe(dataset, df, notes)
                    _log_activity(
                        request.user,
                        'upload',
                        f'Uploaded {dataset.source_format.upper()} dataset "{dataset.name}" (sheet: {only_sheet}).',
                    )
                    messages.success(
                        request,
                        f'Excel sheet "{only_sheet}" normalized safely ({len(df)} rows). Review column mapping below.',
                    )
                    return redirect('datasets:map_columns', pk=dataset.pk)

                if dataset.source_format == 'json':
                    candidates = inspect_json_candidates(dataset.original_file.path)
                    usable_cands = [c for c in candidates if c['is_usable']]
                    if not usable_cands:
                        raise ValueError('No usable JSON record array or object was found.')
                    if len(usable_cands) > 1:
                        _log_activity(
                            request.user,
                            'upload',
                            f'Uploaded multi-array JSON dataset "{dataset.name}".',
                        )
                        messages.info(
                            request,
                            f'JSON file contains {len(usable_cands)} candidate record collections. Select one to import.',
                        )
                        return redirect('datasets:select_json', pk=dataset.pk)

                    only_cand = usable_cands[0]
                    df, notes = read_json_file(dataset.original_file.path, json_path=only_cand['path'])
                    dataset.selected_json_path = only_cand['path']
                    _save_normalized_dataframe(dataset, df, notes)
                    _log_activity(
                        request.user,
                        'upload',
                        f'Uploaded JSON dataset "{dataset.name}" (path: {only_cand["path"]}).',
                    )
                    messages.success(
                        request,
                        f'JSON data normalized safely ({len(df)} rows). Review column mapping below.',
                    )
                    return redirect('datasets:map_columns', pk=dataset.pk)
            except Exception as exc:
                if dataset.normalized_file:
                    dataset.normalized_file.delete(save=False)
                if dataset.original_file:
                    dataset.original_file.delete(save=False)
                dataset.delete()
                form.add_error('original_file', f'Unable to process uploaded file: {exc}')
    else:
        form = DatasetUploadForm()

    context = {
        'active_nav': 'datasets',
        'page_title': 'Upload Dataset (CSV, Excel, JSON)',
        'form': form,
    }
    return render(request, 'datasets/upload.html', context)


@login_required
def dataset_select_sheet_view(request, pk):
    """Prompt the user to choose a worksheet from a multi-sheet Excel workbook."""
    dataset = get_object_or_404(SalesDataset, pk=pk, user=request.user)
    if dataset.source_format not in ('xlsx', 'xls'):
        return redirect('datasets:map_columns', pk=dataset.pk)

    try:
        sheets = inspect_excel_sheets(dataset.original_file.path, source_format=dataset.source_format)
    except FileNotFoundError:
        messages.error(request, 'The uploaded Excel file could not be found on disk.')
        return redirect('datasets:list')
    except Exception as exc:
        messages.error(request, f'Unable to inspect workbook sheets: {exc}')
        return redirect('datasets:list')

    usable_sheets = [s for s in sheets if s['is_usable']]
    if not usable_sheets:
        messages.error(request, 'This workbook contains no usable tabular worksheets.')
        return redirect('datasets:list')

    usable_names = {s['name'] for s in usable_sheets}

    if request.method == 'POST':
        form = SheetSelectionForm(usable_sheets, request.POST)
        if form.is_valid():
            chosen_sheet = form.cleaned_data['sheet_name']
            if chosen_sheet not in usable_names:
                messages.error(request, 'Selected worksheet is invalid or non-tabular.')
            else:
                try:
                    df, notes = read_excel_file(
                        dataset.original_file.path,
                        sheet_name=chosen_sheet,
                        source_format=dataset.source_format,
                    )
                    dataset.selected_sheet = chosen_sheet
                    dataset.is_mapped = False
                    _save_normalized_dataframe(dataset, df, notes)
                    _log_activity(
                        request.user,
                        'upload',
                        f'Selected worksheet "{chosen_sheet}" for "{dataset.name}" ({len(df)} rows).',
                    )
                    messages.success(
                        request,
                        f'Worksheet "{chosen_sheet}" imported and normalized ({len(df)} rows). Configure column mapping below.',
                    )
                    return redirect('datasets:map_columns', pk=dataset.pk)
                except Exception as exc:
                    messages.error(request, f'Unable to import worksheet "{chosen_sheet}": {exc}')
    else:
        initial_sheet = dataset.selected_sheet if dataset.selected_sheet in usable_names else usable_sheets[0]['name']
        form = SheetSelectionForm(usable_sheets, initial={'sheet_name': initial_sheet})

    context = {
        'active_nav': 'datasets',
        'page_title': f'Select Worksheet — {dataset.name}',
        'dataset': dataset,
        'form': form,
        'sheets': sheets,
        'usable_sheets': usable_sheets,
    }
    return render(request, 'datasets/select_sheet.html', context)


@login_required
def dataset_select_json_view(request, pk):
    """Prompt the user to choose which candidate JSON array/path to normalize and import."""
    dataset = get_object_or_404(SalesDataset, pk=pk, user=request.user)
    if dataset.source_format != 'json':
        return redirect('datasets:map_columns', pk=dataset.pk)

    try:
        candidates = inspect_json_candidates(dataset.original_file.path)
    except FileNotFoundError:
        messages.error(request, 'The uploaded JSON file could not be found on disk.')
        return redirect('datasets:list')
    except Exception as exc:
        messages.error(request, f'Unable to inspect JSON structures: {exc}')
        return redirect('datasets:list')

    usable_candidates = [c for c in candidates if c['is_usable']]
    if not usable_candidates:
        messages.error(request, 'This JSON file contains no usable tabular record collections.')
        return redirect('datasets:list')

    usable_paths = {c['path'] for c in usable_candidates}

    if request.method == 'POST':
        form = JsonPathSelectionForm(usable_candidates, request.POST)
        if form.is_valid():
            chosen_path = form.cleaned_data['json_path']
            if chosen_path not in usable_paths:
                messages.error(request, 'Selected JSON path is invalid or empty.')
            else:
                try:
                    df, notes = read_json_file(dataset.original_file.path, json_path=chosen_path)
                    dataset.selected_json_path = chosen_path
                    dataset.is_mapped = False
                    _save_normalized_dataframe(dataset, df, notes)
                    _log_activity(
                        request.user,
                        'upload',
                        f'Selected JSON path "{chosen_path}" for "{dataset.name}" ({len(df)} rows).',
                    )
                    messages.success(
                        request,
                        f'JSON record collection "{chosen_path}" normalized ({len(df)} rows). Configure column mapping below.',
                    )
                    return redirect('datasets:map_columns', pk=dataset.pk)
                except Exception as exc:
                    messages.error(request, f'Unable to import JSON path "{chosen_path}": {exc}')
    else:
        initial_path = (
            dataset.selected_json_path
            if dataset.selected_json_path in usable_paths
            else usable_candidates[0]['path']
        )
        form = JsonPathSelectionForm(usable_candidates, initial={'json_path': initial_path})

    context = {
        'active_nav': 'datasets',
        'page_title': f'Select JSON Record Path — {dataset.name}',
        'dataset': dataset,
        'form': form,
        'candidates': candidates,
        'usable_candidates': usable_candidates,
    }
    return render(request, 'datasets/select_json.html', context)


@login_required
def dataset_map_columns_view(request, pk):
    """Inspect detected column types, map optional/target roles, and run automated profiling."""
    dataset = get_object_or_404(SalesDataset, pk=pk, user=request.user)
    pending_redirect = _redirect_if_selection_pending(dataset)
    if pending_redirect:
        return pending_redirect

    try:
        inspection = inspect_csv_headers(dataset.source_file_path)
    except FileNotFoundError:
        messages.error(request, 'The processed dataset file could not be found on disk.')
        return redirect('datasets:list')
    except Exception as exc:
        messages.error(request, f'Unable to parse dataset file: {exc}')
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

            dataset.profile_summary = profile_general_dataset(dataset.source_file_path, schema)
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
    pending_redirect = _redirect_if_selection_pending(dataset)
    if pending_redirect:
        return pending_redirect
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
    """Delete an owned dataset and remove stored original, normalized, and cleaned files."""
    dataset = get_object_or_404(SalesDataset, pk=pk, user=request.user)
    dataset_name = dataset.name

    for file_field in (dataset.original_file, dataset.normalized_file, dataset.cleaned_file):
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
            file_path = selected_dataset.cleaned_file.path if use_cleaned else selected_dataset.source_file_path
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
                    selected_dataset.source_file_path, schema, options
                )
            except FileNotFoundError:
                messages.error(request, 'Source dataset file was not found on disk.')
                return redirect('datasets:list')
            except Exception as exc:
                messages.error(request, f'Data cleaning failed: {exc}')
                return redirect('datasets:cleaning')

            action = request.POST.get('action', 'preview')
            if action == 'apply':
                if selected_dataset.cleaned_file:
                    selected_dataset.cleaned_file.delete(save=False)

                csv_content = cleaned_df.to_csv(index=False).encode('utf-8')
                base_stem = Path(selected_dataset.original_filename or selected_dataset.original_file.name).stem
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
