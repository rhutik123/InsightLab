"""
Automated tests for multi-format dataset upload validation (CSV, XLSX, XLS, JSON),
worksheet / JSON path selection, normalization, column mapping, profiling, cleaning,
security hardening, user ownership isolation, and end-to-end analytics/forecasting/PDF workflows.
"""

import datetime as dt
import io
import json
import os
import shutil
import struct
import tempfile
import zipfile

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import GeneratedReport

from .data_processing import (
    clean_dataset_dataframe,
    clean_general_dataframe,
    detect_column_schema,
    generate_sample_expenses_csv_bytes,
    generate_sample_sales_csv_bytes,
    generate_sample_traffic_csv_bytes,
    inspect_excel_sheets,
    inspect_json_candidates,
    profile_dataset,
    profile_general_dataset,
    read_excel_file,
    read_json_file,
    read_raw_csv,
    sanitize_original_filename,
    validate_csv_upload,
    validate_uploaded_dataset,
)
from .models import SalesDataset


def _biff_record(rec_id, payload=b''):
    return struct.pack('<HH', rec_id, len(payload)) + payload


def build_test_xls_bytes(sheets):
    """
    Build a minimal valid legacy BIFF8 (.xls) binary workbook in pure Python
    compatible with xlrd, without needing external writer libraries.
    `sheets`: list of (sheet_name, rows_list).
    """
    wb_bof = _biff_record(0x0809, struct.pack('<HHHHII', 0x0600, 0x0005, 0x0DBB, 0x07CC, 0, 0))
    codepage = _biff_record(0x0042, struct.pack('<H', 1200))
    eof = _biff_record(0x000A)

    ws_streams = []
    for _, rows in sheets:
        ws_parts = [_biff_record(0x0809, struct.pack('<HHHHII', 0x0600, 0x0010, 0x0DBB, 0x07CC, 0, 0))]
        for r_idx, row in enumerate(rows):
            for c_idx, val in enumerate(row):
                if val is None:
                    continue
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    ws_parts.append(_biff_record(0x0203, struct.pack('<HHHd', r_idx, c_idx, 0, float(val))))
                else:
                    text = str(val)
                    ws_parts.append(
                        _biff_record(0x0204, struct.pack('<HHHHB', r_idx, c_idx, 0, len(text), 1) + text.encode('utf-16-le'))
                    )
        ws_parts.append(eof)
        ws_streams.append(b''.join(ws_parts))

    pre_bs = wb_bof + codepage
    bs_records = [
        _biff_record(0x0085, struct.pack('<IHBB', 0, 0, len(name), 1) + name.encode('utf-16-le'))
        for name, _ in sheets
    ]
    global_len = len(pre_bs) + sum(len(r) for r in bs_records) + len(eof)
    real_bs = []
    offset = global_len
    for (name, _), ws_bytes in zip(sheets, ws_streams):
        real_bs.append(_biff_record(0x0085, struct.pack('<IHBB', offset, 0, len(name), 1) + name.encode('utf-16-le')))
        offset += len(ws_bytes)

    wb_stream = pre_bs + b''.join(real_bs) + eof + b''.join(ws_streams)
    if len(wb_stream) < 4096:
        wb_stream += b'\x00' * (4096 - len(wb_stream))
    sec_size = 512
    wb_sectors = (len(wb_stream) + sec_size - 1) // sec_size
    wb_stream_padded = wb_stream.ljust(wb_sectors * sec_size, b'\x00')

    wb_start_sec = 1
    total_data_secs = 1 + wb_sectors
    sat_secs = max(1, (total_data_secs + 1 + 127) // 128)
    sat_start_sec = total_data_secs

    sat = [-1] * (sat_secs * 128)
    sat[0] = -2
    for i in range(wb_sectors):
        sec_idx = wb_start_sec + i
        sat[sec_idx] = (sec_idx + 1) if i + 1 < wb_sectors else -2
    for i in range(sat_secs):
        sat[sat_start_sec + i] = -3

    def _dir_entry(name, etype, color, left, right, child, start_sec, size):
        u16 = (name + '\x00').encode('utf-16-le')
        return (
            u16.ljust(64, b'\x00')
            + struct.pack('<HBBiii', len(u16), etype, color, left, right, child)
            + b'\x00' * 16
            + b'\x00' * 20
            + struct.pack('<iiI', start_sec, size, 0)
        )

    dir_sector = (
        _dir_entry('Root Entry', 5, 1, -1, -1, 1, -2, 0)
        + _dir_entry('Workbook', 2, 1, -1, -1, -1, wb_start_sec, len(wb_stream))
        + b'\x00' * 128
        + b'\x00' * 128
    )
    difat = [sat_start_sec + i if i < sat_secs else -1 for i in range(109)]
    header = (
        b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
        + b'\x00' * 16
        + struct.pack('<HHHHHH', 0x003E, 0x0003, 0xFFFE, 9, 6, 0)
        + b'\x00' * 4
        + struct.pack('<iiiiiiiii', 0, sat_secs, 0, 0, 4096, -2, 0, -2, 0)
        + struct.pack('<109i', *difat)
    )
    return header + dir_sector + wb_stream_padded + struct.pack(f'<{len(sat)}i', *sat)


def build_test_xlsx_bytes(sheets):
    """Build a standard .xlsx workbook in memory via openpyxl."""
    wb = openpyxl.Workbook()
    first = True
    for sheet_name, rows in sheets:
        ws = wb.active if first else wb.create_sheet(title=sheet_name)
        if first:
            ws.title = sheet_name
            first = False
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


class DatasetProcessingUnitTests(TestCase):
    """Unit tests for pure data_processing functions."""

    def test_validate_csv_upload_rejects_bad_files(self):
        bad_ext = SimpleUploadedFile('sales.xlsx', b'dummy', content_type='application/octet-stream')
        ok, err = validate_csv_upload(bad_ext)
        self.assertFalse(ok)
        self.assertIn('.csv', err)

        empty_csv = SimpleUploadedFile('empty.csv', b'', content_type='text/csv')
        ok, err = validate_csv_upload(empty_csv)
        self.assertFalse(ok)
        self.assertIn('empty', err.lower())

        too_few_cols = SimpleUploadedFile('narrow.csv', b'single_col\n100\n', content_type='text/csv')
        ok, err = validate_csv_upload(too_few_cols)
        self.assertFalse(ok)
        self.assertIn('2 columns', err)

        binary_file = SimpleUploadedFile('bin.csv', b'a,b,c\n\x00\x01,2,3\n', content_type='text/csv')
        ok, err = validate_csv_upload(binary_file)
        self.assertFalse(ok)
        self.assertIn('Binary', err)

    def test_general_schema_detection_for_traffic_expenses_and_tabular_csv(self):
        # 1. Website Traffic CSV
        traffic_bytes = generate_sample_traffic_csv_bytes()
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
            tmp.write(traffic_bytes)
            t_path = tmp.name
        try:
            df = read_raw_csv(t_path)
            schema = detect_column_schema(df)
            self.assertEqual(schema['dataset_type'], 'traffic')
            self.assertEqual(schema['primary_date_column'], 'Date')
            self.assertEqual(schema['primary_group_column'], 'Traffic Source')
            self.assertIn('Visitors', schema['numeric_columns'])
            self.assertIn('Page Views', schema['numeric_columns'])
            prof = profile_general_dataset(t_path, schema)
            self.assertEqual(prof['numeric_col_count'], 2)
        finally:
            import os
            if os.path.exists(t_path):
                os.remove(t_path)

        # 2. Non-date tabular CSV (2 columns only)
        tabular_csv = b'Item,Score,Weight\nAlpha,88.5,12.1\nBeta,91.0,14.5\nAlpha,76.0,11.8\n'
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp2:
            tmp2.write(tabular_csv)
            tab_path = tmp2.name
        try:
            df_tab = read_raw_csv(tab_path)
            schema_tab = detect_column_schema(df_tab)
            self.assertEqual(schema_tab['primary_date_column'], '')
            cleaned_tab, summary_tab = clean_general_dataframe(
                tab_path, schema_tab, {'remove_duplicates': True, 'missing_strategy': 'drop'}
            )
            self.assertEqual(summary_tab['rows_after'], 3)
            self.assertIn('Weight', cleaned_tab.columns)
        finally:
            import os
            if os.path.exists(tab_path):
                os.remove(tab_path)

    def test_sample_csv_profiling_and_cleaning(self):
        csv_bytes = generate_sample_sales_csv_bytes()
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
            tmp.write(csv_bytes)
            tmp_path = tmp.name

        try:
            mapping = {
                'date': 'sale_date',
                'product': 'product_name',
                'quantity': 'units_sold',
                'revenue': 'sales_revenue',
            }
            profile = profile_dataset(tmp_path, mapping)
            self.assertEqual(profile['unique_products'], 4)
            self.assertGreater(profile['duplicate_rows'], 0)
            self.assertGreater(profile['total_missing'], 0)
            self.assertGreater(profile['negative_qty_count'], 0)

            cleaned_df, summary = clean_dataset_dataframe(
                tmp_path,
                mapping,
                {
                    'remove_duplicates': True,
                    'remove_invalid_dates': True,
                    'remove_negative_values': True,
                    'missing_strategy': 'drop',
                    'aggregate_daily': True,
                },
            )
            self.assertEqual((cleaned_df['quantity'] < 0).sum(), 0)
            self.assertEqual(cleaned_df.isna().sum().sum(), 0)
            self.assertLess(summary['rows_after'], summary['rows_before'])
        finally:
            import os
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class DatasetViewsIntegrationTests(TestCase):
    """Integration tests for upload, mapping, cleaning, deletion, and ownership."""

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.user_a = User.objects.create_user(username='user_a', password='StrongPassword!123')
        self.user_b = User.objects.create_user(username='user_b', password='StrongPassword!123')

    def tearDown(self):
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_upload_map_clean_and_ownership_isolation(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            self.client.login(username='user_a', password='StrongPassword!123')

            csv_bytes = generate_sample_sales_csv_bytes()
            uploaded = SimpleUploadedFile('retail_test.csv', csv_bytes, content_type='text/csv')
            upload_res = self.client.post(
                reverse('datasets:upload'),
                {'name': 'Retail Test', 'description': 'Sample test', 'original_file': uploaded},
            )
            dataset = SalesDataset.objects.get(user=self.user_a)
            self.assertRedirects(upload_res, reverse('datasets:map_columns', args=[dataset.pk]))

            map_res = self.client.post(
                reverse('datasets:map_columns', args=[dataset.pk]),
                {
                    'date_column': 'sale_date',
                    'product_column': 'product_name',
                    'quantity_column': 'units_sold',
                    'revenue_column': 'sales_revenue',
                },
            )
            self.assertRedirects(map_res, reverse('datasets:detail', args=[dataset.pk]))
            dataset.refresh_from_db()
            self.assertTrue(dataset.is_mapped)
            self.assertEqual(dataset.profile_summary['unique_products'], 4)

            original_size_before = dataset.original_file.size

            # Clean data -> creates separate cleaned CSV and keeps original intact
            clean_res = self.client.post(
                reverse('datasets:cleaning'),
                {
                    'dataset': dataset.pk,
                    'remove_duplicates': 'on',
                    'remove_invalid_dates': 'on',
                    'remove_negative_values': 'on',
                    'missing_strategy': 'drop',
                    'aggregate_daily': 'on',
                    'action': 'apply',
                },
            )
            self.assertRedirects(clean_res, reverse('datasets:detail', args=[dataset.pk]))
            dataset.refresh_from_db()
            self.assertTrue(dataset.has_cleaned_version)
            self.assertEqual(dataset.original_file.size, original_size_before)
            self.assertGreater(len(dataset.cleaning_log), 0)

            # User B cannot access or delete User A's dataset
            self.client.logout()
            self.client.login(username='user_b', password='StrongPassword!123')
            self.assertEqual(self.client.get(reverse('datasets:detail', args=[dataset.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse('datasets:delete', args=[dataset.pk])).status_code, 404)


class MultiFormatIngestionUnitTests(TestCase):
    """Unit tests covering XLSX, XLS, JSON, security validation, and edge cases."""

    def test_reject_unsupported_and_malicious_files(self):
        for fname in ('macro.xlsm', 'stream.jsonl', 'notes.txt', 'binary.exe'):
            up = SimpleUploadedFile(fname, b'col1,col2\n1,2\n')
            ok, err, _ = validate_uploaded_dataset(up)
            self.assertFalse(ok)
            self.assertIn('Unsupported', err)

        # CSV content disguised with .xlsx extension
        spoofed_xlsx = SimpleUploadedFile('fake.xlsx', b'date,value\n2025-01-01,10\n')
        ok, err, fmt = validate_uploaded_dataset(spoofed_xlsx)
        self.assertFalse(ok)
        self.assertEqual(fmt, 'xlsx')
        self.assertIn('signature', err.lower())

        # HTML disguised with .xls extension
        spoofed_xls = SimpleUploadedFile('fake.xls', b'<html><body>Not XLS</body></html>')
        ok, err, fmt = validate_uploaded_dataset(spoofed_xls)
        self.assertFalse(ok)
        self.assertEqual(fmt, 'xls')
        self.assertIn('signature', err.lower())

        # Oversized upload rejection (tight threshold)
        xlsx_bytes = build_test_xlsx_bytes([('Data', [['a', 'b'], [1, 2]])])
        oversized = SimpleUploadedFile('big.xlsx', xlsx_bytes)
        ok, err, _ = validate_uploaded_dataset(oversized, max_size_bytes=100)
        self.assertFalse(ok)
        self.assertIn('exceeds', err.lower())

        # Filename sanitization strips path traversal
        self.assertEqual(sanitize_original_filename('../../etc/evil file.xlsx'), 'evil_file.xlsx')

    def test_xlsx_security_and_formula_header_edge_cases(self):
        # Encrypted OLE2 container disguised as .xlsx
        enc_xlsx = SimpleUploadedFile('encrypted.xlsx', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 64)
        ok, err, _ = validate_uploaded_dataset(enc_xlsx)
        self.assertFalse(ok)
        self.assertIn('encrypted', err.lower())

        # Zip archive with path traversal entry
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w') as zf:
            zf.writestr('../escape.txt', 'bad')
            zf.writestr('[Content_Types].xml', '<Types/>')
            zf.writestr('xl/workbook.xml', '<workbook/>')
        ok, err, _ = validate_uploaded_dataset(SimpleUploadedFile('evil.xlsx', zip_buf.getvalue()))
        self.assertFalse(ok)
        self.assertIn('unsafe', err.lower())

        # Macro project inside .xlsx (renamed .xlsm)
        macro_buf = io.BytesIO()
        with zipfile.ZipFile(macro_buf, 'w') as zf:
            zf.writestr('[Content_Types].xml', '<Types/>')
            zf.writestr('xl/workbook.xml', '<workbook/>')
            zf.writestr('xl/vbaProject.bin', b'VBA')
        ok, err, _ = validate_uploaded_dataset(SimpleUploadedFile('renamed.xlsx', macro_buf.getvalue()))
        self.assertFalse(ok)
        self.assertIn('macro', err.lower())

        # Empty workbook rejected cleanly
        empty_xlsx = build_test_xlsx_bytes([('EmptySheet', [])])
        ok, err, _ = validate_uploaded_dataset(SimpleUploadedFile('empty.xlsx', empty_xlsx))
        self.assertFalse(ok)
        self.assertIn('usable worksheet', err.lower())

        # Duplicate headers, blank headers, native date objects, and uncached formula warning
        edge_xlsx = build_test_xlsx_bytes([
            (
                'Orders',
                [
                    ['Date', 'Metric', 'Metric', None],
                    [dt.date(2025, 1, 1), 100, '=B2*2', 'North'],
                    [dt.date(2025, 1, 2), None, 220, 'South'],
                ],
            )
        ])
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            tmp.write(edge_xlsx)
            path = tmp.name
        try:
            df, notes = read_excel_file(path, sheet_name='Orders', source_format='xlsx')
            self.assertEqual(list(df.columns), ['Date', 'Metric', 'Metric_2', 'Unnamed_Col_4'])
            self.assertEqual(df.iloc[0]['Date'], '2025-01-01')
            joined_notes = ' '.join(notes).lower()
            self.assertIn('duplicate', joined_notes)
            self.assertIn('blank header', joined_notes)
            self.assertIn('formula', joined_notes)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_xls_inspection_and_reading(self):
        xls_bytes = build_test_xls_bytes([
            ('Sales', [['date', 'product', 'revenue'], ['2025-01-01', 'Alpha', 100.0], ['2025-01-02', 'Beta', 200.0]]),
            ('Expenses', [['date', 'category', 'amount'], ['2025-01-01', 'Cloud', 50.0], ['2025-01-02', 'Ads', 75.0]]),
        ])
        ok, err, fmt = validate_uploaded_dataset(SimpleUploadedFile('legacy.xls', xls_bytes))
        self.assertTrue(ok, err)
        self.assertEqual(fmt, 'xls')

        with tempfile.NamedTemporaryFile(suffix='.xls', delete=False) as tmp:
            tmp.write(xls_bytes)
            path = tmp.name
        try:
            sheets = inspect_excel_sheets(path, source_format='xls')
            self.assertEqual([s['name'] for s in sheets], ['Sales', 'Expenses'])
            df, _ = read_excel_file(path, sheet_name='Expenses', source_format='xls')
            self.assertEqual(list(df.columns), ['date', 'category', 'amount'])
            self.assertEqual(float(df.iloc[1]['amount']), 75.0)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_json_validation_flattening_and_candidate_detection(self):
        # Empty / malformed / unsupported JSON root rejected
        for name, raw in (
            ('empty.json', b'[]'),
            ('bad.json', b'{bad json'),
            ('scalar.json', b'"just a string"'),
            ('scalars.json', b'[1, 2, 3, 4]'),
        ):
            ok, _, fmt = validate_uploaded_dataset(SimpleUploadedFile(name, raw))
            self.assertFalse(ok)
            self.assertEqual(fmt, 'json')

        # Nested object flattening + nested list serialization + inconsistent keys + Unicode
        nested_payload = {
            'metadata': {'source': 'Analytics API'},
            'records': [
                {
                    'date': '2025-01-01',
                    'metrics': {'visitors': 420, 'conversions': 18},
                    'tags': ['organic', 'search'],
                    'region': 'Café München',
                },
                {
                    'date': '2025-01-02',
                    'metrics': {'visitors': 510},
                    'region': None,
                },
            ],
        }
        raw_json = json.dumps(nested_payload, ensure_ascii=False).encode('utf-8')
        ok, err, _ = validate_uploaded_dataset(SimpleUploadedFile('traffic.json', raw_json))
        self.assertTrue(ok, err)

        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmp:
            tmp.write(raw_json)
            path = tmp.name
        try:
            cands = inspect_json_candidates(path)
            usable = [c for c in cands if c['is_usable']]
            self.assertEqual(len(usable), 1)
            self.assertEqual(usable[0]['path'], 'records')
            df, notes = read_json_file(path, json_path='records')
            self.assertIn('metrics.visitors', df.columns)
            self.assertIn('tags', df.columns)
            self.assertEqual(df.iloc[0]['region'], 'Café München')
            self.assertTrue(any('flattened' in n.lower() for n in notes))
        finally:
            if os.path.exists(path):
                os.remove(path)

        # Single JSON object converted to 1-row table
        single_obj = json.dumps({'name': 'Single', 'score': 99.5, 'status': 'ok'}).encode('utf-8')
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmp2:
            tmp2.write(single_obj)
            path2 = tmp2.name
        try:
            df_single, _ = read_json_file(path2, json_path='$')
            self.assertEqual(len(df_single), 1)
            self.assertEqual(float(df_single.iloc[0]['score']), 99.5)
        finally:
            if os.path.exists(path2):
                os.remove(path2)


class MultiFormatEndToEndIntegrationTests(TestCase):
    """End-to-end Django workflow tests for XLSX, XLS, and JSON through forecasting & PDF."""

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.user = User.objects.create_user(username='ml_user', password='StrongPassword!123')
        self.other_user = User.objects.create_user(username='other_user', password='StrongPassword!123')

    def tearDown(self):
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def _timeseries_rows(self, days=40):
        start = dt.date(2025, 1, 1)
        rows = [['Date', 'Segment', 'Units', 'Revenue']]
        for i in range(days):
            d = (start + dt.timedelta(days=i)).isoformat()
            seg = 'Online' if i % 2 == 0 else 'Retail'
            units = 20 + (i % 7) * 3
            rev = round(units * 25.5 + i * 2.0, 2)
            rows.append([d, seg, units, rev])
        return rows

    def _verify_downstream_pipeline(self, dataset):
        """Exercise Explorer, Cleaning, Analytics, Visualizations, Anomalies, Forecasting, and PDF Report."""
        exp_res = self.client.get(reverse('datasets:explorer') + f'?dataset={dataset.pk}')
        self.assertEqual(exp_res.status_code, 200)

        clean_res = self.client.post(
            reverse('datasets:cleaning'),
            {
                'dataset': dataset.pk,
                'remove_duplicates': 'on',
                'remove_invalid_dates': 'on',
                'missing_strategy': 'drop',
                'categorical_missing_strategy': 'fill_unknown',
                'action': 'apply',
            },
        )
        self.assertRedirects(clean_res, reverse('datasets:detail', args=[dataset.pk]))
        dataset.refresh_from_db()
        self.assertTrue(dataset.has_cleaned_version)

        self.assertEqual(
            self.client.get(reverse('analytics:analysis') + f'?dataset={dataset.pk}').status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse('analytics:visualizations') + f'?dataset={dataset.pk}').status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse('analytics:anomalies') + f'?dataset={dataset.pk}').status_code, 200
        )

        fc_res = self.client.post(
            reverse('forecasting:home'),
            {
                'dataset': dataset.pk,
                'dataset_version': 'cleaned',
                'product_name': '__ALL__',
                'target_metric': 'Revenue',
                'aggregation_freq': 'D',
                'model_name': 'linear_regression',
                'preprocessing_method': 'interpolate',
                'test_size_ratio': 0.2,
                'forecast_horizon': 7,
                'random_seed': 42,
            },
        )
        self.assertEqual(fc_res.status_code, 200)
        self.assertTrue(dataset.forecast_experiments.exists())

        rep_res = self.client.post(reverse('reports:list'), {'dataset_id': dataset.pk})
        self.assertRedirects(rep_res, reverse('reports:list'))
        report_obj = GeneratedReport.objects.filter(dataset=dataset).first()
        self.assertIsNotNone(report_obj)
        dl_res = self.client.get(reverse('reports:download', args=[report_obj.pk]))
        self.assertEqual(dl_res.status_code, 200)
        dl_res.close()

    def test_single_sheet_xlsx_full_pipeline_and_cleanup(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            self.client.login(username='ml_user', password='StrongPassword!123')
            xlsx_bytes = build_test_xlsx_bytes([('RevenueData', self._timeseries_rows(38))])
            up = SimpleUploadedFile('sales_q1.xlsx', xlsx_bytes)
            res = self.client.post(
                reverse('datasets:upload'),
                {'name': 'XLSX Single Sheet', 'original_file': up},
            )
            dataset = SalesDataset.objects.get(name='XLSX Single Sheet')
            self.assertEqual(dataset.source_format, 'xlsx')
            self.assertEqual(dataset.selected_sheet, 'RevenueData')
            self.assertTrue(bool(dataset.normalized_file))
            self.assertRedirects(res, reverse('datasets:map_columns', args=[dataset.pk]))

            map_res = self.client.post(
                reverse('datasets:map_columns', args=[dataset.pk]),
                {
                    'dataset_type': 'sales',
                    'date_column': 'Date',
                    'product_column': 'Segment',
                    'revenue_column': 'Revenue',
                    'quantity_column': 'Units',
                },
            )
            self.assertRedirects(map_res, reverse('datasets:detail', args=[dataset.pk]))
            self._verify_downstream_pipeline(dataset)

            orig_path = dataset.original_file.path
            norm_path = dataset.normalized_file.path
            clean_path = dataset.cleaned_file.path
            self.client.post(reverse('datasets:delete', args=[dataset.pk]))
            self.assertFalse(os.path.exists(orig_path))
            self.assertFalse(os.path.exists(norm_path))
            self.assertFalse(os.path.exists(clean_path))

    def test_multisheet_xlsx_and_ownership_isolation(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            self.client.login(username='ml_user', password='StrongPassword!123')
            multi_bytes = build_test_xlsx_bytes([
                ('Summary', [['Metric', 'Val'], ['Total', 100]]),
                ('Transactions', self._timeseries_rows(32)),
            ])
            up = SimpleUploadedFile('multi_workbook.xlsx', multi_bytes)
            res = self.client.post(
                reverse('datasets:upload'),
                {'name': 'Multi Sheet XLSX', 'original_file': up},
            )
            dataset = SalesDataset.objects.get(name='Multi Sheet XLSX')
            self.assertRedirects(res, reverse('datasets:select_sheet', args=[dataset.pk]))

            # Direct access to map_columns redirects back until sheet selected
            self.assertRedirects(
                self.client.get(reverse('datasets:map_columns', args=[dataset.pk])),
                reverse('datasets:select_sheet', args=[dataset.pk]),
            )

            # Other user cannot access sheet selection endpoint
            self.client.logout()
            self.client.login(username='other_user', password='StrongPassword!123')
            self.assertEqual(
                self.client.get(reverse('datasets:select_sheet', args=[dataset.pk])).status_code, 404
            )

            # Owner selects 'Transactions'
            self.client.logout()
            self.client.login(username='ml_user', password='StrongPassword!123')
            sel_res = self.client.post(
                reverse('datasets:select_sheet', args=[dataset.pk]),
                {'sheet_name': 'Transactions'},
            )
            self.assertRedirects(sel_res, reverse('datasets:map_columns', args=[dataset.pk]))
            dataset.refresh_from_db()
            self.assertEqual(dataset.selected_sheet, 'Transactions')
            self.assertTrue(bool(dataset.normalized_file))

    def test_multisheet_xls_and_multiarray_json_full_pipeline(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            self.client.login(username='ml_user', password='StrongPassword!123')

            # 1. Multi-sheet legacy XLS
            xls_bytes = build_test_xls_bytes([
                ('Meta', [['Key', 'Val'], ['Owner', 'Lab']]),
                ('DailyMetrics', self._timeseries_rows(36)),
            ])
            xls_up = SimpleUploadedFile('legacy_metrics.xls', xls_bytes)
            res = self.client.post(
                reverse('datasets:upload'),
                {'name': 'Legacy XLS Sales', 'original_file': xls_up},
            )
            xls_ds = SalesDataset.objects.get(name='Legacy XLS Sales')
            self.assertRedirects(res, reverse('datasets:select_sheet', args=[xls_ds.pk]))
            sel_res = self.client.post(
                reverse('datasets:select_sheet', args=[xls_ds.pk]),
                {'sheet_name': 'DailyMetrics'},
            )
            self.assertRedirects(sel_res, reverse('datasets:map_columns', args=[xls_ds.pk]))
            self.client.post(
                reverse('datasets:map_columns', args=[xls_ds.pk]),
                {
                    'dataset_type': 'sales',
                    'date_column': 'Date',
                    'product_column': 'Segment',
                    'revenue_column': 'Revenue',
                    'quantity_column': 'Units',
                },
            )
            self._verify_downstream_pipeline(xls_ds)

            # 2. Multi-array JSON with nested objects
            rows = self._timeseries_rows(36)
            headers = rows[0]
            json_records = [
                {
                    'Date': r[0],
                    'Segment': r[1],
                    'Units': r[2],
                    'Revenue': r[3],
                }
                for r in rows[1:]
            ]
            multi_json = json.dumps({
                'summary': [{'label': 'Q1', 'count': 1}],
                'timeseries': json_records,
            }).encode('utf-8')
            json_up = SimpleUploadedFile('api_export.json', multi_json)
            jres = self.client.post(
                reverse('datasets:upload'),
                {'name': 'API JSON Sales', 'original_file': json_up},
            )
            json_ds = SalesDataset.objects.get(name='API JSON Sales')
            self.assertRedirects(jres, reverse('datasets:select_json', args=[json_ds.pk]))
            self.client.post(
                reverse('datasets:select_json', args=[json_ds.pk]),
                {'json_path': 'timeseries'},
            )
            self.client.post(
                reverse('datasets:map_columns', args=[json_ds.pk]),
                {
                    'dataset_type': 'sales',
                    'date_column': 'Date',
                    'product_column': 'Segment',
                    'revenue_column': 'Revenue',
                    'quantity_column': 'Units',
                },
            )
            self._verify_downstream_pipeline(json_ds)
