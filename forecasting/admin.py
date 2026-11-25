from django.contrib import admin

from .models import ForecastExperiment


@admin.register(ForecastExperiment)
class ForecastExperimentAdmin(admin.ModelAdmin):
    list_display = ('dataset', 'user', 'model_name', 'experiment_type', 'mae', 'rmse', 'created_at')
    list_filter = ('model_name', 'experiment_type')
