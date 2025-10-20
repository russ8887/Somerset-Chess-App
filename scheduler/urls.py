from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views, event_views

urlpatterns = [
    # Main pages
    path('login/', views.CoachLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('student-report/<int:student_pk>/term/<int:term_pk>/', views.student_report_view, name='student-report'),
    path('availability/', views.manage_availability, name='manage-availability'),
    path('student/<int:student_pk>/availability/', views.manage_student_availability, name='manage-student-availability'),
    path('analytics/', views.analytics_dashboard, name='analytics-dashboard'),
    path('add-extra-lesson/', views.add_extra_lesson, name='add-extra-lesson'),
    
    # --- New Fill-in Management URL ---
    path('lesson/<int:lesson_pk>/manage/', views.manage_lesson_view, name='manage-lesson'),

    # --- HTMX partials for Dashboard ---
    path('attendance/<int:pk>/mark/<str:status>/', views.mark_attendance, name='mark-attendance'),
    path('attendance/<int:pk>/reason/<str:reason_code>/', views.save_reason, name='save-reason'),
    path('attendance/<int:pk>/mark-fill-in-absent/', views.mark_fill_in_absent, name='mark-fill-in-absent'),
    path('note/create/<int:record_pk>/', views.create_note_view, name='create-note'),
    path('note/<int:pk>/', views.view_lesson_note, name='view-note'),
    path('note/<int:pk>/edit/', views.edit_lesson_note, name='edit-note'),

    # The old fill-in URLs below have been removed as they are now obsolete.
    
    # --- Event Management URLs ---
    path('events/', event_views.event_management_dashboard, name='event-management-dashboard'),
    path('events/create/public-holiday/', event_views.create_public_holiday, name='create-public-holiday'),
    path('events/create/pupil-free-day/', event_views.create_pupil_free_day, name='create-pupil-free-day'),
    path('events/create/camp/', event_views.create_camp_event, name='create-camp-event'),
    path('events/create/excursion/', event_views.create_excursion_event, name='create-excursion-event'),
    path('events/create/individual/', event_views.create_individual_event, name='create-individual-event'),
    path('events/create/custom/', event_views.create_custom_event, name='create-custom-event'),
    path('events/<int:event_id>/', event_views.event_detail, name='event-detail'),
    path('events/<int:event_id>/delete/', event_views.delete_event, name='delete-event'),
    path('events/preview/', event_views.event_preview, name='event-preview'),
    path('events/quick-actions/', event_views.quick_event_actions, name='quick-event-actions'),
    path('api/search-students/', event_views.search_students, name='search-students'),
    
    # Visual Slot Finder API endpoints
    path('api/available-slots/<int:student_id>/', views.get_available_slots_api, name='get-available-slots-api'),
    path('api/move-student/<int:student_id>/', views.move_student_to_slot_api, name='move-student-to-slot-api'),
]
