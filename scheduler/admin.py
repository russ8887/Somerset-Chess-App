from django.contrib import admin
from .models import (
    Term, Coach, Student, Enrollment, ScheduledGroup, TimeSlot, SchoolClass,
    LessonSession, AttendanceRecord, LessonNote, ScheduledUnavailability, OneOffEvent
)

class ScheduledGroupAdmin(admin.ModelAdmin):
    # Use our custom template for the add/change page
    change_form_template = 'admin/scheduler/scheduledgroup/change_form.html'
    
    list_display = ('name', 'coach', 'term', 'day_of_week', 'time_slot')
    list_filter = ('term', 'coach', 'day_of_week')
    search_fields = ('name',)

    def get_fieldsets(self, request, obj=None):
        # This controls the layout of the main fields
        return ((None, {'fields': ('name', 'coach', 'term', 'day_of_week', 'time_slot')}),)

    def save_model(self, request, obj, form, change):
        # Save the main object first
        super().save_model(request, obj, form, change)
        # Now, handle the custom student list from our template
        member_ids = request.POST.getlist('members')
        obj.members.set(member_ids)

    def get_student_lists(self, request, group=None):
        """A helper function to get the available and scheduled students."""
        term_id = request.GET.get('term') or (group.term.id if group else None)
        if not term_id:
            return [], []

        # Get all students enrolled in this term
        all_enrollments_in_term = Enrollment.objects.filter(term_id=term_id).select_related('student')
        
        # Get IDs of students already scheduled in OTHER groups
        other_groups_query = ScheduledGroup.objects.filter(term_id=term_id)
        if group:
            other_groups_query = other_groups_query.exclude(pk=group.pk)
        scheduled_in_other_groups_ids = other_groups_query.values_list('members', flat=True)

        available = all_enrollments_in_term.exclude(id__in=scheduled_in_other_groups_ids).order_by('student__last_name')
        scheduled = group.members.all().select_related('student').order_by('student__last_name') if group else []
        
        return available, scheduled

    def add_view(self, request, form_url='', extra_context=None):
        # This provides the student lists when adding a new group
        extra_context = extra_context or {}
        available, scheduled = self.get_student_lists(request)
        extra_context['available_enrollments'] = available
        extra_context['scheduled_enrollments'] = scheduled
        return super().add_view(request, form_url, extra_context=extra_context)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        # This provides the student lists when editing an existing group
        extra_context = extra_context or {}
        group = self.get_object(request, object_id)
        available, scheduled = self.get_student_lists(request, group)
        extra_context['available_enrollments'] = available
        extra_context['scheduled_enrollments'] = scheduled
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

# --- Registration ---

admin.site.register(Term)
admin.site.register(TimeSlot)
admin.site.register(SchoolClass)
admin.site.register(Coach)
admin.site.register(Student)
admin.site.register(Enrollment)
admin.site.register(ScheduledUnavailability)
admin.site.register(OneOffEvent)
admin.site.register(LessonSession)
admin.site.register(AttendanceRecord)
admin.site.register(LessonNote)
admin.site.register(ScheduledGroup, ScheduledGroupAdmin)