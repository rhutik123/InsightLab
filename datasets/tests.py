"""
Automated tests for dataset upload validation, column mapping, profiling, cleaning,
and user ownership isolation.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .data_processing import (
    clean_dataset_dataframe,
    clean_general_dataframe,
    detect_column_schema,
    generate_sample_expenses_csv_bytes,
    generate_sample_sales_csv_bytes,
    generate_sample_traffic_csv_bytes,
    profile_dataset,
    profile_general_dataset,
    read_raw_csv,
    validate_csv_upload,
)
from .models import SalesDataset


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
