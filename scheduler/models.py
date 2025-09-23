# scheduler/models.py

from django.db import models
from django.contrib.auth.models import User

# The format_html import is no longer needed with the performance update
# from django.utils.html import format_html

class Term(models.Model):
    name = models.CharField(max_length=100, help_text="e.g., Term 3, 2025")
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=False, help_text="Only one term can be active at a time. This term will be used for CSV imports.")

    def __str__(self):
        active_indicator = " (ACTIVE)" if self.is_active else ""
        return f"{self.name}{active_indicator}"
    
    @classmethod
    def get_active_term(cls):
        """Get the currently active term"""
        try:
            return cls.objects.get(is_active=True)
        except cls.DoesNotExist:
            return None
        except cls.MultipleObjectsReturned:
            # If somehow multiple terms are active, return the first one
            return cls.objects.filter(is_active=True).first()
    
    def save(self, *args, **kwargs):
        """Ensure only one term can be active at a time"""
        if self.is_active:
            # Set all other terms to inactive
            Term.objects.filter(is_active=True).update(is_active=False)
        super().save(*args, **kwargs)

class TimeSlot(models.Model):
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"{self.start_time.strftime('%I:%M %p')} - {self.end_time.strftime('%I:%M %p')}"

class SchoolClass(models.Model):
    name = models.CharField(max_length=20, unique=True, help_text="e.g., 4G, 5P")

    class Meta:
        verbose_name = "School Class"
        verbose_name_plural = "School Classes"

    def __str__(self):
        return self.name

class Coach(models.Model):
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    is_head_coach = models.BooleanField(default=False, help_text="Head coaches can view all other coaches' schedules.")
    
    # Coach specialization fields for intelligent slot finder
    specializes_beginner = models.BooleanField(
        default=True, 
        help_text="Coach is effective with beginner students"
    )
    specializes_intermediate = models.BooleanField(
        default=True, 
        help_text="Coach is effective with intermediate students"
    )
    specializes_advanced = models.BooleanField(
        default=False, 
        help_text="Coach is effective with advanced students"
    )
    
    # Coach workload preferences
    max_students_per_lesson = models.IntegerField(
        default=4, 
        help_text="Maximum students this coach prefers per lesson"
    )
    preferred_group_sizes = models.CharField(
        max_length=20,
        default='PAIR,GROUP',
        help_text="Comma-separated preferred group sizes (SOLO,PAIR,GROUP)"
    )
    
    # REMOVED: first_name, last_name, and email to avoid duplicating data from the User model.
    # We will now pull this information directly from the linked user.

    class Meta:
        verbose_name = "Coach"
        verbose_name_plural = "Coaches"

    def __str__(self):
        # Pulls the name directly from the linked User model for a single source of truth.
        if self.user:
            return self.user.get_full_name()
        return f"Coach ID: {self.pk} (No User Linked)"
    
    def specializes_in_skill_level(self, skill_level):
        """Check if coach specializes in a specific skill level"""
        skill_mapping = {
            'B': self.specializes_beginner,
            'I': self.specializes_intermediate,
            'A': self.specializes_advanced,
        }
        return skill_mapping.get(skill_level, False)
    
    def get_preferred_group_sizes_list(self):
        """Get list of preferred group sizes"""
        return [size.strip() for size in self.preferred_group_sizes.split(',') if size.strip()]

class Student(models.Model):
    class SkillLevel(models.TextChoices):
        BEGINNER = 'B', 'Beginner'
        INTERMEDIATE = 'I', 'Intermediate'
        ADVANCED = 'A', 'Advanced'
    
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    year_level = models.IntegerField(help_text="e.g., 3 for Year 3")
    school_class = models.ForeignKey(SchoolClass, on_delete=models.SET_NULL, null=True, blank=True)
    skill_level = models.CharField(
        max_length=1, 
        choices=SkillLevel.choices, 
        default=SkillLevel.BEGINNER,
        help_text="Chess skill level: Beginner, Intermediate, or Advanced"
    )

    def __str__(self):
        # Safely gets the school class name if it exists.
        school_class_name = getattr(self.school_class, 'name', 'N/A')
        return f"{self.first_name} {self.last_name} ({school_class_name})"
    
    def has_scheduling_conflict(self, day_of_week, time_slot):
        """
        Check if student has a scheduling conflict at the given day/time.
        Returns dict with conflict info or None if no conflict.
        """
        # Check individual unavailabilities
        individual_conflicts = ScheduledUnavailability.objects.filter(
            students=self,
            day_of_week=day_of_week,
            time_slot=time_slot
        )
        
        if individual_conflicts.exists():
            return {
                'has_conflict': True,
                'conflict_type': 'individual',
                'conflict_source': individual_conflicts.first().name,
                'conflict_description': f'Individual conflict: {individual_conflicts.first().name}'
            }
        
        # Check class-based unavailabilities
        if self.school_class:
            class_conflicts = ScheduledUnavailability.objects.filter(
                school_classes=self.school_class,
                day_of_week=day_of_week,
                time_slot=time_slot
            )
            
            if class_conflicts.exists():
                return {
                    'has_conflict': True,
                    'conflict_type': 'class',
                    'conflict_source': class_conflicts.first().name,
                    'conflict_description': f'Class conflict: {class_conflicts.first().name}'
                }
        
        return {
            'has_conflict': False,
            'conflict_type': None,
            'conflict_source': None,
            'conflict_description': None
        }
    
    def get_scheduled_lessons_with_conflicts(self, term=None):
        """
        Get all scheduled lessons for this student that have scheduling conflicts.
        Returns list of dicts with lesson and conflict info.
        """
        if not term:
            term = Term.get_active_term()
        
        if not term:
            return []
        
        conflicts = []
        
        # Get all enrollments for this student in the term
        enrollments = self.enrollment_set.filter(term=term)
        
        for enrollment in enrollments:
            # Get all scheduled groups this student is in
            scheduled_groups = enrollment.scheduledgroup_set.all()
            
            for group in scheduled_groups:
                # Check if this group's schedule conflicts with student availability
                conflict_info = self.has_scheduling_conflict(
                    group.day_of_week, 
                    group.time_slot
                )
                
                if conflict_info['has_conflict']:
                    conflicts.append({
                        'enrollment': enrollment,
                        'scheduled_group': group,
                        'conflict_info': conflict_info
                    })
        
        return conflicts

class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    
    class EnrollmentType(models.TextChoices):
        SOLO = 'SOLO', 'Solo'
        PAIR = 'PAIR', 'Pair'
        GROUP = 'GROUP', 'Group'
        
    enrollment_type = models.CharField(max_length=5, choices=EnrollmentType.choices)
    
    # Lesson balance tracking fields
    target_lessons = models.IntegerField(default=8, help_text="Target lessons for this term")
    lessons_carried_forward = models.IntegerField(default=0, help_text="Lessons owed from previous term (+) or credit (-)")
    adjusted_target = models.IntegerField(default=8, editable=False, help_text="Calculated: target_lessons + lessons_carried_forward")
    
    def save(self, *args, **kwargs):
        # Auto-calculate adjusted target
        self.adjusted_target = self.target_lessons + self.lessons_carried_forward
        super().save(*args, **kwargs)
    
    def get_lesson_balance(self):
        """Calculate current lesson balance (positive = owed lessons, negative = extra lessons)"""
        actual_lessons = self.attendancerecord_set.filter(status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']).count()
        return self.adjusted_target - actual_lessons
    
    def get_balance_status(self):
        """Get color-coded status for lesson balance"""
        balance = self.get_lesson_balance()
        if balance > 2:
            return {'status': 'owed', 'color': 'danger', 'text': f'{balance} lessons owed'}
        elif balance > 0:
            return {'status': 'slightly_owed', 'color': 'warning', 'text': f'{balance} lessons owed'}
        elif balance < -2:
            return {'status': 'credit', 'color': 'info', 'text': f'{abs(balance)} lessons credit'}
        elif balance < 0:
            return {'status': 'slight_credit', 'color': 'success', 'text': f'{abs(balance)} lessons credit'}
        else:
            return {'status': 'balanced', 'color': 'success', 'text': 'On target'}

    def __str__(self):
        # UPDATED: Removed the database query from here to prevent major performance issues
        # (N+1 queries) in the Django admin and other parts of the site.
        return f"{self.student} ({self.term.name} - {self.get_enrollment_type_display()})"

class ScheduledGroup(models.Model):
    name = models.CharField(max_length=200, help_text="e.g., Wednesday 4pm Advanced")
    coach = models.ForeignKey(Coach, on_delete=models.SET_NULL, null=True, blank=True)
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    members = models.ManyToManyField(Enrollment)
    
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 0, 'Monday'
        TUESDAY = 1, 'Tuesday'
        WEDNESDAY = 2, 'Wednesday'
        THURSDAY = 3, 'Thursday'
        FRIDAY = 4, 'Friday'
        SATURDAY = 5, 'Saturday'
        SUNDAY = 6, 'Sunday'
        
    day_of_week = models.IntegerField(choices=DayOfWeek.choices)
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, null=True)
    
    # Enhanced fields for intelligent slot finder
    target_skill_level = models.CharField(
        max_length=1,
        choices=Student.SkillLevel.choices,
        default=Student.SkillLevel.BEGINNER,
        help_text="Target skill level for this group"
    )
    max_capacity = models.IntegerField(
        default=4,
        help_text="Maximum number of students in this group"
    )
    preferred_size = models.IntegerField(
        default=3,
        help_text="Preferred number of students in this group"
    )
    
    # Group type for matching preferences
    group_type = models.CharField(
        max_length=5,
        choices=[('SOLO', 'Solo'), ('PAIR', 'Pair'), ('GROUP', 'Group')],
        default='GROUP',
        help_text="Type of group (Solo, Pair, or Group)"
    )

    def __str__(self):
        return self.name
    
    def get_current_size(self):
        """Get current number of students in group"""
        return self.members.count()
    
    def has_space(self):
        """Check if group has space for more students"""
        return self.get_current_size() < self.max_capacity
    
    def get_available_spaces(self):
        """Get number of available spaces"""
        return max(0, self.max_capacity - self.get_current_size())
    
    def is_at_preferred_size(self):
        """Check if group is at preferred size"""
        return self.get_current_size() == self.preferred_size
    
    def get_average_year_level(self):
        """Calculate average year level of current members"""
        if not self.members.exists():
            return 0
        
        year_levels = [member.student.year_level for member in self.members.all()]
        return sum(year_levels) / len(year_levels)
    
    def get_skill_level_distribution(self):
        """Get distribution of skill levels in group"""
        distribution = {'B': 0, 'I': 0, 'A': 0}
        for member in self.members.all():
            skill = member.student.skill_level
            distribution[skill] = distribution.get(skill, 0) + 1
        return distribution
    
    def is_compatible_with_student(self, student):
        """Check if student would be compatible with this group"""
        # Check skill level compatibility
        if abs(ord(student.skill_level) - ord(self.target_skill_level)) > 1:
            return False
        
        # Check year level compatibility (within 2 years)
        avg_year = self.get_average_year_level()
        if avg_year > 0 and abs(student.year_level - avg_year) > 2:
            return False
        
        # Check if group has space
        if not self.has_space():
            return False
        
        return True

class ScheduledUnavailability(models.Model):
    name = models.CharField(max_length=200, help_text="e.g., Year 4 Sports (Recurring)")
    students = models.ManyToManyField(Student, blank=True)
    school_classes = models.ManyToManyField(SchoolClass, blank=True)
    day_of_week = models.IntegerField(choices=ScheduledGroup.DayOfWeek.choices)
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, null=True)

    class Meta:
        verbose_name = "Scheduled Unavailability"
        verbose_name_plural = "Scheduled Unavailabilities"

    def __str__(self):
        return self.name

class OneOffEvent(models.Model):
    class EventType(models.TextChoices):
        PUBLIC_HOLIDAY = 'PUBLIC_HOLIDAY', 'Public Holiday'
        PUPIL_FREE_DAY = 'PUPIL_FREE_DAY', 'Pupil Free Day'
        CAMP = 'CAMP', 'Camp'
        EXCURSION = 'EXCURSION', 'Class Excursion'
        INDIVIDUAL = 'INDIVIDUAL', 'Individual Students'
        CUSTOM = 'CUSTOM', 'Custom Event'
    
    name = models.CharField(max_length=200, help_text="e.g., Year 4 Camp, Public Holiday")
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.CUSTOM, blank=True)
    event_date = models.DateField()
    end_date = models.DateField(blank=True, null=True, help_text="For multi-day events like camps")
    time_slots = models.ManyToManyField(TimeSlot, blank=True, help_text="Select one or more time slots. Leave blank for an all-day event.")
    students = models.ManyToManyField(Student, blank=True)
    school_classes = models.ManyToManyField(SchoolClass, blank=True)
    year_levels = models.CharField(max_length=50, blank=True, help_text="Comma-separated year levels (e.g., '3,4,5')")
    reason = models.CharField(max_length=255, help_text="Reason for absence, e.g., 'School Excursion'")
    is_processed = models.BooleanField(default=False, help_text="Whether this event has been processed")
    created_by = models.ForeignKey('Coach', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    
    class Meta:
        ordering = ['-event_date', '-created_at']
    
    def __str__(self):
        if self.end_date and self.end_date != self.event_date:
            return f"{self.name} ({self.event_date} to {self.end_date})"
        return f"{self.name} on {self.event_date}"
    
    def get_affected_students_count(self):
        """Calculate total number of students affected by this event"""
        count = 0
        
        # Count students from school classes
        for school_class in self.school_classes.all():
            count += school_class.student_set.count()
        
        # Add individual students (avoiding double counting)
        individual_students = self.students.exclude(
            school_class__in=self.school_classes.all()
        )
        count += individual_students.count()
        
        return count
    
    def get_date_range_display(self):
        """Get a nice display of the date range"""
        if self.end_date and self.end_date != self.event_date:
            return f"{self.event_date.strftime('%b %d')} - {self.end_date.strftime('%b %d, %Y')}"
        return self.event_date.strftime('%b %d, %Y')
    
    def is_multi_day(self):
        """Check if this is a multi-day event"""
        return self.end_date and self.end_date != self.event_date
    
    def get_duration_days(self):
        """Get the number of days this event spans"""
        if self.end_date:
            return (self.end_date - self.event_date).days + 1
        return 1
    
    @classmethod
    def create_multi_day_event(cls, name, event_type, start_date, end_date, **kwargs):
        """Create multiple single-day events for a multi-day period"""
        from datetime import timedelta
        
        events = []
        current_date = start_date
        day_count = 1
        total_days = (end_date - start_date).days + 1
        
        while current_date <= end_date:
            event_name = f"{name} - Day {day_count}" if total_days > 1 else name
            
            event = cls.objects.create(
                name=event_name,
                event_type=event_type,
                event_date=current_date,
                **kwargs
            )
            
            # Copy many-to-many relationships
            if 'school_classes' in kwargs:
                event.school_classes.set(kwargs['school_classes'])
            if 'students' in kwargs:
                event.students.set(kwargs['students'])
            if 'time_slots' in kwargs:
                event.time_slots.set(kwargs['time_slots'])
            
            events.append(event)
            current_date += timedelta(days=1)
            day_count += 1
        
        return events

class LessonSession(models.Model):
    scheduled_group = models.ForeignKey(ScheduledGroup, on_delete=models.CASCADE)
    lesson_date = models.DateField()
    
    class Status(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELED = 'CANCELED', 'Canceled'
        
    status = models.CharField(max_length=10, choices=Status.choices, default='SCHEDULED')

    def get_attendance_records(self):
        return self.attendancerecord_set.select_related('enrollment__student__school_class', 'lessonnote').all()

    def __str__(self):
        return f"{self.scheduled_group.name} on {self.lesson_date}"

# scheduler/models.py

class AttendanceRecord(models.Model):
    # These choices provide the reasons for the new buttons
    class AbsenceReason(models.TextChoices):
        SICK = 'SICK', 'Sick'
        TEACHER_REFUSAL = 'TEACHER_REFUSAL', 'Teacher Refusal'
        CLASS_EVENT = 'CLASS_EVENT', 'Class Event'
        CLASS_EMPTY = 'CLASS_EMPTY', 'Class Empty'
        OTHER = 'OTHER', 'Other'

    # Using TextChoices here is a best practice for consistency
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PRESENT = 'PRESENT', 'Present'
        ABSENT = 'ABSENT', 'Absent'
        FILL_IN = 'FILL_IN', 'Fill-in'
        SICK_PRESENT = 'SICK_PRESENT', 'Sick but Present'
        REFUSES_PRESENT = 'REFUSES_PRESENT', 'Present but Refuses'

    lesson_session = models.ForeignKey(LessonSession, on_delete=models.CASCADE)
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    
    # This field uses the choices defined above
    reason_for_absence = models.CharField(
        max_length=20,
        choices=AbsenceReason.choices,
        blank=True,
        null=True
    )

    class Meta:
        unique_together = ('lesson_session', 'enrollment')

    def __str__(self):
        return f"{self.enrollment.student} - {self.lesson_session.lesson_date} - {self.status}"
    
    # This is the crucial helper method that your template needs to work
    def get_absence_reasons(self):
        return self.AbsenceReason.choices
    
    def get_scheduling_conflict(self):
        """
        Check if this attendance record represents a scheduling conflict.
        Returns conflict info dict or None if no conflict.
        """
        # Get the lesson's day of week and time slot
        lesson_day = self.lesson_session.lesson_date.weekday()
        lesson_time_slot = self.lesson_session.scheduled_group.time_slot
        
        # Check if student has conflict at this time
        return self.enrollment.student.has_scheduling_conflict(lesson_day, lesson_time_slot)
    
    @property
    def has_conflict(self):
        """Quick property to check if this record has a scheduling conflict"""
        conflict_info = self.get_scheduling_conflict()
        return conflict_info['has_conflict'] if conflict_info else False
    
    @property
    def conflict_type(self):
        """Get the type of conflict (class/individual) or None"""
        conflict_info = self.get_scheduling_conflict()
        return conflict_info['conflict_type'] if conflict_info and conflict_info['has_conflict'] else None
    
    @property
    def conflict_description(self):
        """Get human-readable conflict description"""
        conflict_info = self.get_scheduling_conflict()
        return conflict_info['conflict_description'] if conflict_info and conflict_info['has_conflict'] else None

class LessonNote(models.Model):
    attendance_record = models.OneToOneField(AttendanceRecord, on_delete=models.CASCADE, related_name="lessonnote")
    
    class Understanding(models.TextChoices):
        EXCELLENT = 'EXCELLENT', 'Excellent'
        GOOD = 'GOOD', 'Good'
        NEEDS_REVIEW = 'NEEDS_REVIEW', 'Needs Review'
        
    student_understanding = models.CharField(max_length=15, choices=Understanding.choices, blank=True)
    topics_covered = models.TextField(blank=True, help_text="A brief summary of topics covered in the lesson.")
    coach_comments = models.TextField(blank=True, help_text="Private notes for the next lesson.")

    def __str__(self):
        return f"Notes for {self.attendance_record.enrollment.student} on {self.attendance_record.lesson_session.lesson_date}"
    
    def get_form(self):
        from .forms import LessonNoteForm
        return LessonNoteForm(instance=self)
