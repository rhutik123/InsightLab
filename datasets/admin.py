from django.contrib import admin

from .models import SalesDataset, WorkspaceActivity


@admin.register(SalesDataset)
class SalesDatasetAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'is_mapped', 'has_cleaned_version', 'uploaded_at')
    list_filter = ('is_mapped', 'has_cleaned_version')
    search_fields = ('name', 'user__username')


@admin.register(WorkspaceActivity)
class WorkspaceActivityAdmin(admin.ModelAdmin):
    list_display = ('user', 'action_type', 'message', 'created_at')
    list_filter = ('action_type',)
