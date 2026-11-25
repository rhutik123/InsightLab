"""
Dataset validation, multi-format ingestion (CSV, XLSX, XLS, JSON), automatic schema
detection, column mapping, automated profiling, non-destructive cleaning, and
synthetic sample dataset generators.
Designed to support general structured tabular datasets while keeping full
compatibility with sales and predictive forecasting workflows.
"""

import csv
import datetime as dt
import io
import json
import math
import os
import posixpath
import random
import re
import zipfile
from datetime import date, timedelta

import numpy as np
import openpyxl
import pandas as pd
import xlrd

MAX_CSV_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB
MAX_UPLOAD_SIZE_BYTES = MAX_CSV_SIZE_BYTES
SUPPORTED_UPLOAD_EXTENSIONS = {'.csv', '.xlsx', '.xls', '.json'}
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MB zip-bomb defense
MAX_XLSX_ARCHIVE_ENTRIES = 500
MAX_EXCEL_ROWS = 200000
MAX_EXCEL_COLS = 250
MAX_JSON_DEPTH = 12
MAX_JSON_CANDIDATES = 25
OLE2_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
ZIP_MAGIC_PREFIXES = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')


def sanitize_original_filename(filename):
    """Return a clean single-segment filename without directory traversal or control characters."""
    base_name = os.path.basename((filename or '').replace('\\', '/')).strip()
    if not base_name:
        return 'dataset.csv'
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', base_name)
    cleaned = cleaned.lstrip('.') or 'dataset.csv'
    return cleaned[:255]


def detect_file_format(filename):
    """Return normalized format code ('csv', 'xlsx', 'xls', 'json') or None if unsupported."""
    ext = os.path.splitext((filename or '').strip().lower())[1]
    mapping = {
        '.csv': 'csv',
        '.xlsx': 'xlsx',
        '.xls': 'xls',
        '.json': 'json',
    }
    return mapping.get(ext)


def _sanitize_headers(raw_headers):
    """
    Normalize raw column headers: replace blank headers with deterministic names,
    collapse multiline whitespace, and deduplicate repeated names safely.
    Returns (cleaned_headers, ingestion_notes).
    """
    cleaned = []
    seen_counts = {}
    blank_renamed = 0
    duplicate_renamed = 0

    for idx, raw_name in enumerate(raw_headers, start=1):
        text = '' if raw_name is None else str(raw_name)
        text = re.sub(r'[\r\n\t]+', ' ', text).strip()
        if not text or text.lower() in ('nan', 'none', 'null'):
            base = f'Unnamed_Col_{idx}'
            blank_renamed += 1
        elif re.match(r'^Unnamed:\s*\d+$', text):
            base = f'Unnamed_Col_{idx}'
            blank_renamed += 1
        else:
            base = text

        base = base[:110]
        if base in seen_counts:
            seen_counts[base] += 1
            unique_name = f'{base}_{seen_counts[base]}'
            while unique_name in seen_counts:
                seen_counts[base] += 1
                unique_name = f'{base}_{seen_counts[base]}'
            seen_counts[unique_name] = 1
            duplicate_renamed += 1
            cleaned.append(unique_name)
        else:
            seen_counts[base] = 1
            cleaned.append(base)

    notes = []
    if blank_renamed:
        notes.append(f'{blank_renamed} blank header column(s) were automatically named.')
    if duplicate_renamed:
        notes.append(f'{duplicate_renamed} duplicate header name(s) were suffixed for uniqueness.')
    return cleaned, notes


def _validate_csv_bytes_content(raw_bytes):
    """Shared validation logic for CSV bytes content."""
    if not raw_bytes:
        return False, 'The uploaded CSV file is empty.'

    sample_bytes = raw_bytes[:65536]
    if b'\x00' in sample_bytes:
        return False, 'Binary or NUL-byte content is not a valid CSV file.'

    try:
        text_sample = sample_bytes.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError:
        try:
            text_sample = sample_bytes.decode('latin-1')
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
    except Exception:
        return False, 'Unable to read uploaded CSV file.'

    return _validate_csv_bytes_content(sample_bytes)


def _format_excel_cell_value(value):
    """Convert raw Excel cell values into clean tabular scalars without executing formulas."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0:
            return value.date().isoformat()
        return value.isoformat(sep=' ', timespec='seconds')
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat(timespec='seconds')
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped != '' else None
    return value


def _extract_xls_cell(cell, datemode, error_counter=None):
    """Extract a single xlrd cell into a normalized scalar without executing formulas."""
    ctype = cell.ctype
    if ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if ctype == xlrd.XL_CELL_DATE:
        try:
            dt_val = xlrd.xldate_as_datetime(cell.value, datemode)
            return _format_excel_cell_value(dt_val)
        except Exception:
            return cell.value
    if ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if ctype == xlrd.XL_CELL_ERROR:
        if error_counter is not None:
            error_counter[0] += 1
        return None
    if ctype == xlrd.XL_CELL_NUMBER:
        val = cell.value
        if isinstance(val, float) and val.is_integer():
            return int(val)
        return _format_excel_cell_value(val)
    return _format_excel_cell_value(cell.value)


class ExcelLimitExceededError(ValueError):
    """Raised when an Excel worksheet exceeds the maximum supported row or column limit."""


def _extract_xlsx_sheet_matrix(ws, sheet_name='Worksheet', max_rows=None, validate_bounds=True):
    """
    Extract trimmed matrix from an openpyxl read-only worksheet.
    Raises ExcelLimitExceededError rather than silently truncating rows or columns.
    """
    rows = []
    non_empty_rows = 0
    for raw_row in ws.iter_rows(values_only=True):
        if len(raw_row) > MAX_EXCEL_COLS:
            for extra_cell in raw_row[MAX_EXCEL_COLS:]:
                if _format_excel_cell_value(extra_cell) is not None:
                    raise ExcelLimitExceededError(
                        f'Worksheet "{sheet_name}" exceeds the maximum supported limit of '
                        f'{MAX_EXCEL_COLS} columns ({MAX_EXCEL_ROWS:,} rows max). '
                        'Please reduce the column count and upload again.'
                    )
        converted = [_format_excel_cell_value(cell) for cell in raw_row[:MAX_EXCEL_COLS]]
        while converted and converted[-1] is None:
            converted.pop()
        if any(cell is not None for cell in converted):
            non_empty_rows += 1
            if non_empty_rows > MAX_EXCEL_ROWS:
                raise ExcelLimitExceededError(
                    f'Worksheet "{sheet_name}" exceeds the maximum supported limit of '
                    f'{MAX_EXCEL_ROWS:,} rows ({MAX_EXCEL_COLS} columns max). '
                    'Please split the workbook into smaller files and upload again.'
                )
            if max_rows is None or len(rows) < max_rows:
                rows.append(converted)
            if max_rows is not None and not validate_bounds and len(rows) >= max_rows:
                break
    return rows


def _extract_xls_sheet_matrix(sheet, datemode, sheet_name='Worksheet', max_rows=None, validate_bounds=True):
    """
    Extract trimmed matrix from an xlrd worksheet, converting Excel serial dates.
    Raises ExcelLimitExceededError rather than silently truncating rows or columns.
    """
    rows = []
    non_empty_rows = 0
    error_counter = [0]
    col_cap = min(sheet.ncols, MAX_EXCEL_COLS)

    for r in range(sheet.nrows):
        if sheet.ncols > MAX_EXCEL_COLS:
            for c_extra in range(MAX_EXCEL_COLS, sheet.ncols):
                if _extract_xls_cell(sheet.cell(r, c_extra), datemode) is not None:
                    raise ExcelLimitExceededError(
                        f'Worksheet "{sheet_name}" exceeds the maximum supported limit of '
                        f'{MAX_EXCEL_COLS} columns ({MAX_EXCEL_ROWS:,} rows max). '
                        'Please reduce the column count and upload again.'
                    )

        converted = [_extract_xls_cell(sheet.cell(r, c), datemode, error_counter) for c in range(col_cap)]
        while converted and converted[-1] is None:
            converted.pop()
        if any(cell is not None for cell in converted):
            non_empty_rows += 1
            if non_empty_rows > MAX_EXCEL_ROWS:
                raise ExcelLimitExceededError(
                    f'Worksheet "{sheet_name}" exceeds the maximum supported limit of '
                    f'{MAX_EXCEL_ROWS:,} rows ({MAX_EXCEL_COLS} columns max). '
                    'Please split the workbook into smaller files and upload again.'
                )
            if max_rows is None or len(rows) < max_rows:
                rows.append(converted)
            if max_rows is not None and not validate_bounds and len(rows) >= max_rows:
                break

    return rows, error_counter[0]


def _rows_matrix_to_dataframe(rows):
    """
    Convert a non-empty matrix where row 0 is headers into (df, notes).
    Aligns ragged rows and sanitizes duplicate/blank headers.
    """
    if not rows:
        return pd.DataFrame(), ['Worksheet contains no non-empty rows.']

    max_width = max(len(r) for r in rows)
    header_row = list(rows[0]) + [None] * (max_width - len(rows[0]))
    clean_headers, header_notes = _sanitize_headers(header_row)

    data_rows = []
    for raw in rows[1:]:
        padded = list(raw) + [None] * (max_width - len(raw))
        if any(cell is not None for cell in padded):
            data_rows.append(padded)

    df = pd.DataFrame(data_rows, columns=clean_headers)
    return df, header_notes


def _count_uncached_xlsx_formulas(file_source, sheet_name, max_scan_rows=2000):
    """
    Detect cells containing Excel formula strings ('=...') that have None as their
    cached value under data_only=True so InsightLab can warn transparently.
    """
    wb_cached = None
    wb_formula = None
    try:
        wb_cached = openpyxl.load_workbook(file_source, read_only=True, data_only=True)
        wb_formula = openpyxl.load_workbook(file_source, read_only=True, data_only=False)
        if sheet_name not in wb_cached.sheetnames or sheet_name not in wb_formula.sheetnames:
            return 0
        ws_c = wb_cached[sheet_name]
        ws_f = wb_formula[sheet_name]
        uncached = 0
        for row_idx, (rc, rf) in enumerate(
            zip(ws_c.iter_rows(values_only=True), ws_f.iter_rows(values_only=True)),
            start=1,
        ):
            if row_idx > max_scan_rows:
                break
            for vc, vf in zip(rc[:MAX_EXCEL_COLS], rf[:MAX_EXCEL_COLS]):
                if isinstance(vf, str) and vf.startswith('=') and vc is None:
                    uncached += 1
        return uncached
    except Exception:
        return 0
    finally:
        for wb in (wb_cached, wb_formula):
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass


def _validate_xlsx_bytes(raw_bytes):
    """Safely inspect ZIP structure, detect encryption/macros/zip bombs, and verify tabular sheet data."""
    if not raw_bytes:
        return False, 'The uploaded .xlsx file is empty.'

    if raw_bytes.startswith(OLE2_MAGIC):
        return False, 'Encrypted or legacy-wrapped Excel files cannot be parsed. Upload an unencrypted .xlsx or .xls workbook.'

    if not raw_bytes.startswith(ZIP_MAGIC_PREFIXES):
        return False, 'Invalid .xlsx file: file signature does not match a valid Excel OpenXML archive.'

    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes), 'r') as zf:
            infos = zf.infolist()
            if not infos:
                return False, 'Invalid .xlsx archive: archive is empty.'
            if len(infos) > MAX_XLSX_ARCHIVE_ENTRIES:
                return False, 'Invalid .xlsx archive: excessive archive entries detected.'

            total_uncompressed = 0
            names = set()
            for info in infos:
                normalized_name = posixpath.normpath(info.filename.replace('\\', '/'))
                if normalized_name.startswith('/') or normalized_name.startswith('..') or '/../' in normalized_name:
                    return False, 'Invalid .xlsx archive: unsafe internal archive path.'
                total_uncompressed += max(0, info.file_size)
                if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
                    return False, 'The .xlsx archive exceeds the safe uncompressed size limit (possible zip bomb).'
                names.add(info.filename.lower())

            if '[content_types].xml' not in names or not any(n.endswith('workbook.xml') for n in names):
                return False, 'Invalid .xlsx file: missing required Excel workbook metadata.'
            if any(n.endswith('vbaproject.bin') for n in names):
                return False, 'Macro-enabled workbooks are not supported. Save the workbook as standard .xlsx without macros.'
    except zipfile.BadZipFile:
        return False, 'Corrupted .xlsx file: unable to read OpenXML ZIP archive.'
    except Exception as exc:
        return False, f'Unable to inspect .xlsx archive: {exc}'

    wb = None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        if not wb.sheetnames:
            return False, 'The .xlsx workbook contains no worksheets.'

        has_usable_sheet = False
        for name in wb.sheetnames:
            rows = _extract_xlsx_sheet_matrix(wb[name], sheet_name=name, max_rows=10, validate_bounds=True)
            if not has_usable_sheet and len(rows) >= 2:
                df_preview, _ = _rows_matrix_to_dataframe(rows)
                if len(df_preview.columns) >= 2 and len(df_preview) >= 1:
                    has_usable_sheet = True

        if not has_usable_sheet:
            return False, (
                'No usable worksheet found in the .xlsx workbook. Each usable sheet must have at least '
                '2 columns and 1 data row.'
            )
    except ExcelLimitExceededError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f'Unable to parse .xlsx workbook safely: {exc}'
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass

    return True, None


def _validate_xls_bytes(raw_bytes):
    """Safely validate legacy BIFF .xls bytes using xlrd without executing macros."""
    if not raw_bytes:
        return False, 'The uploaded .xls file is empty.'

    if not raw_bytes.startswith(OLE2_MAGIC):
        return False, 'Invalid .xls file: file signature does not match a valid legacy Excel (.xls) workbook.'

    wb = None
    try:
        wb = xlrd.open_workbook(file_contents=raw_bytes, on_demand=True)
        if wb.nsheets == 0:
            return False, 'The .xls workbook contains no worksheets.'

        has_usable_sheet = False
        for idx in range(wb.nsheets):
            sheet = wb.sheet_by_index(idx)
            rows, _ = _extract_xls_sheet_matrix(
                sheet, wb.datemode, sheet_name=sheet.name, max_rows=10, validate_bounds=True
            )
            if not has_usable_sheet and len(rows) >= 2:
                df_preview, _ = _rows_matrix_to_dataframe(rows)
                if len(df_preview.columns) >= 2 and len(df_preview) >= 1:
                    has_usable_sheet = True

        if not has_usable_sheet:
            return False, (
                'No usable worksheet found in the .xls workbook. Each usable sheet must have at least '
                '2 columns and 1 data row.'
            )
    except ExcelLimitExceededError as exc:
        return False, str(exc)
    except xlrd.XLRDError as exc:
        return False, f'Invalid or unsupported .xls workbook: {exc}'
    except Exception as exc:
        return False, f'Unable to parse legacy .xls workbook: {exc}'
    finally:
        if wb is not None:
            try:
                wb.release_resources()
            except Exception:
                pass

    return True, None


def _flatten_json_record_items(record, raw_path=(), display_parent='', sep='.', depth=0):
    """
    Flatten nested JSON objects into (structural_path_tuple, dotted_name, value) entries.
    Preserves structural key origin so nested keys vs. literal dotted keys never collide silently.
    """
    items = []
    if not isinstance(record, dict):
        return items

    for key, value in record.items():
        clean_key = str(key).strip() or 'field'
        struct_path = raw_path + (str(key),)
        full_key = f'{display_parent}{sep}{clean_key}' if display_parent else clean_key
        if isinstance(value, dict):
            if depth < MAX_JSON_DEPTH and value:
                items.extend(
                    _flatten_json_record_items(
                        value,
                        raw_path=struct_path,
                        display_parent=full_key,
                        sep=sep,
                        depth=depth + 1,
                    )
                )
            elif not value:
                items.append((struct_path, full_key, None))
            else:
                items.append((struct_path, full_key, json.dumps(value, ensure_ascii=False)))
        elif isinstance(value, list):
            if value:
                items.append((struct_path, full_key, json.dumps(value, ensure_ascii=False)))
            else:
                items.append((struct_path, full_key, None))
        else:
            items.append((struct_path, full_key, value))
    return items


def _flatten_json_record(record, parent_key='', sep='.', depth=0):
    """
    Flatten a single JSON object safely without overwriting colliding dotted keys.
    Returns a flat dictionary with deterministic unique keys.
    """
    entries = _flatten_json_record_items(record, display_parent=parent_key, sep=sep, depth=depth)
    candidate_names = [display_name for _, display_name, _ in entries]
    unique_names, _ = _sanitize_headers(candidate_names)
    return {col_name: val for col_name, (_, _, val) in zip(unique_names, entries)}


def _discover_json_candidates_with_meta(parsed_json):
    """
    Find candidate tabular record sets inside a parsed JSON structure up to MAX_JSON_CANDIDATES.
    Tracks explicitly whether more than MAX_JSON_CANDIDATES collections exist so users are warned.
    """
    candidates = []
    state = {'truncated': False, 'omitted_count': 0}

    def _walk(node, path='$', depth=0):
        if depth > MAX_JSON_DEPTH:
            return
        if isinstance(node, list):
            dict_items = [item for item in node if isinstance(item, dict)]
            if dict_items:
                if len(candidates) >= MAX_JSON_CANDIDATES:
                    state['truncated'] = True
                    state['omitted_count'] += 1
                    return
                if len(dict_items) == len(node):
                    candidates.append({
                        'path': path,
                        'records': dict_items,
                        'kind': 'array_of_objects',
                    })
                else:
                    candidates.append({
                        'path': path,
                        'records': dict_items,
                        'kind': 'mixed_array_filtered',
                        'skipped_non_objects': len(node) - len(dict_items),
                    })
        elif isinstance(node, dict):
            # Prefer common data wrapper keys first for deterministic ordering
            preferred = ['data', 'records', 'items', 'results', 'rows']
            ordered_keys = [k for k in preferred if k in node] + [k for k in node.keys() if k not in preferred]
            for key in ordered_keys:
                child_path = str(key) if path == '$' else f'{path}.{key}'
                _walk(node[key], child_path, depth + 1)

    _walk(parsed_json, '$', 0)

    # Single JSON object (1-row dataset) only if no array candidates exist inside
    if not candidates and isinstance(parsed_json, dict) and parsed_json:
        candidates.append({
            'path': '$',
            'records': [parsed_json],
            'kind': 'single_object',
        })

    return {
        'candidates': candidates,
        'truncated': state['truncated'],
        'max_candidates': MAX_JSON_CANDIDATES,
        'omitted_count': state['omitted_count'],
    }


def _discover_json_candidates_from_obj(parsed_json):
    """Return candidate tabular record collections from parsed JSON."""
    return _discover_json_candidates_with_meta(parsed_json)['candidates']


def normalize_json_records(records, extra_notes=None):
    """
    Convert a list of JSON record dicts into (DataFrame, notes).
    Preserves distinct structural paths when nested keys collide with dotted keys.
    """
    notes = list(extra_notes or [])
    dict_records = [rec for rec in records if isinstance(rec, dict)]
    if not dict_records:
        return pd.DataFrame(), notes + ['Selected JSON structure contains no object records.']

    has_nested_dict = any(
        isinstance(v, dict) and bool(v)
        for rec in dict_records
        for v in rec.values()
    )
    has_nested_list = any(
        isinstance(v, list) and bool(v)
        for rec in dict_records
        for v in rec.values()
    )

    all_paths = []
    seen_paths = set()
    path_to_display = {}
    flattened_row_maps = []

    for rec in dict_records:
        row_map = {}
        for struct_path, display_name, val in _flatten_json_record_items(rec):
            row_map[struct_path] = val
            if struct_path not in seen_paths:
                seen_paths.add(struct_path)
                all_paths.append(struct_path)
                path_to_display[struct_path] = display_name
        flattened_row_maps.append(row_map)

    inconsistent = any(set(rm.keys()) != seen_paths for rm in flattened_row_maps)
    raw_display_names = [path_to_display[p] for p in all_paths]

    # Identify exact collisions before header sanitization (e.g. nested vs literal dotted key)
    seen_display = set()
    colliding_names = []
    for name in raw_display_names:
        if name in seen_display and name not in colliding_names:
            colliding_names.append(name)
        seen_display.add(name)

    clean_headers, header_notes = _sanitize_headers(raw_display_names)

    aligned_rows = [
        [row_map.get(path, None) for path in all_paths]
        for row_map in flattened_row_maps
    ]
    df = pd.DataFrame(aligned_rows, columns=clean_headers)

    if has_nested_dict:
        notes.append('Nested JSON object properties were flattened using dot notation (e.g., parent.child).')
    if colliding_names:
        preview_colliding = ', '.join(f'"{c}"' for c in colliding_names[:5])
        notes.append(
            f'Detected flattened JSON column collision(s) on {preview_colliding}; '
            'preserved all values using deterministic unique column names.'
        )
    if has_nested_list:
        notes.append('Nested JSON array values within records were preserved as JSON text.')
    if inconsistent:
        notes.append('Inconsistent object keys across records were aligned using null values.')
    for hnote in header_notes:
        if not (colliding_names and 'duplicate header name' in hnote.lower()):
            notes.append(hnote)
    return df, notes


def _validate_json_bytes(raw_bytes):
    """Validate uploaded JSON bytes safely using stdlib json (never eval)."""
    if not raw_bytes:
        return False, 'The uploaded JSON file is empty.'

    if b'\x00' in raw_bytes[:65536]:
        return False, 'Binary or NUL-byte content is not a valid JSON file.'

    try:
        text = raw_bytes.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError:
        return False, 'JSON files must be valid UTF-8 encoded text.'

    if not text.strip():
        return False, 'The uploaded JSON file is empty.'

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f'Invalid JSON syntax: {exc.msg} (line {exc.lineno}, column {exc.colno}).'

    if not isinstance(parsed, (list, dict)):
        return False, 'Unsupported JSON root type. Upload a JSON array of objects or a structured JSON object.'

    if len(parsed) == 0:
        return False, 'The uploaded JSON structure is empty.'

    candidates = _discover_json_candidates_from_obj(parsed)
    if not candidates:
        return False, (
            'Unsupported JSON structure: no tabular array of objects or structured record object was found.'
        )

    usable = False
    for cand in candidates:
        df_cand, _ = normalize_json_records(cand['records'][:25])
        if len(df_cand.columns) >= 2 and len(df_cand) >= 1:
            usable = True
            break

    if not usable:
        return False, (
            'No usable tabular JSON record set was found. Each dataset must yield at least 2 columns and 1 record.'
        )

    return True, None


def validate_uploaded_dataset(uploaded_file, max_size_bytes=MAX_UPLOAD_SIZE_BYTES):
    """
    Validate an uploaded Django file across CSV, XLSX, XLS, and JSON formats.
    Returns (is_valid: bool, error_message: str | None, detected_format: str | None).
    """
    if not uploaded_file:
        return False, 'Please choose a dataset file to upload.', None

    fmt = detect_file_format(uploaded_file.name)
    if not fmt:
        return (
            False,
            'Unsupported file format. Supported formats are CSV (.csv), Excel (.xlsx, .xls), and JSON (.json).',
            None,
        )

    if uploaded_file.size == 0:
        return False, f'The uploaded .{fmt} file is empty.', fmt

    if uploaded_file.size > max_size_bytes:
        max_mb = max_size_bytes // (1024 * 1024)
        return False, f'File size exceeds the {max_mb} MB limit.', fmt

    try:
        uploaded_file.seek(0)
        if fmt == 'csv':
            sample_bytes = uploaded_file.read(65536)
            uploaded_file.seek(0)
            ok, err = _validate_csv_bytes_content(sample_bytes)
            return ok, err, fmt
        raw_bytes = uploaded_file.read()
        uploaded_file.seek(0)
    except Exception:
        return False, 'Unable to read the uploaded file.', fmt

    if fmt == 'xlsx':
        ok, err = _validate_xlsx_bytes(raw_bytes)
    elif fmt == 'xls':
        ok, err = _validate_xls_bytes(raw_bytes)
    elif fmt == 'json':
        ok, err = _validate_json_bytes(raw_bytes)
    else:
        ok, err = False, 'Unsupported file format.'

    return ok, err, fmt


def inspect_excel_sheets(file_path, source_format='xlsx', preview_rows=5):
    """
    Inspect every worksheet in an .xlsx or .xls workbook without executing formulas/macros.
    Returns list of sheet summary dicts containing headers, row/col counts, usability,
    ingestion notes, and preview rows.
    """
    summaries = []
    if source_format == 'xlsx':
        wb = None
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                try:
                    rows = _extract_xlsx_sheet_matrix(ws, sheet_name=sheet_name)
                except ExcelLimitExceededError as exc:
                    summaries.append({
                        'name': sheet_name,
                        'row_count': 0,
                        'column_count': 0,
                        'headers': [],
                        'preview_rows': [],
                        'is_usable': False,
                        'notes': [str(exc)],
                    })
                    continue
                notes = []
                if not rows:
                    summaries.append({
                        'name': sheet_name,
                        'row_count': 0,
                        'column_count': 0,
                        'headers': [],
                        'preview_rows': [],
                        'is_usable': False,
                        'notes': ['Worksheet is empty.'],
                    })
                    continue

                df_sheet, header_notes = _rows_matrix_to_dataframe(rows)
                notes.extend(header_notes)
                uncached = _count_uncached_xlsx_formulas(file_path, sheet_name)
                if uncached:
                    notes.append(
                        f'{uncached} formula cell(s) have no cached values and were imported as missing.'
                    )

                n_rows = int(len(df_sheet))
                n_cols = int(len(df_sheet.columns))
                is_usable = n_cols >= 2 and n_rows >= 1
                if not is_usable:
                    notes.append('Sheet needs at least 2 columns and 1 data row to be imported.')

                preview_df = df_sheet.head(preview_rows).fillna('')
                summaries.append({
                    'name': sheet_name,
                    'row_count': n_rows,
                    'column_count': n_cols,
                    'headers': [str(c) for c in df_sheet.columns],
                    'preview_rows': preview_df.astype(str).to_dict(orient='records'),
                    'is_usable': is_usable,
                    'notes': notes,
                })
        finally:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass
    elif source_format == 'xls':
        wb = None
        try:
            wb = xlrd.open_workbook(file_path, on_demand=True)
            for idx in range(wb.nsheets):
                sheet = wb.sheet_by_index(idx)
                try:
                    rows, err_cells = _extract_xls_sheet_matrix(sheet, wb.datemode, sheet_name=sheet.name)
                except ExcelLimitExceededError as exc:
                    summaries.append({
                        'name': sheet.name,
                        'row_count': 0,
                        'column_count': 0,
                        'headers': [],
                        'preview_rows': [],
                        'is_usable': False,
                        'notes': [str(exc)],
                    })
                    continue
                notes = []
                if not rows:
                    summaries.append({
                        'name': sheet.name,
                        'row_count': 0,
                        'column_count': 0,
                        'headers': [],
                        'preview_rows': [],
                        'is_usable': False,
                        'notes': ['Worksheet is empty.'],
                    })
                    continue

                df_sheet, header_notes = _rows_matrix_to_dataframe(rows)
                notes.extend(header_notes)
                if err_cells:
                    notes.append(f'{err_cells} Excel error cell(s) were converted to missing values.')

                n_rows = int(len(df_sheet))
                n_cols = int(len(df_sheet.columns))
                is_usable = n_cols >= 2 and n_rows >= 1
                if not is_usable:
                    notes.append('Sheet needs at least 2 columns and 1 data row to be imported.')

                preview_df = df_sheet.head(preview_rows).fillna('')
                summaries.append({
                    'name': sheet.name,
                    'row_count': n_rows,
                    'column_count': n_cols,
                    'headers': [str(c) for c in df_sheet.columns],
                    'preview_rows': preview_df.astype(str).to_dict(orient='records'),
                    'is_usable': is_usable,
                    'notes': notes,
                })
        finally:
            if wb is not None:
                try:
                    wb.release_resources()
                except Exception:
                    pass
    else:
        raise ValueError(f'Unsupported Excel format: {source_format}')

    return summaries


def read_excel_file(file_path, sheet_name=None, source_format='xlsx'):
    """
    Read a specific worksheet from an .xlsx or .xls workbook into (DataFrame, notes).
    Auto-selects the first usable sheet when sheet_name is omitted.
    """
    notes = []
    if source_format == 'xlsx':
        wb = None
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            target_sheet = sheet_name or (wb.sheetnames[0] if wb.sheetnames else '')
            if target_sheet not in wb.sheetnames:
                raise ValueError(f'Selected worksheet "{target_sheet}" does not exist in workbook.')
            rows = _extract_xlsx_sheet_matrix(wb[target_sheet], sheet_name=target_sheet)
        finally:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass

        df, header_notes = _rows_matrix_to_dataframe(rows)
        notes.extend(header_notes)
        uncached = _count_uncached_xlsx_formulas(file_path, target_sheet)
        if uncached:
            notes.append(f'{uncached} formula cell(s) have no cached values and were imported as missing.')
    elif source_format == 'xls':
        wb = None
        try:
            wb = xlrd.open_workbook(file_path, on_demand=True)
            available_names = wb.sheet_names()
            target_sheet = sheet_name or (available_names[0] if available_names else '')
            if target_sheet not in available_names:
                raise ValueError(f'Selected worksheet "{target_sheet}" does not exist in workbook.')
            sheet = wb.sheet_by_name(target_sheet)
            rows, err_cells = _extract_xls_sheet_matrix(sheet, wb.datemode, sheet_name=target_sheet)
        finally:
            if wb is not None:
                try:
                    wb.release_resources()
                except Exception:
                    pass

        df, header_notes = _rows_matrix_to_dataframe(rows)
        notes.extend(header_notes)
        if err_cells:
            notes.append(f'{err_cells} Excel error cell(s) were converted to missing values.')
    else:
        raise ValueError(f'Unsupported Excel format: {source_format}')

    if len(df.columns) < 2 or df.empty:
        raise ValueError(f'Worksheet "{target_sheet}" must have at least 2 columns and 1 data row.')

    return df, notes


def inspect_json_structure(file_path, preview_rows=5):
    """
    Inspect JSON file structure and return a dict containing candidate tabular record paths,
    truncation metadata when >MAX_JSON_CANDIDATES arrays exist, and preview tables.
    """
    with open(file_path, 'r', encoding='utf-8-sig') as fh:
        parsed = json.load(fh)

    discovered = _discover_json_candidates_with_meta(parsed)
    raw_candidates = discovered['candidates']
    summaries = []
    for cand in raw_candidates:
        extra_notes = []
        if cand['kind'] == 'single_object':
            extra_notes.append('Single JSON object converted into a 1-row normalized table.')
        elif cand['kind'] == 'mixed_array_filtered':
            extra_notes.append(
                f"{cand['skipped_non_objects']} non-object element(s) in the JSON array were ignored."
            )

        df_cand, notes = normalize_json_records(cand['records'], extra_notes=extra_notes)
        n_rows = int(len(df_cand))
        n_cols = int(len(df_cand.columns))
        is_usable = n_cols >= 2 and n_rows >= 1
        if not is_usable:
            notes.append('Record path needs at least 2 columns and 1 record to be imported.')

        preview_df = df_cand.head(preview_rows).fillna('')
        summaries.append({
            'path': cand['path'],
            'label': 'Root JSON Structure ($)' if cand['path'] == '$' else cand['path'],
            'kind': cand['kind'],
            'row_count': n_rows,
            'column_count': n_cols,
            'headers': [str(c) for c in df_cand.columns],
            'preview_rows': preview_df.astype(str).to_dict(orient='records'),
            'is_usable': is_usable,
            'notes': notes,
        })

    return {
        'candidates': summaries,
        'truncated': discovered['truncated'],
        'max_candidates': discovered['max_candidates'],
        'omitted_count': discovered['omitted_count'],
    }


def inspect_json_candidates(file_path, preview_rows=5):
    """Return candidate tabular record paths from inspect_json_structure."""
    return inspect_json_structure(file_path, preview_rows=preview_rows)['candidates']


def read_json_file(file_path, json_path='$'):
    """Read and normalize a selected candidate JSON record path into (DataFrame, notes)."""
    with open(file_path, 'r', encoding='utf-8-sig') as fh:
        parsed = json.load(fh)

    candidates = _discover_json_candidates_from_obj(parsed)
    selected = None
    target_path = json_path or '$'
    for cand in candidates:
        if cand['path'] == target_path:
            selected = cand
            break

    if selected is None:
        raise ValueError(f'Selected JSON record path "{target_path}" was not found.')

    extra_notes = []
    if selected['kind'] == 'single_object':
        extra_notes.append('Single JSON object converted into a 1-row normalized table.')
    elif selected['kind'] == 'mixed_array_filtered':
        extra_notes.append(
            f"{selected['skipped_non_objects']} non-object element(s) in the JSON array were ignored."
        )

    df, notes = normalize_json_records(selected['records'], extra_notes=extra_notes)
    if len(df.columns) < 2 or df.empty:
        raise ValueError(f'JSON record path "{target_path}" must yield at least 2 columns and 1 record.')

    return df, notes


def normalize_dataset_to_internal_format(df, source_base_name='dataset'):
    """
    Convert any validated DataFrame (from XLSX, XLS, or JSON) into InsightLab's
    standardized UTF-8 CSV byte representation so all downstream modules share one pipeline.
    Returns (csv_bytes: bytes, normalized_filename: str).
    """
    work_df = df.copy()
    clean_cols, _ = _sanitize_headers(list(work_df.columns))
    work_df.columns = clean_cols
    work_df = work_df.dropna(how='all')

    stem = os.path.splitext(sanitize_original_filename(source_base_name))[0] or 'dataset'
    normalized_filename = f'{stem}_normalized.csv'
    buf = io.BytesIO()
    work_df.to_csv(buf, index=False, encoding='utf-8-sig')
    return buf.getvalue(), normalized_filename


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
