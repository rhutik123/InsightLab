"""
Automated tests for Exploratory Data Analysis (EDA), anomaly detection,
and Business Decision Support insights.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from datasets.data_processing import (
    detect_column_schema,
    generate_sample_expenses_csv_bytes,
    generate_sample_sales_csv_bytes,
    generate_sample_traffic_csv_bytes,
    load_general_dataframe,
    load_standardized_dataframe,
    profile_general_dataset,
)
from datasets.models import SalesDataset

from .analysis import build_custom_visualization_data, compute_eda, compute_general_eda, detect_anomalies, detect_sales_anomalies
from .decision_support import generate_business_insights


class AnalyticsModuleTests(TestCase):
    """Verify EDA sufficiency rules, Z-score anomaly detection, and decision insights."""

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.user = User.objects.create_user(username='analyst1', password='StrongPassword!123')

    def tearDown(self):
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_eda_anomalies_and_insights_on_sample_data(self):
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
            tmp.write(generate_sample_sales_csv_bytes())
            tmp_path = tmp.name

        try:
            df = load_standardized_dataframe(
                tmp_path,
                {
                    'date': 'sale_date',
                    'product': 'product_name',
                    'quantity': 'units_sold',
                    'revenue': 'sales_revenue',
                },
            )
            eda = compute_eda(df)
            self.assertTrue(eda['has_data'])
            self.assertTrue(eda['weekly_trend']['available'])
            self.assertTrue(eda['monthly_trend']['available'])
            # Sample data is 120 days in 2025 -> yearly trend must NOT be fabricated
            self.assertFalse(eda['yearly_trend']['available'])
            self.assertTrue(eda['seasonal_decomposition']['available'])

            # Wireless Mouse has an injected 3x spike on day 75
            anom = detect_sales_anomalies(df, product_filter='Wireless Mouse', window=7, z_threshold=2.0)
            self.assertTrue(anom['has_data'])
            self.assertGreaterEqual(anom['anomaly_count'], 1)
            self.assertIn('statistical observation', anom['anomalies'][0]['observation_note'].lower())

            insights = generate_business_insights(
                df.groupby('date')['revenue'].sum(),
                [1500.0] * 7,
                target_col='revenue',
                product_label='All Products',
                anomalies=anom['anomalies'],
            )
            self.assertGreater(len(insights), 0)
            combined_text = ' '.join(i['detail'].lower() for i in insights)
            self.assertIn('demand-planning suggestion', combined_text)
        finally:
            import os
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_analytics_views_render_with_dataset(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            csv_file = SimpleUploadedFile(
                'sample.csv', generate_sample_sales_csv_bytes(), content_type='text/csv'
            )
            ds = SalesDataset.objects.create(
                user=self.user,
                name='Sample Analytics',
                original_file=csv_file,
                date_column='sale_date',
                product_column='product_name',
                quantity_column='units_sold',
                revenue_column='sales_revenue',
                is_mapped=True,
                profile_summary={'total_revenue': 10000, 'total_quantity': 500, 'unique_products': 4},
            )
            self.client.login(username='analyst1', password='StrongPassword!123')
            self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
            self.assertEqual(
                self.client.get(reverse('analytics:analysis') + f'?dataset={ds.pk}').status_code, 200
            )
            self.assertEqual(
                self.client.get(reverse('analytics:anomalies') + f'?dataset={ds.pk}').status_code, 200
            )
            self.assertEqual(
                self.client.get(reverse('analytics:visualizations') + f'?dataset={ds.pk}').status_code, 200
            )

    def test_general_analytics_and_cross_sectional_anomalies_on_non_sales_datasets(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            # Website Traffic dataset (Date, Traffic Source, Visitors, Page Views)
            traffic_file = SimpleUploadedFile(
                'traffic.csv', generate_sample_traffic_csv_bytes(), content_type='text/csv'
            )
            ds_traffic = SalesDataset.objects.create(
                user=self.user,
                name='Web Traffic Q1',
                dataset_type='traffic',
                original_file=traffic_file,
                date_column='Date',
                product_column='Traffic Source',
                revenue_column='Visitors',
                quantity_column='Page Views',
                is_mapped=True,
            )
            df_t = load_general_dataframe(ds_traffic.original_file.path, {})
            schema_t = detect_column_schema(df_t)
            ds_traffic.column_schema = schema_t
            ds_traffic.profile_summary = profile_general_dataset(ds_traffic.original_file.path, schema_t)
            ds_traffic.save()

            eda_t = compute_general_eda(
                df_t, date_col='Date', target_col='Visitors', group_col='Traffic Source', secondary_col='Page Views'
            )
            self.assertTrue(eda_t['has_data'])
            self.assertTrue(eda_t['has_date'])
            self.assertGreater(len(eda_t['correlations']), 0)

            anom_t = detect_anomalies(
                df_t, target_col='Visitors', date_col='Date', group_col='Traffic Source', group_filter='Social Media'
            )
            self.assertGreaterEqual(anom_t['anomaly_count'], 1)

            viz_scatter = build_custom_visualization_data(df_t, 'scatter', 'Visitors', 'Page Views')
            self.assertTrue(viz_scatter['has_chart'])
            self.assertEqual(viz_scatter['chart_type'], 'scatter')

            # Cross-sectional dataset without a date column
            tabular_file = SimpleUploadedFile(
                'metrics.csv',
                b'Segment,Score,Latency\nA,10,120\nB,12,115\nA,11,118\nB,13,121\nA,12,119\nB,99,450\n',
                content_type='text/csv',
            )
            ds_tab = SalesDataset.objects.create(
                user=self.user,
                name='No Date Metrics',
                original_file=tabular_file,
                revenue_column='Score',
                product_column='Segment',
                is_mapped=True,
            )
            self.client.login(username='analyst1', password='StrongPassword!123')
            self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
            self.assertEqual(self.client.get(reverse('datasets:explorer') + f'?dataset={ds_tab.pk}').status_code, 200)
            self.assertEqual(self.client.get(reverse('analytics:anomalies') + f'?dataset={ds_tab.pk}').status_code, 200)
