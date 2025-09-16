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

class Student(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    year_level = models.IntegerField(help_text="e.g., 3 for Year 3")
    school_class = models.ForeignKey(SchoolClass, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        # Safely gets the school class name if it exists.
        school_class_name = getattr(self.school_class, 'name', 'N/A')
        return f"{self.first_name} {self.last_name} ({school_class_name})"

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

    def __str__(self):
        return self.name

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
    name = models.CharField(max_length=200, help_text="e.g., Year 4 Camp, Public Holiday")
    event_date = models.DateField()
    time_slots = models.ManyToManyField(TimeSlot, blank=True, help_text="Select one or more time slots. Leave blank for an all-day event.")
    students = models.ManyToManyField(Student, blank=True)
    school_classes = models.ManyToManyField(SchoolClass, blank=True)
    reason = models.CharField(max_length=255, help_text="Reason for absence, e.g., 'School Excursion'")
    
    def __str__(self):
        return f"{self.name} on {self.event_date}"

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
