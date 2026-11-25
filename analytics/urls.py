"""
URL patterns for the analytics app.
"""

from django.urls import path

from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.analysis_view, name='analysis'),
    path('visualizations/', views.visualizations_view, name='visualizations'),
    path('anomalies/', views.anomaly_detection_view, name='anomalies'),
]
