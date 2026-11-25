"""
Database models for uploaded datasets and workspace activity tracking.
Supports CSV, Excel (.xlsx/.xls), and JSON datasets via a shared normalized
pipeline while preserving compatibility with sales-forecasting workflows.
"""

from django.contrib.auth.models import User
from django.db import models


class SalesDataset(models.Model):
    """Represents a user-uploaded structured dataset and its normalized/cleaned versions."""

    DATASET_TYPE_CHOICES = [
        ('general', 'General Analytics'),
        ('sales', 'Sales & Revenue'),
        ('traffic', 'Web / Traffic Analytics'),
        ('finance', 'Expenses & Finance'),
        ('inventory', 'Inventory & Operations'),
    ]

    SOURCE_FORMAT_CHOICES = [
        ('csv', 'CSV (.csv)'),
        ('xlsx', 'Excel (.xlsx)'),
        ('xls', 'Legacy Excel (.xls)'),
        ('json', 'JSON (.json)'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sales_datasets')
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    dataset_type = models.CharField(max_length=40, choices=DATASET_TYPE_CHOICES, default='general')

    original_file = models.FileField(upload_to='datasets/original/%Y/%m/')
    normalized_file = models.FileField(upload_to='datasets/normalized/%Y/%m/', blank=True, null=True)
    cleaned_file = models.FileField(upload_to='datasets/cleaned/%Y/%m/', blank=True, null=True)
    file_size_bytes = models.PositiveIntegerField(default=0)
    source_format = models.CharField(max_length=12, choices=SOURCE_FORMAT_CHOICES, default='csv')
    original_filename = models.CharField(max_length=255, blank=True)
    selected_sheet = models.CharField(max_length=120, blank=True)
    selected_json_path = models.CharField(max_length=200, blank=True)
    ingestion_notes = models.JSONField(default=list, blank=True)

    # Primary role mappings (kept compatible with existing sales workflows; date & group are optional)
    date_column = models.CharField(max_length=120, blank=True)
    product_column = models.CharField(max_length=120, blank=True)
    quantity_column = models.CharField(max_length=120, blank=True)
    revenue_column = models.CharField(max_length=120, blank=True)

    # General detected/confirmed column schema (datetime, numeric, categorical, text, identifier)
    column_schema = models.JSONField(default=dict, blank=True)

    is_mapped = models.BooleanField(default=False)
    has_cleaned_version = models.BooleanField(default=False)

    # JSON summaries computed from actual dataset files
    profile_summary = models.JSONField(default=dict, blank=True)
    cleaned_profile_summary = models.JSONField(default=dict, blank=True)
    cleaning_log = models.JSONField(default=list, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.name} ({self.user.username})'

    @property
    def source_file_path(self):
        """Return normalized CSV path if available, otherwise original file path."""
        if self.normalized_file:
            return self.normalized_file.path
        return self.original_file.path

    @property
    def active_file_path(self):
        """Return cleaned file path if available, otherwise normalized/original source path."""
        if self.has_cleaned_version and self.cleaned_file:
            return self.cleaned_file.path
        return self.source_file_path

    @property
    def needs_structure_selection(self):
        """Return True when a multi-sheet Excel or multi-array JSON file awaits user selection."""
        if self.normalized_file:
            return False
        return self.source_format in ('xlsx', 'xls', 'json')

    @property
    def active_profile(self):
        """Return cleaned profile summary when available, else original profile."""
        if self.has_cleaned_version and self.cleaned_profile_summary:
            return self.cleaned_profile_summary
        return self.profile_summary or {}

    @property
    def primary_target_column(self):
        """Return the primary numerical target column name."""
        schema = self.column_schema or {}
        if schema.get('primary_target_column'):
            return schema['primary_target_column']
        if self.revenue_column:
            return self.revenue_column
        numeric_cols = schema.get('numeric_columns') or []
        return numeric_cols[0] if numeric_cols else ''

    @property
    def primary_group_column(self):
        """Return the primary categorical grouping column name if configured."""
        schema = self.column_schema or {}
        if 'primary_group_column' in schema:
            return schema['primary_group_column'] or ''
        return self.product_column or ''

    @property
    def primary_date_column(self):
        """Return the primary datetime column name if configured."""
        schema = self.column_schema or {}
        if 'primary_date_column' in schema:
            return schema['primary_date_column'] or ''
        return self.date_column or ''

    def get_column_mapping(self):
        """Return standardized mapping dictionary compatible with both general and sales modules."""
        return {
            'date': self.primary_date_column,
            'product': self.primary_group_column,
            'quantity': self.quantity_column,
            'revenue': self.primary_target_column,
            'target': self.primary_target_column,
            'group': self.primary_group_column,
        }


class WorkspaceActivity(models.Model):
    """Audit trail of real user actions in the InsightLab workspace."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workspace_activities')
    action_type = models.CharField(max_length=50)
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username}: {self.message}'
