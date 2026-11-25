"""
Dataset validation, automatic schema detection, column mapping, automated profiling,
non-destructive cleaning, and synthetic sample dataset generators.
Designed to support general structured CSV datasets (sales, traffic, expenses,
inventory, tabular metrics) while keeping full compatibility with sales research.
"""

import csv
import io
import math
import random
from datetime import date, timedelta

import numpy as np
import pandas as pd

MAX_CSV_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB


def validate_csv_upload(uploaded_file, max_size_bytes=MAX_CSV_SIZE_BYTES):
    """
    Validate an uploaded Django file object as a non-empty, readable CSV.
    Accepts any structured CSV with at least 2 columns and 1 data row.
    Returns (is_valid: bool, error_message: str | None).
    """
    if not uploaded_file:
        return False, 'Please choose a CSV file to upload.'

    filename = (uploaded_file.name or '').lower()
    if not filename.endswith('.csv'):
        return False, 'Only CSV files (.csv) are supported.'

    if uploaded_file.size == 0:
        return False, 'The uploaded CSV file is empty.'

    if uploaded_file.size > max_size_bytes:
        max_mb = max_size_bytes // (1024 * 1024)
        return False, f'File size exceeds the {max_mb} MB limit.'

    try:
        uploaded_file.seek(0)
        sample_bytes = uploaded_file.read(65536)
        uploaded_file.seek(0)
        if b'\x00' in sample_bytes:
            return False, 'Binary or NUL-byte content is not a valid CSV file.'
        text_sample = sample_bytes.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError:
        try:
            uploaded_file.seek(0)
            text_sample = uploaded_file.read(65536).decode('latin-1')
            uploaded_file.seek(0)
        except Exception:
            return False, 'Unable to decode file as text CSV.'

    if not text_sample.strip():
        return False, 'The uploaded CSV file contains no data.'

    try:
        df_preview = pd.read_csv(io.StringIO(text_sample), nrows=5)
    except Exception as exc:
        return False, f'Invalid CSV format: {exc}'

    if len(df_preview.columns) < 2:
        return False, 'A structured CSV dataset must have at least 2 columns.'

    if df_preview.empty:
        return False, 'The CSV file contains headers but no data rows.'

    return True, None


def read_raw_csv(file_path):
    """Read a CSV file safely using UTF-8-SIG with Latin-1 fallback."""
    try:
        return pd.read_csv(file_path, encoding='utf-8-sig')
    except UnicodeDecodeError:
        return pd.read_csv(file_path, encoding='latin-1')


def detect_column_schema(df):
    """
    Inspect every column in a DataFrame and classify it as:
    'datetime', 'numeric', 'categorical', 'text', or 'identifier'.
    Also suggests primary_date_column, primary_target_column,
    primary_group_column, secondary_numeric_column, and dataset_type.
    """
    column_types = {}
    date_columns = []
    numeric_columns = []
    categorical_columns = []
    text_columns = []
    identifier_columns = []

    n_rows = max(1, len(df))

    for col in df.columns:
        col_name = str(col)
        lower_name = col_name.strip().lower()
        series = df[col]
        non_null = series.dropna()

        if non_null.empty:
            column_types[col_name] = 'text'
            text_columns.append(col_name)
            continue

        # Check identifier naming first when mostly unique
        unique_ratio = non_null.nunique() / max(1, len(non_null))
        is_id_name = lower_name == 'id' or lower_name.endswith('_id') or lower_name.endswith(' id')

        # Attempt numeric parsing
        numeric_parsed = pd.to_numeric(non_null, errors='coerce')
        numeric_ratio = numeric_parsed.notna().mean()

        if numeric_ratio >= 0.8:
            if is_id_name and unique_ratio >= 0.85:
                column_types[col_name] = 'identifier'
                identifier_columns.append(col_name)
            else:
                column_types[col_name] = 'numeric'
                numeric_columns.append(col_name)
            continue

        # Attempt datetime parsing on string/object values
        str_vals = non_null.astype(str).str.strip()
        if is_id_name and unique_ratio >= 0.9:
            column_types[col_name] = 'identifier'
            identifier_columns.append(col_name)
            continue

        try:
            date_parsed = pd.to_datetime(str_vals, errors='coerce', format='mixed')
        except TypeError:
            date_parsed = pd.to_datetime(str_vals, errors='coerce')

        date_ratio = date_parsed.notna().mean()
        has_date_hint = any(
            kw in lower_name for kw in ('date', 'time', 'day', 'month', 'year', 'timestamp')
        )
        if date_ratio >= 0.75 or (has_date_hint and date_ratio >= 0.4):
            column_types[col_name] = 'datetime'
            date_columns.append(col_name)
            continue

        # Categorical vs free text
        nunique = int(str_vals.nunique())
        if nunique <= 50 and (nunique / max(1, len(str_vals)) <= 0.5 or nunique <= 15):
            column_types[col_name] = 'categorical'
            categorical_columns.append(col_name)
        elif is_id_name or unique_ratio >= 0.95 and n_rows >= 10:
            column_types[col_name] = 'identifier'
            identifier_columns.append(col_name)
        else:
            column_types[col_name] = 'text'
            text_columns.append(col_name)

    # Smart defaults for primary roles
    suggestions = suggest_column_mapping([str(c) for c in df.columns])

    primary_date = suggestions['date'] if suggestions['date'] in date_columns else (
        date_columns[0] if date_columns else ''
    )

    # Prefer revenue/sales/visitors/amount/stock style target column if present
    target_priority = [
        'revenue', 'sales_revenue', 'sales', 'amount', 'visitors', 'page_views',
        'stock_level', 'total', 'value', 'price', 'metric',
    ]
    primary_target = ''
    for kw in target_priority:
        for nc in numeric_columns:
            if kw in nc.lower():
                primary_target = nc
                break
        if primary_target:
            break
    if not primary_target and suggestions['revenue'] in numeric_columns:
        primary_target = suggestions['revenue']
    if not primary_target and numeric_columns:
        primary_target = numeric_columns[0]

    remaining_numeric = [c for c in numeric_columns if c != primary_target]
    secondary_numeric = (
        suggestions['quantity']
        if suggestions['quantity'] in remaining_numeric
        else (remaining_numeric[0] if remaining_numeric else '')
    )

    primary_group = (
        suggestions['product']
        if suggestions['product'] in categorical_columns
        else (categorical_columns[0] if categorical_columns else '')
    )

    # Infer dataset domain hint
    joined_cols = ' '.join(str(c).lower() for c in df.columns)
    if any(k in joined_cols for k in ('revenue', 'sales', 'units_sold', 'order')):
        dataset_type = 'sales'
    elif any(k in joined_cols for k in ('visitor', 'page_view', 'traffic', 'session', 'bounce')):
        dataset_type = 'traffic'
    elif any(k in joined_cols for k in ('expense', 'payment', 'cost', 'budget')):
        dataset_type = 'finance'
    elif any(k in joined_cols for k in ('stock', 'inventory', 'warehouse', 'consumed')):
        dataset_type = 'inventory'
    else:
        dataset_type = 'general'

    return {
        'column_types': column_types,
        'date_columns': date_columns,
        'numeric_columns': numeric_columns,
        'categorical_columns': categorical_columns,
        'text_columns': text_columns,
        'identifier_columns': identifier_columns,
        'primary_date_column': primary_date,
        'primary_target_column': primary_target,
        'primary_group_column': primary_group,
        'secondary_numeric_column': secondary_numeric,
        'dataset_type': dataset_type,
    }


def suggest_column_mapping(columns):
    """
    Suggest standard field mappings ('date', 'product', 'quantity', 'revenue')
    from raw CSV header names using case-insensitive keyword matching.
    """
    lower_map = {col: col.strip().lower() for col in columns}
    suggestions = {'date': '', 'product': '', 'quantity': '', 'revenue': ''}

    keyword_rules = {
        'date': ['date', 'order_date', 'sale_date', 'transaction_date', 'timestamp', 'day'],
        'product': ['product', 'item', 'sku', 'product_name', 'category', 'traffic_source', 'source', 'name'],
        'quantity': ['quantity', 'qty', 'units', 'units_sold', 'volume', 'count', 'visitors', 'units_consumed'],
        'revenue': ['revenue', 'sales', 'amount', 'total', 'sales_amount', 'page_views', 'stock_level', 'value'],
    }

    used_cols = set()
    for target_field, keywords in keyword_rules.items():
        for col, lower_col in lower_map.items():
            if col in used_cols:
                continue
            if lower_col in keywords:
                suggestions[target_field] = col
                used_cols.add(col)
                break
        if suggestions[target_field]:
            continue
        for col, lower_col in lower_map.items():
            if col in used_cols:
                continue
            if any(kw in lower_col for kw in keywords):
                suggestions[target_field] = col
                used_cols.add(col)
                break

    return suggestions


def inspect_csv_headers(file_path, preview_rows=5):
    """Return column names, detected schema, suggested mappings, and sample preview rows."""
    df = read_raw_csv(file_path)
    columns = [str(c) for c in df.columns]
    suggested = suggest_column_mapping(columns)
    detected_schema = detect_column_schema(df)
    preview_df = df.head(preview_rows).fillna('')
    rows = preview_df.astype(str).to_dict(orient='records')
    return {
        'columns': columns,
        'suggested': suggested,
        'detected_schema': detected_schema,
        'preview_rows': rows,
        'total_raw_rows': int(len(df)),
    }


def _safe_float(value):
    """Convert numpy/pandas scalar to finite float or None."""
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return round(result, 4)
    except (TypeError, ValueError):
        return None


def _numeric_stats(series):
    """Compute descriptive statistics and IQR outlier count for a numeric Series."""
    clean = pd.to_numeric(series, errors='coerce').dropna()
    if clean.empty:
        return {
            'count': 0,
            'min': None,
            'max': None,
            'mean': None,
            'median': None,
            'std': None,
            'sum': 0.0,
            'negative_count': 0,
            'iqr_outlier_count': 0,
        }

    iqr_outliers = 0
    if len(clean) >= 8:
        q1 = clean.quantile(0.25)
        q3 = clean.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            iqr_outliers = int(((clean > q3 + 3.0 * iqr) | (clean < q1 - 3.0 * iqr)).sum())

    return {
        'count': int(clean.shape[0]),
        'min': _safe_float(clean.min()),
        'max': _safe_float(clean.max()),
        'mean': _safe_float(clean.mean()),
        'median': _safe_float(clean.median()),
        'std': _safe_float(clean.std(ddof=1)) if len(clean) > 1 else 0.0,
        'sum': round(float(clean.sum()), 2),
        'negative_count': int((clean < 0).sum()),
        'iqr_outlier_count': iqr_outliers,
    }


def profile_general_dataset(file_path, schema):
    """
    Analyze any structured CSV file using its detected/mapped column schema and return
    a comprehensive, JSON-serializable profiling dictionary.
    """
    df = read_raw_csv(file_path)
    row_count = int(len(df))
    col_count = int(len(df.columns))

    detected = detect_column_schema(df)
    column_types = schema.get('column_types') or detected['column_types']
    numeric_cols = schema.get('numeric_columns') or detected['numeric_columns']
    categorical_cols = schema.get('categorical_columns') or detected['categorical_columns']

    date_col = schema.get('primary_date_column') or ''
    group_col = schema.get('primary_group_column') or ''
    target_col = schema.get('primary_target_column') or (numeric_cols[0] if numeric_cols else '')
    secondary_col = schema.get('secondary_numeric_column') or ''

    missing_by_column = {str(col): int(df[col].isna().sum()) for col in df.columns}
    total_missing = int(sum(missing_by_column.values()))
    duplicate_rows = int(df.duplicated().sum())

    # Optional date profiling
    date_min = None
    date_max = None
    invalid_dates_count = 0
    if date_col and date_col in df.columns:
        parsed_dates = pd.to_datetime(df[date_col], errors='coerce')
        valid_dates = parsed_dates.dropna()
        invalid_dates_count = int(parsed_dates.isna().sum())
        date_min = valid_dates.min().strftime('%Y-%m-%d') if not valid_dates.empty else None
        date_max = valid_dates.max().strftime('%Y-%m-%d') if not valid_dates.empty else None

    # All numeric columns stats
    numeric_columns_stats = {}
    total_negative_values = 0
    total_outliers = 0
    for col in numeric_cols:
        if col in df.columns:
            stats = _numeric_stats(df[col])
            numeric_columns_stats[col] = stats
            total_negative_values += stats['negative_count']
            total_outliers += stats['iqr_outlier_count']

    # Categorical columns stats
    categorical_columns_stats = {}
    for col in categorical_cols:
        if col in df.columns:
            vals = df[col].dropna().astype(str).str.strip()
            vals = vals[vals != '']
            top_counts = vals.value_counts().head(5)
            categorical_columns_stats[col] = {
                'unique_count': int(vals.nunique()),
                'top_values': [[str(k), int(v)] for k, v in top_counts.items()],
            }

    unique_groups = 0
    if group_col and group_col in df.columns:
        groups = df[group_col].dropna().astype(str).str.strip()
        groups = groups[groups != '']
        unique_groups = int(groups.nunique())

    # Target and secondary stats (compatible with existing sales views/tests)
    target_stats = (
        numeric_columns_stats.get(target_col)
        or (_numeric_stats(df[target_col]) if target_col and target_col in df.columns else _numeric_stats(pd.Series(dtype=float)))
    )
    secondary_stats = (
        numeric_columns_stats.get(secondary_col)
        or (_numeric_stats(df[secondary_col]) if secondary_col and secondary_col in df.columns else _numeric_stats(pd.Series(dtype=float)))
    )

    sample_df = df.head(10).fillna('')
    sample_rows = sample_df.astype(str).to_dict(orient='records')

    return {
        'row_count': row_count,
        'col_count': col_count,
        'columns': [str(c) for c in df.columns],
        'column_types': column_types,
        'numeric_col_count': len(numeric_cols),
        'categorical_col_count': len(categorical_cols),
        'date_col_count': len(schema.get('date_columns') or detected['date_columns']),
        'primary_date_column': date_col,
        'primary_group_column': group_col,
        'primary_target_column': target_col,
        'secondary_numeric_column': secondary_col,
        'date_min': date_min,
        'date_max': date_max,
        'invalid_dates_count': invalid_dates_count,
        'missing_by_column': missing_by_column,
        'total_missing': total_missing,
        'duplicate_rows': duplicate_rows,
        'numeric_columns_stats': numeric_columns_stats,
        'categorical_columns_stats': categorical_columns_stats,
        'total_negative_values': total_negative_values,
        'total_outliers': total_outliers,
        # Backward-compatible keys used in existing sales profile/tests
        'unique_products': unique_groups,
        'negative_qty_count': secondary_stats['negative_count'] if secondary_col else total_negative_values,
        'invalid_qty_count': int(pd.to_numeric(df[secondary_col], errors='coerce').isna().sum()) if secondary_col and secondary_col in df.columns else 0,
        'negative_rev_count': target_stats['negative_count'],
        'invalid_rev_count': int(pd.to_numeric(df[target_col], errors='coerce').isna().sum()) if target_col and target_col in df.columns else 0,
        'suspicious_high_count': target_stats['iqr_outlier_count'],
        'total_quantity': secondary_stats['sum'] if secondary_col else 0.0,
        'total_revenue': target_stats['sum'],
        'avg_quantity': secondary_stats['mean'] or 0.0,
        'avg_revenue': target_stats['mean'] or 0.0,
        'quantity_stats': secondary_stats,
        'revenue_stats': target_stats,
        'sample_rows': sample_rows,
    }


def profile_dataset(file_path, mapping):
    """
    Backward-compatible profiling wrapper accepting a 4-key sales mapping dict
    {'date', 'product', 'quantity', 'revenue'}.
    """
    schema = {
        'primary_date_column': mapping.get('date', ''),
        'primary_group_column': mapping.get('product', ''),
        'secondary_numeric_column': mapping.get('quantity', ''),
        'primary_target_column': mapping.get('revenue', '') or mapping.get('target', ''),
    }
    return profile_general_dataset(file_path, schema)


def load_general_dataframe(file_path, schema):
    """
    Load any CSV using its column schema, converting datetime and numeric columns
    into clean pandas types while preserving all columns.
    """
    df = read_raw_csv(file_path)
    detected = detect_column_schema(df)
    date_cols = set(schema.get('date_columns') or detected['date_columns'])
    primary_date = schema.get('primary_date_column') or ''
    if primary_date:
        date_cols.add(primary_date)

    numeric_cols = set(schema.get('numeric_columns') or detected['numeric_columns'])
    for col in (schema.get('primary_target_column'), schema.get('secondary_numeric_column')):
        if col:
            numeric_cols.add(col)

    work_df = df.copy()
    for col in work_df.columns:
        col_name = str(col)
        if col_name in date_cols:
            work_df[col_name] = pd.to_datetime(work_df[col_name], errors='coerce')
        elif col_name in numeric_cols:
            work_df[col_name] = pd.to_numeric(work_df[col_name], errors='coerce')
        elif work_df[col_name].dtype == object:
            work_df[col_name] = work_df[col_name].where(work_df[col_name].isna(), work_df[col_name].astype(str).str.strip())

    if primary_date and primary_date in work_df.columns:
        work_df = work_df.dropna(subset=[primary_date]).sort_values(primary_date).reset_index(drop=True)

    return work_df


def load_standardized_dataframe(file_path, mapping):
    """
    Load a mapped CSV into a standardized 4-column DataFrame:
    ['date', 'product', 'quantity', 'revenue'] for time-series & sales workflows.
    Works flexibly even if group ('product') or secondary ('quantity') is omitted.
    """
    df = read_raw_csv(file_path)
    date_col = mapping.get('date', '')
    prod_col = mapping.get('product', '') or mapping.get('group', '')
    qty_col = mapping.get('quantity', '')
    rev_col = mapping.get('revenue', '') or mapping.get('target', '')

    data = {}
    if date_col and date_col in df.columns:
        data['date'] = pd.to_datetime(df[date_col], errors='coerce')
    else:
        data['date'] = pd.NaT

    if prod_col and prod_col in df.columns:
        data['product'] = df[prod_col].fillna('All').astype(str).str.strip().replace('', 'All')
    else:
        data['product'] = 'All Records'

    if rev_col and rev_col in df.columns:
        data['revenue'] = pd.to_numeric(df[rev_col], errors='coerce')
    else:
        data['revenue'] = 0.0

    if qty_col and qty_col in df.columns:
        data['quantity'] = pd.to_numeric(df[qty_col], errors='coerce')
    else:
        data['quantity'] = data['revenue']

    standardized = pd.DataFrame(data)
    if date_col and date_col in df.columns:
        standardized = standardized.dropna(subset=['date']).sort_values('date').reset_index(drop=True)
    return standardized


def clean_general_dataframe(file_path, schema, options):
    """
    Non-destructive, dataset-independent cleaning pipeline supporting any structured CSV.
    Preserves all dataset columns while handling duplicates, whitespace, invalid dates,
    negative numeric values, missing numeric/categorical values, and optional date+group aggregation.
    Returns (cleaned_df: pd.DataFrame, summary: dict).
    """
    raw_df = read_raw_csv(file_path)
    rows_before = int(len(raw_df))
    work_df = raw_df.copy()
    operations = []

    detected = detect_column_schema(raw_df)
    date_col = schema.get('primary_date_column') or ''
    group_col = schema.get('primary_group_column') or ''
    numeric_cols = [c for c in (schema.get('numeric_columns') or detected['numeric_columns']) if c in work_df.columns]
    categorical_cols = [c for c in (schema.get('categorical_columns') or detected['categorical_columns']) if c in work_df.columns]

    # Trim whitespace in string columns
    for col in work_df.select_dtypes(include=['object']).columns:
        work_df[col] = work_df[col].where(work_df[col].isna(), work_df[col].astype(str).str.strip())

    # 1. Remove duplicates
    duplicates_removed = 0
    if options.get('remove_duplicates', True):
        before = len(work_df)
        work_df = work_df.drop_duplicates()
        duplicates_removed = int(before - len(work_df))
        operations.append(f'Removed {duplicates_removed} duplicate record(s).')

    # 2. Parse & filter invalid dates if a primary date column is mapped
    invalid_dates_removed = 0
    if date_col and date_col in work_df.columns:
        work_df[date_col] = pd.to_datetime(work_df[date_col], errors='coerce')
        if options.get('remove_invalid_dates', True):
            before = len(work_df)
            work_df = work_df.dropna(subset=[date_col])
            invalid_dates_removed = int(before - len(work_df))
            operations.append(f'Removed {invalid_dates_removed} row(s) with invalid "{date_col}" values.')

    # 3. Convert numeric columns and optionally remove negative values
    for col in numeric_cols:
        work_df[col] = pd.to_numeric(work_df[col], errors='coerce')

    negative_removed = 0
    if options.get('remove_negative_values', False) and numeric_cols:
        before = len(work_df)
        mask = pd.Series(True, index=work_df.index)
        for col in numeric_cols:
            mask &= work_df[col].isna() | (work_df[col] >= 0)
        work_df = work_df[mask]
        negative_removed = int(before - len(work_df))
        operations.append(f'Removed {negative_removed} row(s) containing negative numerical values.')

    # 4. Handle missing numerical values
    missing_strategy = options.get('missing_strategy', 'drop')
    missing_numeric_count = int(sum(work_df[c].isna().sum() for c in numeric_cols)) if numeric_cols else 0
    if numeric_cols:
        if missing_strategy == 'drop':
            before = len(work_df)
            work_df = work_df.dropna(subset=numeric_cols)
            dropped = int(before - len(work_df))
            operations.append(f'Dropped {dropped} row(s) with missing numerical values.')
        elif missing_strategy == 'median':
            for col in numeric_cols:
                med = work_df[col].median()
                work_df[col] = work_df[col].fillna(0.0 if pd.isna(med) else med)
            operations.append(f'Imputed {missing_numeric_count} missing numerical value(s) using column medians.')
        elif missing_strategy == 'mean':
            for col in numeric_cols:
                avg = work_df[col].mean()
                work_df[col] = work_df[col].fillna(0.0 if pd.isna(avg) else avg)
            operations.append(f'Imputed {missing_numeric_count} missing numerical value(s) using column means.')
        elif missing_strategy == 'zero':
            for col in numeric_cols:
                work_df[col] = work_df[col].fillna(0.0)
            operations.append(f'Filled {missing_numeric_count} missing numerical value(s) with 0.')

    # 5. Handle missing categorical values
    cat_strategy = options.get('categorical_missing_strategy', 'fill_unknown')
    if categorical_cols:
        for col in categorical_cols:
            work_df[col] = work_df[col].replace('', np.nan)
        cat_missing = int(sum(work_df[c].isna().sum() for c in categorical_cols))
        if cat_missing > 0:
            if cat_strategy == 'drop':
                before = len(work_df)
                work_df = work_df.dropna(subset=categorical_cols)
                operations.append(f'Dropped {int(before - len(work_df))} row(s) with blank categorical values.')
            elif cat_strategy == 'fill_unknown':
                for col in categorical_cols:
                    work_df[col] = work_df[col].fillna('Unknown')
                operations.append(f'Replaced {cat_missing} missing categorical value(s) with "Unknown".')

    # 6. Flag statistical outliers (informational)
    suspicious_count = 0
    if len(work_df) >= 8 and numeric_cols:
        primary_target = schema.get('primary_target_column') or numeric_cols[0]
        if primary_target in work_df.columns:
            col_vals = work_df[primary_target].dropna()
            if len(col_vals) >= 8:
                q1, q3 = col_vals.quantile(0.25), col_vals.quantile(0.75)
                iqr = q3 - q1
                if iqr > 0:
                    suspicious_count = int((col_vals > q3 + 3.0 * iqr).sum())

    # 7. Optional aggregation by Date + Group (when both exist)
    aggregated_rows_merged = 0
    if (
        options.get('aggregate_daily', False)
        and date_col
        and date_col in work_df.columns
        and numeric_cols
        and not work_df.empty
    ):
        before = len(work_df)
        work_df[date_col] = work_df[date_col].dt.strftime('%Y-%m-%d')
        group_keys = [date_col]
        if group_col and group_col in work_df.columns:
            group_keys.append(group_col)
        work_df = (
            work_df.groupby(group_keys, as_index=False)[numeric_cols]
            .sum()
            .sort_values(group_keys)
            .reset_index(drop=True)
        )
        aggregated_rows_merged = int(before - len(work_df))
        operations.append(
            f'Aggregated numerical columns by {" & ".join(group_keys)} (merged {aggregated_rows_merged} record(s)).'
        )
    elif date_col and date_col in work_df.columns:
        work_df = work_df.sort_values(date_col).reset_index(drop=True)
        work_df[date_col] = work_df[date_col].dt.strftime('%Y-%m-%d')

    for col in numeric_cols:
        if col in work_df.columns:
            work_df[col] = work_df[col].round(4)

    preview_cols = [str(c) for c in work_df.columns]
    preview_df = work_df.head(10).fillna('')
    summary = {
        'rows_before': rows_before,
        'rows_after': int(len(work_df)),
        'duplicates_removed': duplicates_removed,
        'invalid_dates_removed': invalid_dates_removed,
        'negative_removed': negative_removed,
        'missing_handled': missing_numeric_count,
        'suspicious_outliers_flagged': suspicious_count,
        'aggregated_rows_merged': aggregated_rows_merged,
        'operations': operations,
        'columns': preview_cols,
        'preview_rows': preview_df.astype(str).to_dict(orient='records'),
    }
    return work_df, summary


def clean_dataset_dataframe(file_path, mapping, options):
    """
    Backward-compatible cleaning wrapper for the 4-column sales schema,
    normalizing output columns to ['date', 'product', 'quantity', 'revenue'].
    """
    df = read_raw_csv(file_path)
    rows_before = int(len(df))
    operations = []

    work_df = pd.DataFrame(
        {
            'date_raw': df[mapping['date']],
            'product': df[mapping['product']],
            'quantity': pd.to_numeric(df[mapping['quantity']], errors='coerce'),
            'revenue': pd.to_numeric(df[mapping['revenue']], errors='coerce'),
        }
    )

    duplicates_removed = 0
    if options.get('remove_duplicates', True):
        before = len(work_df)
        work_df = work_df.drop_duplicates()
        duplicates_removed = int(before - len(work_df))
        operations.append(f'Removed {duplicates_removed} duplicate record(s).')

    work_df['product'] = work_df['product'].where(work_df['product'].notna(), '')
    work_df['product'] = work_df['product'].astype(str).str.strip()
    work_df['date'] = pd.to_datetime(work_df['date_raw'], errors='coerce')

    invalid_dates_removed = 0
    if options.get('remove_invalid_dates', True):
        before = len(work_df)
        work_df = work_df.dropna(subset=['date'])
        work_df = work_df[work_df['product'] != '']
        invalid_dates_removed = int(before - len(work_df))
        operations.append(
            f'Removed {invalid_dates_removed} row(s) with invalid dates or blank group names.'
        )

    negative_removed = 0
    if options.get('remove_negative_values', True):
        before = len(work_df)
        qty_ok = work_df['quantity'].isna() | (work_df['quantity'] >= 0)
        rev_ok = work_df['revenue'].isna() | (work_df['revenue'] >= 0)
        work_df = work_df[qty_ok & rev_ok]
        negative_removed = int(before - len(work_df))
        operations.append(f'Removed {negative_removed} row(s) with negative numerical values.')

    missing_strategy = options.get('missing_strategy', 'drop')
    missing_handled = int(work_df['quantity'].isna().sum() + work_df['revenue'].isna().sum())
    if missing_strategy == 'drop':
        before = len(work_df)
        work_df = work_df.dropna(subset=['quantity', 'revenue'])
        dropped = int(before - len(work_df))
        operations.append(f'Dropped {dropped} row(s) with missing numerical values.')
    elif missing_strategy == 'median':
        qty_med = work_df['quantity'].median()
        rev_med = work_df['revenue'].median()
        work_df['quantity'] = work_df['quantity'].fillna(0.0 if pd.isna(qty_med) else qty_med)
        work_df['revenue'] = work_df['revenue'].fillna(0.0 if pd.isna(rev_med) else rev_med)
        operations.append(f'Imputed {missing_handled} missing numeric value(s) using column medians.')
    elif missing_strategy == 'zero':
        work_df['quantity'] = work_df['quantity'].fillna(0.0)
        work_df['revenue'] = work_df['revenue'].fillna(0.0)
        operations.append(f'Filled {missing_handled} missing numeric value(s) with 0.')

    suspicious_count = 0
    if len(work_df) >= 8:
        q1, q3 = work_df['revenue'].quantile(0.25), work_df['revenue'].quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            suspicious_count = int((work_df['revenue'] > q3 + 3.0 * iqr).sum())

    work_df = work_df[['date', 'product', 'quantity', 'revenue']].copy()
    aggregated_rows_merged = 0
    if options.get('aggregate_daily', True) and not work_df.empty:
        before = len(work_df)
        work_df['date'] = work_df['date'].dt.strftime('%Y-%m-%d')
        work_df = (
            work_df.groupby(['date', 'product'], as_index=False)[['quantity', 'revenue']]
            .sum()
            .sort_values(['date', 'product'])
            .reset_index(drop=True)
        )
        aggregated_rows_merged = int(before - len(work_df))
        operations.append(
            f'Aggregated records by Date and Product (merged {aggregated_rows_merged} same-day records).'
        )
    else:
        work_df = work_df.sort_values('date').reset_index(drop=True)
        work_df['date'] = work_df['date'].dt.strftime('%Y-%m-%d')

    work_df['quantity'] = work_df['quantity'].round(2)
    work_df['revenue'] = work_df['revenue'].round(2)

    summary = {
        'rows_before': rows_before,
        'rows_after': int(len(work_df)),
        'duplicates_removed': duplicates_removed,
        'invalid_dates_removed': invalid_dates_removed,
        'negative_removed': negative_removed,
        'missing_handled': missing_handled,
        'suspicious_outliers_flagged': suspicious_count,
        'aggregated_rows_merged': aggregated_rows_merged,
        'operations': operations,
        'columns': ['date', 'product', 'quantity', 'revenue'],
        'preview_rows': work_df.head(10).to_dict(orient='records'),
    }
    return work_df, summary


def generate_sample_sales_csv_bytes():
    """
    Generate a clearly labeled synthetic retail sales CSV (120 days, 4 products)
    with weekly seasonality and intentional quality test rows.
    """
    rng = random.Random(42)
    start_date = date(2025, 1, 1)
    products = [
        ('Wireless Mouse', 28.0, 24.0),
        ('Mechanical Keyboard', 75.0, 14.0),
        ('USB-C Hub', 42.0, 18.0),
        ('Laptop Stand', 35.0, 12.0),
    ]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['sale_date', 'product_name', 'units_sold', 'sales_revenue'])

    sample_duplicate_row = None
    for day_idx in range(120):
        current_date = start_date + timedelta(days=day_idx)
        weekday_factor = 1.25 if current_date.weekday() in (4, 5) else 0.95
        trend_factor = 1.0 + (day_idx / 400.0)

        for prod_name, unit_price, base_units in products:
            seasonal = 1.0 + 0.15 * math.sin(2.0 * math.pi * day_idx / 7.0)
            units = max(1, round(base_units * weekday_factor * trend_factor * seasonal + rng.gauss(0, 2.2)))
            if day_idx == 75 and prod_name == 'Wireless Mouse':
                units = units * 3
            revenue = round(units * unit_price, 2)
            row = [current_date.isoformat(), prod_name, units, revenue]
            writer.writerow(row)
            if day_idx == 4 and prod_name == 'Wireless Mouse':
                sample_duplicate_row = row

    if sample_duplicate_row:
        writer.writerow(sample_duplicate_row)
    writer.writerow(['2025-02-14', 'USB-C Hub', '', 420.00])
    writer.writerow(['2025-03-01', 'Laptop Stand', -3, -105.00])

    return output.getvalue().encode('utf-8')


def generate_sample_traffic_csv_bytes():
    """
    Generate a clearly labeled synthetic website traffic CSV (90 days, 3 traffic sources)
    for testing InsightLab with non-sales datasets.
    """
    rng = random.Random(101)
    start_date = date(2025, 2, 1)
    sources = [
        ('Organic Search', 420, 2.8),
        ('Direct', 210, 2.1),
        ('Social Media', 165, 1.9),
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Traffic Source', 'Visitors', 'Page Views'])
    for day_idx in range(90):
        dt = start_date + timedelta(days=day_idx)
        weekday_boost = 0.85 if dt.weekday() in (5, 6) else 1.08
        for src, base_vis, pages_per_vis in sources:
            visitors = max(20, round(base_vis * weekday_boost * (1 + day_idx / 300.0) + rng.gauss(0, 22)))
            if day_idx == 52 and src == 'Social Media':
                visitors = visitors * 3
            page_views = round(visitors * (pages_per_vis + rng.uniform(-0.2, 0.2)))
            writer.writerow([dt.isoformat(), src, visitors, page_views])
    return output.getvalue().encode('utf-8')


def generate_sample_expenses_csv_bytes():
    """
    Generate a clearly labeled synthetic business expense CSV (90 days, 4 categories)
    for testing InsightLab with financial/operational datasets.
    """
    rng = random.Random(202)
    start_date = date(2025, 3, 1)
    categories = [
        ('Cloud Hosting', 145.0, 'Corporate Card'),
        ('Marketing Ads', 260.0, 'Bank Transfer'),
        ('Software Licenses', 85.0, 'Corporate Card'),
        ('Logistics & Shipping', 190.0, 'Invoice'),
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Transaction Date', 'Category', 'Amount', 'Payment Method'])
    for day_idx in range(90):
        dt = start_date + timedelta(days=day_idx)
        for cat, base_amt, pay_method in categories:
            amount = max(10.0, round(base_amt * (1 + 0.1 * math.sin(day_idx / 7.0)) + rng.gauss(0, 18.0), 2))
            writer.writerow([dt.isoformat(), cat, amount, pay_method])
    return output.getvalue().encode('utf-8')
