# scheduler/admin.py

from django.contrib import admin
from .models import (
    Term, TimeSlot, SchoolClass, Coach, Student, Enrollment,
    ScheduledGroup, ScheduledUnavailability, OneOffEvent, LessonSession, AttendanceRecord, LessonNote
)

# --- Configuration for Basic Models ---
@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date', 'is_active')
    list_filter = ('start_date', 'end_date', 'is_active')
    ordering = ('-is_active', '-start_date')  # Show active term first
    list_editable = ('is_active',)  # Allow quick editing of active status
    
    def get_queryset(self, request):
        """Highlight the active term in the admin list"""
        return super().get_queryset(request)

@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'start_time', 'end_time')
    ordering = ('start_time',)

@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)

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
    
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['import_csv_url'] = '/admin/import-students/'
        return super().changelist_view(request, extra_context=extra_context)

# --- Configuration for the Scheduled Group Admin ---
@admin.register(ScheduledGroup)
class ScheduledGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'coach', 'get_members_display', 'get_day_time_display', 'term')
    list_filter = ('term', 'coach', 'day_of_week')
    search_fields = ('name', 'coach__user__first_name', 'coach__user__last_name')
    filter_horizontal = ('members',) # Makes selecting students much easier
    
    def get_queryset(self, request):
        """Optimize queries to prevent N+1 problems"""
        return super().get_queryset(request).select_related(
            'coach__user', 'term', 'time_slot'
        ).prefetch_related(
            'members__student__school_class'
        )
    
    @admin.display(description='Members', ordering='members__student__last_name')
    def get_members_display(self, obj):
        """Display group members with capacity info"""
        members = obj.members.all()
        member_count = len(members)
        
        if member_count == 0:
            return f"Empty (0/{obj.max_capacity})"
        
        # Show first few students, then count if more
        member_names = []
        for member in members[:3]:  # Show first 3 students
            student = member.student
            member_names.append(f"{student.first_name} {student.last_name}")
        
        if member_count > 3:
            member_names.append(f"(+{member_count - 3} more)")
        
        members_text = ", ".join(member_names)
        capacity_text = f"({member_count}/{obj.max_capacity})"
        
        return f"{members_text} {capacity_text}"
    
    @admin.display(description='Schedule', ordering='day_of_week')
    def get_day_time_display(self, obj):
        """Display day and time in a compact format"""
        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        day_name = day_names[obj.day_of_week] if obj.day_of_week < len(day_names) else f'Day {obj.day_of_week}'
        
        if obj.time_slot:
            time_str = obj.time_slot.start_time.strftime('%I:%M %p')
            return f"{day_name} {time_str}"
        else:
            return f"{day_name} (No time set)"
    
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['import_csv_url'] = '/admin/import-lessons/'
        return super().changelist_view(request, extra_context=extra_context)

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

# --- Configuration for Enrollment Admin ---
@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('student', 'term', 'enrollment_type', 'target_lessons', 'lessons_carried_forward', 'adjusted_target', 'get_lesson_balance_display')
    list_filter = ('term', 'enrollment_type')
    search_fields = ('student__first_name', 'student__last_name')
    fields = ('student', 'term', 'enrollment_type', 'target_lessons', 'lessons_carried_forward', 'adjusted_target')
    readonly_fields = ('adjusted_target',)
    
    def get_lesson_balance_display(self, obj):
        balance = obj.get_lesson_balance()
        status = obj.get_balance_status()
        if balance > 0:
            return f"Owes {balance} lessons"
        elif balance < 0:
            return f"Credit {abs(balance)} lessons"
        else:
            return "Balanced"
    get_lesson_balance_display.short_description = 'Lesson Balance'
    get_lesson_balance_display.admin_order_field = 'adjusted_target'
    ordering = ('term', 'student__last_name', 'student__first_name')

# --- Configuration for Lesson Session Admin ---
@admin.register(LessonSession)
class LessonSessionAdmin(admin.ModelAdmin):
    list_display = ('scheduled_group', 'lesson_date', 'status')
    list_filter = ('status', 'lesson_date', 'scheduled_group__term')
    search_fields = ('scheduled_group__name',)
    ordering = ('-lesson_date',)

# --- Configuration for Lesson Note Admin ---
@admin.register(LessonNote)
class LessonNoteAdmin(admin.ModelAdmin):
    list_display = ('get_student_name', 'get_lesson_date', 'student_understanding')
    list_filter = ('student_understanding', 'attendance_record__lesson_session__lesson_date')
    search_fields = ('attendance_record__enrollment__student__first_name', 'attendance_record__enrollment__student__last_name')
    
    @admin.display(description='Student', ordering='attendance_record__enrollment__student__last_name')
    def get_student_name(self, obj):
        return obj.attendance_record.enrollment.student
    
    @admin.display(description='Lesson Date', ordering='attendance_record__lesson_session__lesson_date')
    def get_lesson_date(self, obj):
        return obj.attendance_record.lesson_session.lesson_date

# --- Configuration for Scheduled Unavailability Admin ---
@admin.register(ScheduledUnavailability)
class ScheduledUnavailabilityAdmin(admin.ModelAdmin):
    list_display = ('name', 'day_of_week', 'time_slot')
    list_filter = ('day_of_week',)
    search_fields = ('name',)
    filter_horizontal = ('students', 'school_classes')

# --- Configuration for One-Off Event Admin ---
@admin.register(OneOffEvent)
class OneOffEventAdmin(admin.ModelAdmin):
    list_display = ('name', 'event_date', 'get_time_slots_display', 'get_affected_groups_display', 'reason')
    list_filter = ('event_date', 'reason', 'school_classes')
    search_fields = ('name', 'reason')
    filter_horizontal = ('students', 'school_classes', 'time_slots')
    date_hierarchy = 'event_date'
    ordering = ('-event_date',)
    
    def changelist_view(self, request, extra_context=None):
        """Add context data for the custom template"""
        from datetime import date, timedelta
        
        extra_context = extra_context or {}
        extra_context['today'] = date.today()
        extra_context['next_week'] = date.today() + timedelta(days=7)
        
        return super().changelist_view(request, extra_context=extra_context)
    
    fieldsets = (
        ('Event Details', {
            'fields': ('name', 'event_date', 'reason')
        }),
        ('Time Slots', {
            'fields': ('time_slots',),
            'description': 'Select specific time slots affected by this event. Leave blank for an all-day event.'
        }),
        ('Affected Students', {
            'fields': ('school_classes', 'students'),
            'description': 'Select school classes and/or individual students affected by this event.'
        }),
    )
    
    actions = ['create_public_holiday', 'create_pupil_free_day', 'create_whole_school_event']
    
    @admin.display(description='Time Slots')
    def get_time_slots_display(self, obj):
        if obj.time_slots.exists():
            slots = obj.time_slots.all()[:3]  # Show first 3 slots
            display = ', '.join([str(slot) for slot in slots])
            if obj.time_slots.count() > 3:
                display += f' (+{obj.time_slots.count() - 3} more)'
            return display
        return 'All Day'
    
    @admin.display(description='Affected Groups')
    def get_affected_groups_display(self, obj):
        groups = []
        if obj.school_classes.exists():
            classes = list(obj.school_classes.values_list('name', flat=True))
            if len(classes) <= 3:
                groups.extend(classes)
            else:
                groups.extend(classes[:2])
                groups.append(f'+{len(classes) - 2} more classes')
        
        if obj.students.exists():
            student_count = obj.students.count()
            if student_count <= 2:
                students = [str(s) for s in obj.students.all()]
                groups.extend(students)
            else:
                groups.append(f'{student_count} individual students')
        
        return ', '.join(groups) if groups else 'No specific targets'
    
    @admin.action(description='Create Public Holiday event for selected dates')
    def create_public_holiday(self, request, queryset):
        """Create public holiday events that affect all students"""
        from django.contrib import messages
        from datetime import date
        
        # Get today's date as default
        today = date.today()
        
        # Create a public holiday event for today (can be customized)
        event, created = OneOffEvent.objects.get_or_create(
            name=f'Public Holiday - {today.strftime("%B %d, %Y")}',
            event_date=today,
            defaults={
                'reason': 'Public Holiday'
            }
        )
        
        if created:
            # Add all school classes to make it affect everyone
            all_classes = SchoolClass.objects.all()
            event.school_classes.set(all_classes)
            messages.success(request, f'Public Holiday event created for {today.strftime("%B %d, %Y")}. All students will be marked absent.')
        else:
            messages.info(request, f'Public Holiday event already exists for {today.strftime("%B %d, %Y")}.')
    
    @admin.action(description='Create Pupil Free Day event for selected dates')
    def create_pupil_free_day(self, request, queryset):
        """Create pupil free day events that affect all students"""
        from django.contrib import messages
        from datetime import date
        
        # Get today's date as default
        today = date.today()
        
        # Create a pupil free day event for today (can be customized)
        event, created = OneOffEvent.objects.get_or_create(
            name=f'Pupil Free Day - {today.strftime("%B %d, %Y")}',
            event_date=today,
            defaults={
                'reason': 'Pupil Free Day'
            }
        )
        
        if created:
            # Add all school classes to make it affect everyone
            all_classes = SchoolClass.objects.all()
            event.school_classes.set(all_classes)
            messages.success(request, f'Pupil Free Day event created for {today.strftime("%B %d, %Y")}. All students will be marked absent.')
        else:
            messages.info(request, f'Pupil Free Day event already exists for {today.strftime("%B %d, %Y")}.')
    
    @admin.action(description='Create whole school event for selected dates')
    def create_whole_school_event(self, request, queryset):
        """Create custom whole school events"""
        from django.contrib import messages
        from datetime import date
        
        # Get today's date as default
        today = date.today()
        
        # Create a generic whole school event for today (can be customized)
        event, created = OneOffEvent.objects.get_or_create(
            name=f'Whole School Event - {today.strftime("%B %d, %Y")}',
            event_date=today,
            defaults={
                'reason': 'School Event'
            }
        )
        
        if created:
            # Add all school classes to make it affect everyone
            all_classes = SchoolClass.objects.all()
            event.school_classes.set(all_classes)
            messages.success(request, f'Whole School Event created for {today.strftime("%B %d, %Y")}. All students will be marked absent.')
        else:
            messages.info(request, f'Whole School Event already exists for {today.strftime("%B %d, %Y")}.')
    
    def save_model(self, request, obj, form, change):
        """Override save to provide user feedback"""
        super().save_model(request, obj, form, change)
        from django.contrib import messages
        
        if not change:  # New object
            affected_count = 0
            if obj.school_classes.exists():
                affected_count += sum(sc.student_set.count() for sc in obj.school_classes.all())
            if obj.students.exists():
                affected_count += obj.students.count()
            
            time_info = "all day" if not obj.time_slots.exists() else f"{obj.time_slots.count()} time slots"
            messages.success(request, f'Event "{obj.name}" created successfully. Will affect approximately {affected_count} students for {time_info} on {obj.event_date}.')
