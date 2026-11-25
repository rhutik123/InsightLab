"""
Database model for saving reproducible forecasting runs and research experiments.
"""

from django.contrib.auth.models import User
from django.db import models

from datasets.models import SalesDataset


class ForecastExperiment(models.Model):
    """Stores configuration, chronological split metadata, and evaluation metrics for a forecast."""

    MODEL_CHOICES = [
        ('naive', 'Naive Forecast (Baseline)'),
        ('moving_average', 'Moving Average (7-Period)'),
        ('linear_regression', 'Linear Regression (Lag + Calendar)'),
        ('random_forest', 'Random Forest Regressor'),
        ('arima', 'ARIMA (Statsmodels)'),
    ]

    EXPERIMENT_TYPE_CHOICES = [
        ('forecast', 'Single Forecast / Model Comparison'),
        ('algorithm_comparison', 'Algorithm Comparison'),
        ('dataset_size', 'Effect of Historical Dataset Size'),
        ('seasonality', 'Effect of Seasonality Features'),
        ('missing_data', 'Effect of Missing Data & Imputation'),
        ('volatility', 'Effect of Sales Fluctuations'),
        ('product_comparison', 'Product-Wise Forecasting Comparison'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='forecast_experiments')
    dataset = models.ForeignKey(
        SalesDataset, on_delete=models.CASCADE, related_name='forecast_experiments'
    )
    dataset_version = models.CharField(max_length=20, default='cleaned')
    product_name = models.CharField(max_length=150, default='__ALL__')
    target_metric = models.CharField(max_length=20, default='revenue')
    aggregation_freq = models.CharField(max_length=5, default='D')
    model_name = models.CharField(max_length=40, choices=MODEL_CHOICES)
    preprocessing_method = models.CharField(max_length=30, default='zero_fill')

    test_size_ratio = models.FloatField(default=0.2)
    forecast_horizon = models.PositiveIntegerField(default=14)
    random_seed = models.IntegerField(default=42)

    train_start_date = models.DateField(null=True, blank=True)
    train_end_date = models.DateField(null=True, blank=True)
    test_start_date = models.DateField(null=True, blank=True)
    test_end_date = models.DateField(null=True, blank=True)
    train_rows = models.PositiveIntegerField(default=0)
    test_rows = models.PositiveIntegerField(default=0)

    mae = models.FloatField(null=True, blank=True)
    rmse = models.FloatField(null=True, blank=True)
    mape = models.FloatField(null=True, blank=True)
    r2 = models.FloatField(null=True, blank=True)
    mape_note = models.CharField(max_length=255, blank=True)

    experiment_type = models.CharField(
        max_length=40, choices=EXPERIMENT_TYPE_CHOICES, default='forecast'
    )
    experiment_group = models.CharField(max_length=64, blank=True, db_index=True)
    experiment_label = models.CharField(max_length=150, blank=True)

    results_json = models.JSONField(default=dict, blank=True)
    insights_json = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        product_display = 'All Products' if self.product_name == '__ALL__' else self.product_name
        return f'{self.get_model_name_display()} — {product_display} ({self.dataset.name})'

    @property
    def product_display(self):
        return 'All Records (Aggregated)' if self.product_name == '__ALL__' else self.product_name
