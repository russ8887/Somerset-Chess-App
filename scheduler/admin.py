# scheduler/admin.py

from django.contrib import admin
from .models import (
    Term, TimeSlot, SchoolClass, Coach, Student, Enrollment,
    ScheduledGroup, ScheduledUnavailability, LessonSession, AttendanceRecord, LessonNote
)

# --- Configuration for the Coach Admin ---
@admin.register(Coach)
class CoachAdmin(admin.ModelAdmin):
    list_display = ('get_full_name', 'get_email', 'is_head_coach', 'user_is_staff')
    list_filter = ('is_head_coach',)
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
    
    # These methods pull information from the linked User model to display in the list
    @admin.display(description='Full Name', ordering='user__first_name')
    def get_full_name(self, obj):
        return obj.user.get_full_name() if obj.user else "No User Linked"

    @admin.display(description='Email', ordering='user__email')
    def get_email(self, obj):
        return obj.user.email if obj.user else "N/A"
        
    @admin.display(description='Has Admin Login', boolean=True)
    def user_is_staff(self, obj):
        return obj.user.is_staff if obj.user else False

# --- Configuration for the Student Admin ---
@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('last_name', 'first_name', 'year_level', 'school_class')
    list_filter = ('year_level', 'school_class')
    search_fields = ('first_name', 'last_name')

# --- Configuration for the Scheduled Group Admin ---
@admin.register(ScheduledGroup)
class ScheduledGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'coach', 'term', 'day_of_week', 'time_slot')
    list_filter = ('term', 'coach', 'day_of_week')
    search_fields = ('name', 'coach__user__first_name', 'coach__user__last_name')
    filter_horizontal = ('members',) # Makes selecting students much easier

# --- Configuration for the Attendance Record Admin ---
@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('get_student_name', 'lesson_session', 'status', 'reason_for_absence')
    list_filter = ('status', 'reason_for_absence', 'lesson_session__lesson_date')
    search_fields = ('enrollment__student__first_name', 'enrollment__student__last_name')

    @admin.display(description='Student', ordering='enrollment__student__last_name')
    def get_student_name(self, obj):
        return obj.enrollment.student

# --- Register all other models with the admin site ---
admin.site.register(Term)
admin.site.register(TimeSlot)
admin.site.register(SchoolClass)
admin.site.register(Enrollment)
admin.site.register(ScheduledUnavailability)
admin.site.register(LessonSession)
admin.site.register(LessonNote)