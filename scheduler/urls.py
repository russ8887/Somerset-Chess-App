from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

urlpatterns = [
    # Main pages
    path('login/', views.CoachLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('student-report/<int:student_pk>/term/<int:term_pk>/', views.student_report_view, name='student-report'),
    path('availability/', views.manage_availability, name='manage-availability'),
    path('student/<int:student_pk>/availability/', views.manage_student_availability, name='manage-student-availability'),
    
    # --- New Fill-in Management URL ---
    path('lesson/<int:lesson_pk>/manage/', views.manage_lesson_view, name='manage-lesson'),

    # --- HTMX partials for Dashboard ---
    path('attendance/<int:pk>/mark/<str:status>/', views.mark_attendance, name='mark-attendance'),
    path('attendance/<int:pk>/reason/<str:reason_code>/', views.save_reason, name='save-reason'),
    path('note/create/<int:record_pk>/', views.create_note_view, name='create-note'),
    path('note/<int:pk>/', views.view_lesson_note, name='view-note'),
    path('note/<int:pk>/edit/', views.edit_lesson_note, name='edit-note'),

    # The old fill-in URLs below have been removed as they are now obsolete.
]
