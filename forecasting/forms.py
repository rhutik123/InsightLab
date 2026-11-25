"""
Forms for configuring time-series forecasting runs and controlled research experiments.
Supports arbitrary numerical target columns and optional categorical segments.
"""

from django import forms

from accounts.forms import INPUT_CLASS
from datasets.forms import SELECT_CLASS
from datasets.models import SalesDataset


class ForecastRunForm(forms.Form):
    """Configuration form for single-model or 5-model time-series forecasting."""

    MODEL_CHOICES = [
        ('compare_all', 'Compare All 5 Models (Same Test Split)'),
        ('naive', 'Naive Forecast (Baseline)'),
        ('moving_average', 'Moving Average (7-Period)'),
        ('linear_regression', 'Linear Regression'),
        ('random_forest', 'Random Forest Regressor'),
        ('arima', 'ARIMA (Statsmodels)'),
    ]

    FREQ_CHOICES = [
        ('D', 'Daily (Recommended)'),
        ('W', 'Weekly'),
        ('M', 'Monthly'),
    ]

    MISSING_CHOICES = [
        ('zero_fill', 'Fill missing dates with 0'),
        ('ffill', 'Forward-fill missing dates'),
        ('interpolate', 'Linear interpolation'),
    ]

    VERSION_CHOICES = [
        ('cleaned', 'Cleaned (if available)'),
        ('original', 'Original Uploaded'),
    ]

    dataset = forms.ModelChoiceField(queryset=SalesDataset.objects.none(), label='Dataset')
    dataset_version = forms.ChoiceField(choices=VERSION_CHOICES, initial='cleaned', label='Data Version')
    product_name = forms.CharField(
        required=False,
        initial='__ALL__',
        label='Segment / Group Filter (__ALL__ for total)',
    )
    target_metric = forms.CharField(
        required=False,
        initial='',
        label='Numerical Target Column',
    )
    aggregation_freq = forms.ChoiceField(choices=FREQ_CHOICES, initial='D', label='Frequency')
    model_name = forms.ChoiceField(choices=MODEL_CHOICES, initial='compare_all', label='Forecasting Model')
    preprocessing_method = forms.ChoiceField(
        choices=MISSING_CHOICES, initial='zero_fill', label='Missing Date Handling'
    )
    test_size_ratio = forms.FloatField(
        min_value=0.1, max_value=0.4, initial=0.2, label='Chronological Test Ratio (0.10–0.40)'
    )
    forecast_horizon = forms.IntegerField(
        min_value=1, max_value=90, initial=14, label='Forecast Horizon (Periods)'
    )
    random_seed = forms.IntegerField(min_value=0, max_value=99999, initial=42, label='Random Seed')

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dataset'].queryset = SalesDataset.objects.filter(user=user, is_mapped=True)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = SELECT_CLASS
            else:
                field.widget.attrs['class'] = INPUT_CLASS


class ResearchExperimentForm(forms.Form):
    """Configuration form for MSc IT controlled research experiments."""

    EXPERIMENT_TYPE_CHOICES = [
        ('algorithm_comparison', '1. Comparison of Forecasting Algorithms'),
        ('dataset_size', '2. Effect of Historical Dataset Size'),
        ('seasonality', '3. Effect of Seasonality / Calendar Features'),
        ('missing_data', '4. Effect of Missing Data & Imputation'),
        ('volatility', '5. Effect of Unusual Fluctuations (Volatility)'),
        ('product_comparison', '6. Segment / Product-Wise Comparison'),
    ]

    MODEL_CHOICES = [
        ('random_forest', 'Random Forest Regressor'),
        ('linear_regression', 'Linear Regression'),
        ('arima', 'ARIMA (Statsmodels)'),
        ('moving_average', 'Moving Average (7-Period)'),
        ('naive', 'Naive Forecast (Baseline)'),
    ]

    dataset = forms.ModelChoiceField(queryset=SalesDataset.objects.none(), label='Dataset')
    experiment_type = forms.ChoiceField(
        choices=EXPERIMENT_TYPE_CHOICES, initial='algorithm_comparison', label='Research Protocol'
    )
    model_name = forms.ChoiceField(
        choices=MODEL_CHOICES,
        initial='random_forest',
        label='Primary Model (for single-model protocols)',
    )
    product_name = forms.CharField(required=False, initial='__ALL__', label='Segment / Group Scope')
    target_metric = forms.CharField(
        required=False, initial='', label='Numerical Target Column'
    )
    aggregation_freq = forms.ChoiceField(
        choices=ForecastRunForm.FREQ_CHOICES, initial='D', label='Frequency'
    )
    preprocessing_method = forms.ChoiceField(
        choices=ForecastRunForm.MISSING_CHOICES, initial='zero_fill', label='Imputation Method'
    )
    forecast_horizon = forms.IntegerField(min_value=1, max_value=60, initial=14, label='Forecast Horizon')
    random_seed = forms.IntegerField(min_value=0, max_value=99999, initial=42, label='Random Seed')

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dataset'].queryset = SalesDataset.objects.filter(user=user, is_mapped=True)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = SELECT_CLASS
            else:
                field.widget.attrs['class'] = INPUT_CLASS
