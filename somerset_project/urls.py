from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from scheduler.admin_views import import_students_csv, download_csv_template

def health_check(request):
    return JsonResponse({'status': 'healthy'})

urlpatterns = [
    path('admin/import-students/', import_students_csv, name='import_students_csv'),
    path('admin/download-csv-template/', download_csv_template, name='download_csv_template'),
    path('admin/', admin.site.urls),
    path('', include('scheduler.urls')),
    path('health/', health_check, name='health_check'),
]
