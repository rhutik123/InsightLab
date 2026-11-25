"""
Root URL configuration for InsightLab.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from analytics.views import dashboard_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard_view, name='dashboard'),
    path('accounts/', include('accounts.urls')),
    path('datasets/', include('datasets.urls')),
    path('analytics/', include('analytics.urls')),
    path('forecasting/', include('forecasting.urls')),
    path('reports/', include('reports.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
