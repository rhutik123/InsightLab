"""
URL patterns for the datasets app.
"""

from django.urls import path

from . import views

app_name = 'datasets'

urlpatterns = [
    path('', views.dataset_list_view, name='list'),
    path('upload/', views.dataset_upload_view, name='upload'),
    path('sample-csv/', views.download_sample_csv_view, name='sample_csv'),
    path('explorer/', views.data_explorer_view, name='explorer'),
    path('cleaning/', views.data_cleaning_view, name='cleaning'),
    path('<int:pk>/select-sheet/', views.dataset_select_sheet_view, name='select_sheet'),
    path('<int:pk>/select-json/', views.dataset_select_json_view, name='select_json'),
    path('<int:pk>/map/', views.dataset_map_columns_view, name='map_columns'),
    path('<int:pk>/', views.dataset_detail_view, name='detail'),
    path('<int:pk>/delete/', views.dataset_delete_view, name='delete'),
]
