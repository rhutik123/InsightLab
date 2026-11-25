"""
URL patterns for the forecasting app.
"""

from django.urls import path

from . import views

app_name = 'forecasting'

urlpatterns = [
    path('', views.forecasting_home_view, name='home'),
    path('compare/', views.model_comparison_view, name='compare'),
    path('experiments/', views.experiment_lab_view, name='experiments'),
    path('<int:pk>/', views.experiment_detail_view, name='detail'),
    path('<int:pk>/delete/', views.experiment_delete_view, name='delete'),
]
