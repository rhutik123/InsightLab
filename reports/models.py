"""
Database model for tracking generated PDF reports.
"""

from django.contrib.auth.models import User
from django.db import models

from datasets.models import SalesDataset


class GeneratedReport(models.Model):
    """Stores metadata and file reference for a user-generated PDF report."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='generated_reports')
    dataset = models.ForeignKey(
        SalesDataset, on_delete=models.CASCADE, related_name='generated_reports'
    )
    title = models.CharField(max_length=200)
    pdf_file = models.FileField(upload_to='reports/%Y/%m/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} ({self.user.username})'
