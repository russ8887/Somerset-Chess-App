from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import ScheduledGroup, LessonSession, Term
from datetime import timedelta

@receiver(post_save, sender=ScheduledGroup)
def create_lesson_sessions_for_group(sender, instance, created, **kwargs):
    """
    Automatically create all lesson sessions for a group for the entire term.
    """
    # Delete existing future sessions to handle updates
    LessonSession.objects.filter(scheduled_group=instance).delete()

    term = instance.term
    start_date = term.start_date
    end_date = term.end_date
    day_of_week = instance.day_of_week

    current_date = start_date
    while current_date <= end_date:
        if current_date.weekday() == day_of_week:
            LessonSession.objects.create(
                scheduled_group=instance,
                lesson_date=current_date
            )
        current_date += timedelta(days=1)