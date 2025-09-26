#!/usr/bin/env python
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')
django.setup()

from scheduler.models import Student, ScheduledGroup, Term, Enrollment

def debug_pair_groups():
    print("=== DEBUGGING PAIR GROUPS ===")
    
    current_term = Term.get_active_term()
    
    # Find all PAIR groups
    pair_groups = ScheduledGroup.objects.filter(
        term=current_term,
        group_type='PAIR'
    ).select_related('coach').prefetch_related('members__student')
    
    print(f'Found {pair_groups.count()} PAIR groups:')
    
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    
    for group in pair_groups:
        day_name = day_names[group.day_of_week] if group.day_of_week < len(day_names) else f'Day {group.day_of_week}'
        current_size = group.get_current_size()
        
        print(f'\n{group.name}:')
        print(f'  Time: {day_name} {group.time_slot}')
        print(f'  Size: {current_size}/{group.max_capacity}')
        print(f'  Coach: {group.coach}')
        
        # Get current members
        members = []
        for member in group.members.all():
            student = member.student
            members.append(f'{student.first_name} {student.last_name} ({member.enrollment_type})')
        
        if members:
            print(f'  Members: {", ".join(members)}')
        else:
            print(f'  Members: None (EMPTY)')
    
    # Also check Thomas's availability
    print(f'\n=== THOMAS\'S AVAILABILITY ===')
    thomas = Student.objects.get(id=8)
    
    from scheduler.slot_finder import EnhancedSlotFinderEngine
    engine = EnhancedSlotFinderEngine()
    available_slots = engine.availability_checker.get_available_slots(thomas)
    
    print(f'Thomas is available at:')
    for day, time_slot in available_slots:
        day_name = day_names[day] if day < len(day_names) else f'Day {day}'
        print(f'  {day_name} {time_slot}')
    
    # Check if any PAIR groups overlap with Thomas's availability
    print(f'\n=== PAIR GROUPS AT THOMAS\'S AVAILABLE TIMES ===')
    overlapping_groups = []
    
    for day, time_slot in available_slots:
        day_name = day_names[day] if day < len(day_names) else f'Day {day}'
        
        pair_groups_at_time = ScheduledGroup.objects.filter(
            term=current_term,
            group_type='PAIR',
            day_of_week=day,
            time_slot=time_slot
        ).prefetch_related('members__student')
        
        if pair_groups_at_time.exists():
            print(f'\n{day_name} {time_slot}:')
            for group in pair_groups_at_time:
                current_size = group.get_current_size()
                print(f'  {group.name}: {current_size}/{group.max_capacity} students')
                overlapping_groups.append(group)
        else:
            print(f'{day_name} {time_slot}: No PAIR groups')
    
    if not overlapping_groups:
        print(f'\n❌ NO PAIR GROUPS OVERLAP WITH THOMAS\'S AVAILABILITY!')
        print(f'This is why no displacement recommendations are generated.')
    else:
        print(f'\n✅ Found {len(overlapping_groups)} PAIR groups at Thomas\'s available times')

if __name__ == "__main__":
    debug_pair_groups()
