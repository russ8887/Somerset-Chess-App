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
    
    # Student status tracking
    is_active = models.BooleanField(
        default=True, 
        help_text="Inactive students are removed from future lessons but keep historical records"
    )
    withdrawal_date = models.DateField(
        null=True, 
        blank=True, 
        help_text="Date when student became inactive (optional)"
    )
    withdrawal_reason = models.CharField(
        max_length=200, 
        blank=True, 
        help_text="Reason for withdrawal (optional)"
    )
    
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
        help_text="Maximum number of students in this group (auto-calculated based on group type)"
    )
    preferred_size = models.IntegerField(
        default=3,
        help_text="Preferred number of students in this group (auto-calculated based on group type)"
    )
    
    # Group type for matching preferences
    group_type = models.CharField(
        max_length=5,
        choices=[('SOLO', 'Solo'), ('PAIR', 'Pair'), ('GROUP', 'Group')],
        default='GROUP',
        help_text="Type of group (Solo, Pair, or Group)"
    )
    
    def save(self, *args, **kwargs):
        """Auto-set capacity based on group type"""
        if self.group_type == 'SOLO':
            self.max_capacity = 1
            self.preferred_size = 1
        elif self.group_type == 'PAIR':
            self.max_capacity = 2
            self.preferred_size = 2
        elif self.group_type == 'GROUP':
            self.max_capacity = 3
            self.preferred_size = 3
        super().save(*args, **kwargs)
    
    def get_type_based_max_capacity(self):
        """Get the correct max capacity based on group type"""
        capacity_map = {
            'SOLO': 1,
            'PAIR': 2,
            'GROUP': 3
        }
        return capacity_map.get(self.group_type, 3)
    
    def get_type_based_preferred_size(self):
        """Get the correct preferred size based on group type"""
        return self.get_type_based_max_capacity()  # Same as max for our use case

    def __str__(self):
        return self.name
    
    def get_current_size(self):
        """Get current number of students in group"""
        return self.members.count()
    
    def has_space(self):
        """Check if group has space for more students"""
        return self.get_current_size() < self.get_type_based_max_capacity()
    
    def get_available_spaces(self):
        """Get number of available spaces"""
        return max(0, self.get_type_based_max_capacity() - self.get_current_size())
    
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
    
    def is_compatible_with_student(self, student, student_enrollment_type=None):
        """Check if student would be compatible with this group"""
        # Check if group has space first (most important)
        if not self.has_space():
            return False
        
        # Get the dynamic group type based on current members
        try:
            from .slot_finder import EnhancedSlotFinderEngine
            slot_finder = EnhancedSlotFinderEngine()
            current_term = Term.get_active_term()
            
            if current_term:
                dynamic_type = slot_finder._get_effective_group_type(self, current_term)
            else:
                dynamic_type = 'UNKNOWN'
        except ImportError:
            dynamic_type = 'UNKNOWN'
            
            # For PAIR_WAITING groups, be more flexible with PAIR students
            if dynamic_type == 'PAIR_WAITING' and student_enrollment_type == 'PAIR':
                # PAIR students can join PAIR_WAITING groups with relaxed rules
                # Only check if skill levels are within 2 levels (more flexible)
                skill_diff = abs(ord(student.skill_level) - ord(self.target_skill_level))
                if skill_diff > 2:  # Allow B->I, I->A, but not B->A
                    return False
                
                # More flexible year level check for PAIR students (within 3 years)
                avg_year = self.get_average_year_level()
                if avg_year > 0 and abs(student.year_level - avg_year) > 3:
                    return False
                
                return True
        
        # Standard compatibility checks for other cases
        # Check skill level compatibility (within 1 level)
        if abs(ord(student.skill_level) - ord(self.target_skill_level)) > 1:
            return False
        
        # Check year level compatibility (within 2 years)
        avg_year = self.get_average_year_level()
        if avg_year > 0 and abs(student.year_level - avg_year) > 2:
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
        """
        HISTORICAL-AWARE ROSTER GENERATION: 
        - For PAST lessons: Show students based on existing attendance records (preserves history)
        - For FUTURE lessons: Show current group members + create records as needed
        - For TODAY: Show current group members + create records as needed
        This ensures historical accuracy while allowing future planning.
        """
        from datetime import date
        today = date.today()
        
        # For past lessons, use historical attendance records only
        if self.lesson_date < today:
            # Return existing attendance records - don't create new ones
            existing_records = AttendanceRecord.objects.filter(
                lesson_session=self
            ).select_related('enrollment__student__school_class').prefetch_related('lessonnote').order_by('enrollment__student__last_name')
            
            return existing_records
        
        # For today and future lessons, use current group members + fill-ins
        # EXCLUDE INACTIVE ENROLLMENTS from future lessons
        current_members = self.scheduled_group.members.filter(is_active=True)
        records = []
        
        # 1. Create/get records for ACTIVE current group members only
        for enrollment in current_members:
            record, created = AttendanceRecord.objects.get_or_create(
                lesson_session=self,
                enrollment=enrollment,
                defaults={'status': 'PENDING'}
            )
            records.append(record)
        
        # 2. Get any existing fill-in records that aren't already included (both FILL_IN and FILL_IN_ABSENT)
        existing_enrollment_ids = [enrollment.id for enrollment in current_members]
        fill_in_records = AttendanceRecord.objects.filter(
            lesson_session=self,
            status__in=['FILL_IN', 'FILL_IN_ABSENT']
        ).exclude(enrollment_id__in=existing_enrollment_ids)
        
        # Add fill-in records to the list
        records.extend(fill_in_records)
        
        # Prefetch related data for performance
        all_record_ids = [r.id for r in records]
        return AttendanceRecord.objects.filter(
            id__in=all_record_ids
        ).select_related('enrollment__student__school_class').prefetch_related('lessonnote').order_by('enrollment__student__last_name')

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
        FILL_IN_ABSENT = 'FILL_IN_ABSENT', 'Fill-in Absent'
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
        
    student_understanding = models.CharField(max_length=500, choices=Understanding.choices, blank=True)
    topics_covered = models.TextField(blank=True, help_text="A brief summary of topics covered in the lesson.")
    coach_comments = models.TextField(blank=True, help_text="Private notes for the next lesson.")

    def __str__(self):
        return f"Notes for {self.attendance_record.enrollment.student} on {self.attendance_record.lesson_session.lesson_date}"
    
    def get_form(self):
        from .forms import LessonNoteForm
        return LessonNoteForm(instance=self)


# =============================================================================
# CHESS TRAINING CURRICULUM SYSTEM
# =============================================================================

class CurriculumLevel(models.Model):
    """
    Represents the main levels of chess curriculum (Foundation, Intermediate, Advanced, etc.)
    """
    class Level(models.TextChoices):
        FOUNDATION = 'FOUNDATION', 'Foundation (400-600 ELO)'
        TACTICAL = 'TACTICAL', 'Tactical Awareness (600-800 ELO)'
        STRATEGIC = 'STRATEGIC', 'Strategic Thinking (800-1000 ELO)'
        ADVANCED = 'ADVANCED', 'Advanced Concepts (1000-1200 ELO)'
        MASTERY = 'MASTERY', 'Mastery Path (1200+ ELO)'
    
    name = models.CharField(max_length=20, choices=Level.choices, unique=True)
    description = models.TextField(help_text="Description of what students learn at this level")
    min_elo = models.IntegerField(help_text="Minimum ELO for this level")
    max_elo = models.IntegerField(help_text="Maximum ELO for this level")
    sort_order = models.IntegerField(default=0, help_text="Order in which levels should be completed")
    
    class Meta:
        ordering = ['sort_order']
        verbose_name = "Curriculum Level"
        verbose_name_plural = "Curriculum Levels"
    
    def __str__(self):
        return f"{self.get_name_display()}"


class CurriculumTopic(models.Model):
    """
    Individual topics/lessons within the curriculum (e.g., "Rook Movement", "Knight Forks")
    """
    level = models.ForeignKey(CurriculumLevel, on_delete=models.CASCADE, related_name='topics')
    name = models.CharField(max_length=200, help_text="Topic name (e.g., 'Rook Movement')")
    category = models.CharField(max_length=100, help_text="Category (e.g., 'Piece Basics', 'Basic Tactics')")
    sort_order = models.IntegerField(default=0, help_text="Order within the level")
    
    # Teaching Instructions
    learning_objective = models.TextField(help_text="What the student must understand/demonstrate")
    teaching_method = models.TextField(help_text="Step-by-step instructions for coaches")
    practice_activities = models.TextField(help_text="Hands-on exercises and games")
    pass_criteria = models.TextField(help_text="Specific requirements to pass this topic")
    enhancement_activities = models.TextField(
        blank=True, 
        help_text="Extra activities for students who master quickly"
    )
    common_mistakes = models.TextField(
        blank=True, 
        help_text="What coaches should watch out for"
    )
    
    # Time and Prerequisites
    estimated_time_min = models.IntegerField(
        default=15, 
        help_text="Estimated time in minutes for average student"
    )
    estimated_time_max = models.IntegerField(
        default=30, 
        help_text="Maximum time including enhancements"
    )
    elo_points = models.IntegerField(
        default=10, 
        help_text="ELO points awarded for mastering this topic"
    )
    
    # Status and metadata
    is_active = models.BooleanField(default=True, help_text="Whether this topic is currently used")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['level__sort_order', 'sort_order']
        unique_together = ['level', 'name']
        verbose_name = "Curriculum Topic"
        verbose_name_plural = "Curriculum Topics"
    
    def __str__(self):
        return f"{self.level.get_name_display()} - {self.name}"
    
    def get_prerequisites(self):
        """Get all topics that must be completed before this one"""
        return CurriculumTopic.objects.filter(
            topicprerequisite__required_for=self
        ).order_by('level__sort_order', 'sort_order')
    
    def get_next_topics(self):
        """Get topics that become available after completing this one"""
        return CurriculumTopic.objects.filter(
            topicprerequisite__prerequisite=self
        ).order_by('level__sort_order', 'sort_order')


class TopicPrerequisite(models.Model):
    """
    Defines which topics must be completed before others
    """
    prerequisite = models.ForeignKey(
        CurriculumTopic, 
        on_delete=models.CASCADE, 
        related_name='unlocks'
    )
    required_for = models.ForeignKey(
        CurriculumTopic, 
        on_delete=models.CASCADE, 
        related_name='prerequisites'
    )
    is_strict = models.BooleanField(
        default=True, 
        help_text="If True, prerequisite MUST be completed. If False, it's just recommended."
    )
    
    class Meta:
        unique_together = ['prerequisite', 'required_for']
        verbose_name = "Topic Prerequisite"
        verbose_name_plural = "Topic Prerequisites"
    
    def __str__(self):
        strict = " (Required)" if self.is_strict else " (Recommended)"
        return f"{self.prerequisite.name} → {self.required_for.name}{strict}"


class StudentProgress(models.Model):
    """
    Tracks individual student progress through the curriculum
    """
    class Status(models.TextChoices):
        NOT_STARTED = 'NOT_STARTED', 'Not Started'
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        MASTERED = 'MASTERED', 'Mastered'
        NEEDS_REVIEW = 'NEEDS_REVIEW', 'Needs Review'
        FAILED = 'FAILED', 'Failed Assessment'
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='curriculum_progress')
    topic = models.ForeignKey(CurriculumTopic, on_delete=models.CASCADE, related_name='student_progress')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    
    # Progress tracking
    attempts = models.IntegerField(default=0, help_text="Number of times this topic has been attempted")
    mastery_date = models.DateField(null=True, blank=True, help_text="Date when topic was mastered")
    last_attempted_date = models.DateField(null=True, blank=True)
    last_lesson_session = models.ForeignKey(
        LessonSession, 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL,
        help_text="Last lesson where this topic was worked on"
    )
    
    # Assessment details
    coach_notes = models.TextField(blank=True, help_text="Coach observations and notes")
    pass_percentage = models.IntegerField(
        null=True, 
        blank=True, 
        help_text="Percentage score on assessment (0-100)"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['student', 'topic']
        ordering = ['topic__level__sort_order', 'topic__sort_order']
        verbose_name = "Student Progress"
        verbose_name_plural = "Student Progress"
    
    def __str__(self):
        return f"{self.student} - {self.topic.name} ({self.get_status_display()})"
    
    def is_due_for_recap(self):
        """Check if this mastered topic is due for a recap session"""
        if self.status != self.Status.MASTERED or not self.mastery_date:
            return False
        
        try:
            recap = self.recap_schedule.get()
            return recap.is_due()
        except RecapSchedule.DoesNotExist:
            # No recap schedule exists, create one
            RecapSchedule.create_for_progress(self)
            return RecapSchedule.objects.get(progress=self).is_due()
    
    def calculate_current_elo(self):
        """Calculate student's current ELO based on mastered topics"""
        mastered_topics = StudentProgress.objects.filter(
            student=self.student,
            status=self.Status.MASTERED
        ).select_related('topic')
        
        base_elo = 400  # Starting ELO
        earned_points = sum(progress.topic.elo_points for progress in mastered_topics)
        
        return base_elo + earned_points


class RecapSchedule(models.Model):
    """
    Manages the spaced repetition schedule for reviewing mastered topics
    """
    progress = models.OneToOneField(
        StudentProgress, 
        on_delete=models.CASCADE, 
        related_name='recap_schedule'
    )
    
    # Spaced repetition intervals (in lessons)
    current_interval = models.IntegerField(default=4, help_text="Current interval between recaps")
    next_recap_lesson = models.IntegerField(help_text="Lesson number when recap is due")
    
    # Recap history
    total_recaps = models.IntegerField(default=0, help_text="Total number of recaps completed")
    successful_recaps = models.IntegerField(default=0, help_text="Number of successful recaps")
    last_recap_date = models.DateField(null=True, blank=True)
    last_recap_result = models.CharField(
        max_length=10,
        choices=[
            ('PASS', 'Passed'),
            ('REVIEW', 'Needs Review'),
            ('FAIL', 'Failed')
        ],
        null=True,
        blank=True
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Recap Schedule"
        verbose_name_plural = "Recap Schedules"
    
    def __str__(self):
        return f"{self.progress.student} - {self.progress.topic.name} (Next: Lesson {self.next_recap_lesson})"
    
    @classmethod
    def create_for_progress(cls, progress):
        """Create a recap schedule for newly mastered progress"""
        if progress.status != StudentProgress.Status.MASTERED:
            raise ValueError("Can only create recap schedule for mastered topics")
        
        # Calculate next recap lesson number
        current_lesson_count = AttendanceRecord.objects.filter(
            enrollment__student=progress.student,
            status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
        ).count()
        
        next_lesson = current_lesson_count + 4  # First recap after 4 lessons
        
        return cls.objects.create(
            progress=progress,
            current_interval=4,
            next_recap_lesson=next_lesson
        )
    
    def is_due(self):
        """Check if recap is currently due"""
        current_lesson_count = AttendanceRecord.objects.filter(
            enrollment__student=self.progress.student,
            status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
        ).count()
        
        return current_lesson_count >= self.next_recap_lesson
    
    def mark_recap_completed(self, result):
        """Mark a recap as completed and schedule the next one"""
        from datetime import date
        
        self.last_recap_date = date.today()
        self.last_recap_result = result
        self.total_recaps += 1
        
        if result == 'PASS':
            self.successful_recaps += 1
            # Double the interval for successful recaps (4 → 8 → 16 → 32)
            self.current_interval = min(self.current_interval * 2, 32)
        elif result == 'REVIEW':
            # Keep same interval for topics that need review
            pass
        else:  # FAIL
            # Reset to shorter interval for failed recaps
            self.current_interval = 4
            # Mark the original progress as needing review
            self.progress.status = StudentProgress.Status.NEEDS_REVIEW
            self.progress.save()
        
        # Schedule next recap
        current_lesson_count = AttendanceRecord.objects.filter(
            enrollment__student=self.progress.student,
            status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
        ).count()
        
        self.next_recap_lesson = current_lesson_count + self.current_interval
        self.save()
