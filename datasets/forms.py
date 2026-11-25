"""
Forms for uploading CSV, Excel (.xlsx/.xls), and JSON datasets, selecting workbook sheets
or JSON record paths, mapping flexible column roles, and configuring data cleaning.
"""

from django import forms

from accounts.forms import INPUT_CLASS
from .data_processing import validate_uploaded_dataset
from .models import SalesDataset

SELECT_CLASS = (
    'block w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 '
    'text-sm text-slate-900 shadow-xs focus:border-indigo-500 focus:outline-none '
    'focus:ring-2 focus:ring-indigo-500/20'
)


class DatasetUploadForm(forms.ModelForm):
    """Upload form supporting CSV (.csv), Excel (.xlsx, .xls), and JSON (.json) datasets."""

    class Meta:
        model = SalesDataset
        fields = ('name', 'dataset_type', 'description', 'original_file')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.detected_format = 'csv'
        self.fields['name'].widget.attrs.update({
            'class': INPUT_CLASS,
            'placeholder': 'e.g., Website Traffic Q1, Retail Sales 2025, Monthly Expenses',
        })
        self.fields['dataset_type'].widget.attrs['class'] = SELECT_CLASS
        self.fields['dataset_type'].label = 'Dataset Category'
        self.fields['dataset_type'].required = False
        self.fields['description'].widget = forms.Textarea(
            attrs={
                'class': INPUT_CLASS,
                'rows': 3,
                'placeholder': 'Optional notes about data source, granularity, or domain context...',
            }
        )
        self.fields['original_file'].label = 'Dataset File (.csv, .xlsx, .xls, .json)'
        self.fields['original_file'].widget.attrs.update({
            'class': (
                'block w-full text-sm text-slate-600 file:mr-4 file:rounded-lg '
                'file:border-0 file:bg-indigo-50 file:px-4 file:py-2 file:text-sm '
                'file:font-medium file:text-indigo-700 hover:file:bg-indigo-100'
            ),
            'accept': '.csv,.xlsx,.xls,.json',
        })

    def clean_dataset_type(self):
        return self.cleaned_data.get('dataset_type') or 'general'

    def clean_original_file(self):
        uploaded_file = self.cleaned_data.get('original_file')
        is_valid, error_message, detected_format = validate_uploaded_dataset(uploaded_file)
        if not is_valid:
            raise forms.ValidationError(error_message)
        self.detected_format = detected_format or 'csv'
        return uploaded_file


class SheetSelectionForm(forms.Form):
    """Select which usable worksheet from a multi-sheet Excel workbook to ingest."""

    sheet_name = forms.ChoiceField(
        label='Worksheet to Import',
        widget=forms.RadioSelect,
    )

    def __init__(self, usable_sheets, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sheet_name'].choices = [
            (s['name'], f"{s['name']} ({s['row_count']} rows, {s['column_count']} columns)")
            for s in usable_sheets
        ]
        if usable_sheets and not self.initial.get('sheet_name'):
            self.initial['sheet_name'] = usable_sheets[0]['name']


class JsonPathSelectionForm(forms.Form):
    """Select which candidate record path inside a structured JSON file to ingest."""

    json_path = forms.ChoiceField(
        label='JSON Record Collection to Import',
        widget=forms.RadioSelect,
    )

    def __init__(self, usable_candidates, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['json_path'].choices = [
            (c['path'], f"{c['label']} ({c['row_count']} records, {c['column_count']} columns)")
            for c in usable_candidates
        ]
        if usable_candidates and not self.initial.get('json_path'):
            self.initial['json_path'] = usable_candidates[0]['path']


class ColumnMappingForm(forms.Form):
    """
    Flexible schema mapping form allowing users to assign Date (optional),
    Category/Group (optional), Primary Numerical Target, and Secondary Numerical
    Metric (optional) from arbitrary CSV headers.
    """

    dataset_type = forms.ChoiceField(
        choices=SalesDataset.DATASET_TYPE_CHOICES,
        required=False,
        initial='general',
        label='Dataset Domain',
    )
    date_column = forms.ChoiceField(required=False, label='Date / Time Column (optional)')
    product_column = forms.ChoiceField(
        required=False, label='Category / Grouping Column (optional)'
    )
    revenue_column = forms.ChoiceField(
        required=False, label='Primary Numerical Target Column (e.g., Revenue, Visitors, Amount)'
    )
    quantity_column = forms.ChoiceField(
        required=False, label='Secondary Numerical Column (optional, e.g., Quantity, Page Views)'
    )

    def __init__(self, csv_columns, *args, **kwargs):
        super().__init__(*args, **kwargs)
        optional_choices = [('', '-- None / Not applicable --')] + [(col, col) for col in csv_columns]
        self.fields['dataset_type'].widget.attrs['class'] = SELECT_CLASS
        for field_name in ('date_column', 'product_column', 'revenue_column', 'quantity_column'):
            self.fields[field_name].choices = optional_choices
            self.fields[field_name].widget.attrs['class'] = SELECT_CLASS

    def clean(self):
        cleaned = super().clean()
        selected = [
            cleaned.get('date_column'),
            cleaned.get('product_column'),
            cleaned.get('quantity_column'),
            cleaned.get('revenue_column'),
        ]
        non_empty = [val for val in selected if val]
        if len(non_empty) != len(set(non_empty)):
            raise forms.ValidationError(
                'Each selected role must map to a distinct CSV column.'
            )
        return cleaned


class DataCleaningForm(forms.Form):
    """Dataset-independent options for previewing and applying data cleaning."""

    MISSING_STRATEGY_CHOICES = [
        ('drop', 'Drop rows with missing numerical values'),
        ('median', 'Impute missing numerical values with column median'),
        ('mean', 'Impute missing numerical values with column mean'),
        ('zero', 'Fill missing numerical values with 0'),
    ]

    CATEGORICAL_MISSING_CHOICES = [
        ('fill_unknown', 'Replace blank categorical values with "Unknown"'),
        ('drop', 'Drop rows with blank categorical values'),
        ('keep', 'Keep categorical blanks as-is'),
    ]

    dataset = forms.ModelChoiceField(queryset=SalesDataset.objects.none(), label='Select Dataset')
    remove_duplicates = forms.BooleanField(
        required=False, initial=True, label='Remove duplicate records'
    )
    remove_invalid_dates = forms.BooleanField(
        required=False,
        initial=True,
        label='Remove rows with invalid dates (when a Date column is mapped)',
    )
    remove_negative_values = forms.BooleanField(
        required=False,
        initial=False,
        label='Remove rows with negative values in numerical columns',
    )
    missing_strategy = forms.ChoiceField(
        choices=MISSING_STRATEGY_CHOICES, initial='drop', label='Missing numerical values'
    )
    categorical_missing_strategy = forms.ChoiceField(
        choices=CATEGORICAL_MISSING_CHOICES,
        required=False,
        initial='fill_unknown',
        label='Missing categorical values',
    )
    aggregate_daily = forms.BooleanField(
        required=False,
        initial=False,
        label='Aggregate numerical columns by Date & Category (when Date is mapped)',
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dataset'].queryset = SalesDataset.objects.filter(user=user, is_mapped=True)
        self.fields['dataset'].widget.attrs['class'] = SELECT_CLASS
        self.fields['missing_strategy'].widget.attrs['class'] = SELECT_CLASS
        self.fields['categorical_missing_strategy'].widget.attrs['class'] = SELECT_CLASS
        for field_name in (
            'remove_duplicates',
            'remove_invalid_dates',
            'remove_negative_values',
            'aggregate_daily',
        ):
            self.fields[field_name].widget.attrs['class'] = (
                'h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500'
            )

    def clean_categorical_missing_strategy(self):
        return self.cleaned_data.get('categorical_missing_strategy') or 'fill_unknown'
