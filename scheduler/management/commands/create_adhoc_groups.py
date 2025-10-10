from django.core.management.base import BaseCommand
from scheduler.models import Coach, TimeSlot, ScheduledGroup, Term


class Command(BaseCommand):
    help = 'Create ad-hoc scheduled groups for each coach and time slot combination'

    def handle(self, *args, **options):
        # Get the active term
        active_term = Term.get_active_term()
        if not active_term:
            self.stdout.write(
                self.style.ERROR('No active term found. Please set an active term first.')
            )
            return

        # Get all coaches and time slots
        coaches = Coach.objects.all()
        time_slots = TimeSlot.objects.all().order_by('start_time')

        created_count = 0
        existing_count = 0

        for coach in coaches:
            for time_slot in time_slots:
                # Create ad-hoc group name
                group_name = f"Ad-hoc Lesson - {coach} - {time_slot}"
                
                # Check if this ad-hoc group already exists
                existing_group = ScheduledGroup.objects.filter(
                    name=group_name,
                    coach=coach,
                    term=active_term,
                    time_slot=time_slot
                ).first()
                
                if existing_group:
                    existing_count += 1
                    self.stdout.write(f"  Already exists: {group_name}")
                else:
                    # Create the ad-hoc group
                    # Use Monday (0) as default day since these are for ad-hoc lessons
                    ad_hoc_group = ScheduledGroup.objects.create(
                        name=group_name,
                        coach=coach,
                        term=active_term,
                        day_of_week=0,  # Monday as placeholder - won't be used for scheduling
                        time_slot=time_slot,
                        group_type='GROUP',  # Default to group type
                        target_skill_level='B',  # Default to beginner
                        max_capacity=4  # Default capacity
                    )
                    # Don't add any members - these are for fill-ins only
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"  Created: {group_name}")
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f'\nCompleted! Created {created_count} new ad-hoc groups, '
                f'{existing_count} already existed.'
            )
        )
        
        if created_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    '\nNote: Ad-hoc groups are now available in the admin panel. '
                    'To create an extra lesson:\n'
                    '1. Go to Admin Panel → Lesson Sessions → Add Lesson Session\n'
                    '2. Select an "Ad-hoc Lesson" scheduled group\n'
                    '3. Choose your desired date\n'
                    '4. Save - the empty lesson will appear on your dashboard\n'
                    '5. Use the fill-in management to add students'
                )
            )
