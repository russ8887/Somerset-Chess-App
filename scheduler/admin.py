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

    # Temporarily commented out to test if basic admin works
    # def change_view(self, request, object_id, form_url='', extra_context=None):
    #     """Override change_view to provide context for custom template"""
    #     import logging
    #     logger = logging.getLogger(__name__)
    #     logger.info(f"ScheduledGroupAdmin change_view called with object_id: {object_id}")

    #     extra_context = extra_context or {}
    #     extra_context['request'] = request  # Add request to template context
    #     logger.info("Added request to extra_context")

    #     # Get the scheduled group object
    #     try:
    #         obj = self.get_object(request, object_id)
    #         logger.info(f"Retrieved object: {obj}")
    #     except Exception as e:
    #         logger.error(f"Error getting object: {e}")
    #         obj = None

    #     # Get term from request parameters (set by JavaScript in template)
    #     term_id = request.GET.get('term')
    #     logger.info(f"Term ID from request: {term_id}")

    #     if term_id:
    #     try:
    #         from .models import Term, Enrollment
    #         logger.info("Importing models successful")
    #         term = Term.objects.get(pk=term_id)
    #         logger.info(f"Found term: {term}")

    #         # Get all enrollments for this term
    #         term_enrollments = Enrollment.objects.filter(term=term)
    #         logger.info(f"Found {term_enrollments.count()} enrollments for term")

    #         if obj:
    #             # Available enrollments: those in the term but not in this group
    #             extra_context['available_enrollments'] = term_enrollments.exclude(
    #                 pk__in=obj.members.values_list('pk', flat=True)
    #             )
    #             # Scheduled enrollments: current members of this group
    #             extra_context['scheduled_enrollments'] = obj.members.all()
    #             logger.info(f"Set context for existing object: {len(extra_context['available_enrollments'])} available, {len(extra_context['scheduled_enrollments'])} scheduled")
    #         else:
    #             # For new objects, all term enrollments are available
    #             extra_context['available_enrollments'] = term_enrollments
    #             extra_context['scheduled_enrollments'] = Enrollment.objects.none()
    #             logger.info(f"Set context for new object: {len(extra_context['available_enrollments'])} available")

    #     except (Term.DoesNotExist, ValueError) as e:
    #         logger.error(f"Term error: {e}")
    #         # If term doesn't exist or invalid, show empty querysets
    #         extra_context['available_enrollments'] = Enrollment.objects.none()
    #         extra_context['scheduled_enrollments'] = Enrollment.objects.none()
    #     except Exception as e:
    #         logger.error(f"Unexpected error in term processing: {e}")
    #         extra_context['available_enrollments'] = Enrollment.objects.none()
    #         extra_context['scheduled_enrollments'] = Enrollment.objects.none()
    # else:
    #     # No term selected yet
    #     logger.info("No term selected")
    #     extra_context['available_enrollments'] = Enrollment.objects.none()
    #     extra_context['scheduled_enrollments'] = Enrollment.objects.none()

    # logger.info("About to call super().change_view")
    # try:
    #     result = super().change_view(request, object_id, form_url, extra_context)
    #     logger.info("super().change_view completed successfully")
    #     return result
    # except Exception as e:
    #     logger.error(f"Error in super().change_view: {e}")
    #     raise

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
