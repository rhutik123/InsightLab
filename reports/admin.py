from django.contrib import admin

from .models import GeneratedReport


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'dataset', 'created_at')
