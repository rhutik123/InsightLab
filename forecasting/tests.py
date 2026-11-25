"""
Automated tests for chronological splitting, evaluation metrics (including zero actuals),
all 5 forecasting models, and the 6 Experiment Lab protocols.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from datasets.data_processing import (
    generate_sample_expenses_csv_bytes,
    generate_sample_sales_csv_bytes,
    generate_sample_traffic_csv_bytes,
    load_standardized_dataframe,
)
from datasets.models import SalesDataset

from .evaluation import evaluate_predictions
from .forecasting_models import run_forecast_experiment
from .models import ForecastExperiment
from .preprocessing import chronological_split, prepare_time_series
from .research import ALL_MODELS, run_research_experiment_suite


class ForecastingEngineUnitTests(TestCase):
    """Unit tests for preprocessing, evaluation, models, and research experiments."""

    def setUp(self):
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
            tmp.write(generate_sample_sales_csv_bytes())
            self.tmp_path = tmp.name

        self.df = load_standardized_dataframe(
            self.tmp_path,
            {
                'date': 'sale_date',
                'product': 'product_name',
                'quantity': 'units_sold',
                'revenue': 'sales_revenue',
            },
        )
        # Filter negative test row for clean time-series unit tests
        self.df = self.df[(self.df['quantity'] >= 0) & (self.df['revenue'] >= 0)]

    def tearDown(self):
        import os
        if os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)

    def test_chronological_split_never_shuffles(self):
        series, _ = prepare_time_series(self.df, '__ALL__', 'revenue', 'D', 'zero_fill')
        train, test = chronological_split(series, test_ratio=0.2)
        self.assertLess(train.index.max(), test.index.min())
        self.assertEqual(len(train) + len(test), len(series))

    def test_mape_handles_zero_actuals_safely(self):
        all_zero = evaluate_predictions([0.0, 0.0, 0.0], [1.0, 2.0, 3.0])
        self.assertIsNone(all_zero['mape'])
        self.assertIn('zero', all_zero['mape_note'].lower())

        partial_zero = evaluate_predictions([0.0, 100.0, 200.0], [10.0, 110.0, 180.0])
        self.assertIsNotNone(partial_zero['mape'])
        self.assertIn('Excluded 1', partial_zero['mape_note'])

    def test_all_five_forecasting_models_execute(self):
        series, _ = prepare_time_series(self.df, '__ALL__', 'revenue', 'D', 'zero_fill')
        for model_name in ALL_MODELS:
            res = run_forecast_experiment(
                series, model_name, freq='D', test_ratio=0.2, forecast_horizon=10, random_seed=42
            )
            self.assertEqual(len(res['results']['future_predictions']), 10)
            self.assertTrue(all(v >= 0 for v in res['results']['future_predictions']))
            self.assertIsNotNone(res['metrics']['mae'])
            self.assertIsNotNone(res['metrics']['rmse'])

    def test_all_six_research_experiment_suites(self):
        for exp_type in (
            'algorithm_comparison',
            'dataset_size',
            'seasonality',
            'missing_data',
            'volatility',
            'product_comparison',
        ):
            records, note = run_research_experiment_suite(
                self.df,
                experiment_type=exp_type,
                product_name='__ALL__',
                model_name='linear_regression',
                random_seed=42,
            )
            self.assertGreater(len(records), 0, f'Empty records for {exp_type}')
            self.assertTrue(bool(note))


class ForecastingViewsIntegrationTests(TestCase):
    """Integration tests for forecasting and Experiment Lab views."""

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.user = User.objects.create_user(username='ml_student', password='StrongPassword!123')

    def tearDown(self):
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_compare_all_forecast_and_experiment_lab(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            csv_file = SimpleUploadedFile(
                'sample.csv', generate_sample_sales_csv_bytes(), content_type='text/csv'
            )
            ds = SalesDataset.objects.create(
                user=self.user,
                name='ML Test Dataset',
                original_file=csv_file,
                date_column='sale_date',
                product_column='product_name',
                quantity_column='units_sold',
                revenue_column='sales_revenue',
                is_mapped=True,
            )
            self.client.login(username='ml_student', password='StrongPassword!123')

            res = self.client.post(
                reverse('forecasting:home'),
                {
                    'dataset': ds.pk,
                    'dataset_version': 'original',
                    'product_name': '__ALL__',
                    'target_metric': 'revenue',
                    'aggregation_freq': 'D',
                    'model_name': 'compare_all',
                    'preprocessing_method': 'zero_fill',
                    'test_size_ratio': '0.2',
                    'forecast_horizon': '7',
                    'random_seed': '42',
                },
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(ForecastExperiment.objects.filter(user=self.user).count(), 5)

            lab_res = self.client.post(
                reverse('forecasting:experiments'),
                {
                    'dataset': ds.pk,
                    'experiment_type': 'dataset_size',
                    'model_name': 'linear_regression',
                    'product_name': '__ALL__',
                    'target_metric': 'revenue',
                    'aggregation_freq': 'D',
                    'preprocessing_method': 'zero_fill',
                    'forecast_horizon': '7',
                    'random_seed': '42',
                },
            )
            self.assertEqual(lab_res.status_code, 200)
            self.assertEqual(
                ForecastExperiment.objects.filter(user=self.user, experiment_type='dataset_size').count(),
                4,
            )
            self.assertEqual(self.client.get(reverse('forecasting:compare')).status_code, 200)

    def test_generalized_forecasting_on_traffic_expenses_and_non_date_validation(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            self.client.login(username='ml_student', password='StrongPassword!123')

            # 1. Website Traffic dataset forecasting Visitors
            t_file = SimpleUploadedFile('traffic.csv', generate_sample_traffic_csv_bytes(), content_type='text/csv')
            ds_traffic = SalesDataset.objects.create(
                user=self.user,
                name='Website Traffic',
                dataset_type='traffic',
                original_file=t_file,
                date_column='Date',
                product_column='Traffic Source',
                revenue_column='Visitors',
                quantity_column='Page Views',
                is_mapped=True,
                column_schema={
                    'primary_date_column': 'Date',
                    'primary_group_column': 'Traffic Source',
                    'primary_target_column': 'Visitors',
                    'numeric_columns': ['Visitors', 'Page Views'],
                },
            )
            res_t = self.client.post(
                reverse('forecasting:home'),
                {
                    'dataset': ds_traffic.pk,
                    'dataset_version': 'original',
                    'product_name': 'Organic Search',
                    'target_metric': 'Visitors',
                    'aggregation_freq': 'D',
                    'model_name': 'linear_regression',
                    'preprocessing_method': 'zero_fill',
                    'test_size_ratio': '0.2',
                    'forecast_horizon': '7',
                    'random_seed': '42',
                },
            )
            self.assertEqual(res_t.status_code, 200)
            self.assertTrue(
                ForecastExperiment.objects.filter(
                    user=self.user, dataset=ds_traffic, target_metric='Visitors', product_name='Organic Search'
                ).exists()
            )

            # 2. Business Expenses dataset forecasting Amount
            e_file = SimpleUploadedFile('expenses.csv', generate_sample_expenses_csv_bytes(), content_type='text/csv')
            ds_exp = SalesDataset.objects.create(
                user=self.user,
                name='Expenses',
                dataset_type='finance',
                original_file=e_file,
                date_column='Transaction Date',
                product_column='Category',
                revenue_column='Amount',
                is_mapped=True,
                column_schema={
                    'primary_date_column': 'Transaction Date',
                    'primary_group_column': 'Category',
                    'primary_target_column': 'Amount',
                    'numeric_columns': ['Amount'],
                },
            )
            res_e = self.client.post(
                reverse('forecasting:home'),
                {
                    'dataset': ds_exp.pk,
                    'dataset_version': 'original',
                    'product_name': '__ALL__',
                    'target_metric': 'Amount',
                    'aggregation_freq': 'D',
                    'model_name': 'random_forest',
                    'preprocessing_method': 'zero_fill',
                    'test_size_ratio': '0.2',
                    'forecast_horizon': '5',
                    'random_seed': '42',
                },
            )
            self.assertEqual(res_e.status_code, 200)
            self.assertTrue(
                ForecastExperiment.objects.filter(user=self.user, dataset=ds_exp, target_metric='Amount').exists()
            )

            # 3. Non-date dataset returns clean validation message instead of crashing
            tab_file = SimpleUploadedFile('nodate.csv', b'Category,Score\nA,10\nB,20\n', content_type='text/csv')
            ds_tab = SalesDataset.objects.create(
                user=self.user,
                name='Tabular Only',
                original_file=tab_file,
                product_column='Category',
                revenue_column='Score',
                is_mapped=True,
                column_schema={'primary_date_column': '', 'primary_target_column': 'Score', 'numeric_columns': ['Score']},
            )
            res_tab = self.client.post(
                reverse('forecasting:home'),
                {
                    'dataset': ds_tab.pk,
                    'dataset_version': 'original',
                    'product_name': '__ALL__',
                    'target_metric': 'Score',
                    'aggregation_freq': 'D',
                    'model_name': 'naive',
                    'preprocessing_method': 'zero_fill',
                    'test_size_ratio': '0.2',
                    'forecast_horizon': '5',
                    'random_seed': '42',
                },
            )
            self.assertEqual(res_tab.status_code, 200)
            self.assertContains(res_tab, 'does not have a mapped Date/Time column')
