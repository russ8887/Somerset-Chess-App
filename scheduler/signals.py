from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import ScheduledGroup, LessonSession, Term
from datetime import timedelta

@receiver(post_save, sender=ScheduledGroup)
def create_lesson_sessions_for_group(sender, instance, created, **kwargs):
    """
    Automatically create all lesson sessions for a group for the entire term.
    CRITICAL FIX: Only delete future sessions, NEVER delete past sessions with attendance data.
    """
    from datetime import date
    today = date.today()
    
    # SAFE DELETION: Only delete FUTURE lesson sessions to prevent data loss
    # This preserves all historical attendance records
    future_sessions = LessonSession.objects.filter(
        scheduled_group=instance,
        lesson_date__gt=today  # Only future dates
    )
    
    # Check if any future sessions have attendance records (shouldn't happen, but be safe)
    sessions_with_records = future_sessions.filter(attendancerecord__isnull=False).distinct()
    if sessions_with_records.exists():
        # Don't delete sessions that already have attendance data
        future_sessions = future_sessions.exclude(
            id__in=sessions_with_records.values_list('id', flat=True)
        )
    
    # Safe to delete only empty future sessions
    future_sessions.delete()

    term = instance.term
    start_date = max(term.start_date, today)  # Don't create past sessions
    end_date = term.end_date
    day_of_week = instance.day_of_week

    current_date = start_date
    while current_date <= end_date:
        if current_date.weekday() == day_of_week:
            # Use get_or_create to avoid duplicates
            LessonSession.objects.get_or_create(
                scheduled_group=instance,
                lesson_date=current_date
            )
        current_date += timedelta(days=1)
