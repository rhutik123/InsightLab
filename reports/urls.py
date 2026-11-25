"""
URL patterns for the reports app.
"""

from django.urls import path

from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.report_list_view, name='list'),
    path('<int:pk>/download/', views.report_download_view, name='download'),
    path('<int:pk>/delete/', views.report_delete_view, name='delete'),
]
