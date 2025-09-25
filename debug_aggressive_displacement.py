#!/usr/bin/env python
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')
django.setup()

from scheduler.models import Student, ScheduledGroup, Term, Enrollment
from scheduler.slot_finder import EnhancedSlotFinderEngine

def debug_aggressive_displacement():
    print("=== DEBUGGING AGGRESSIVE DISPLACEMENT ===")
    
    # Test with Thomas (ID 8) who had 0 recommendations before
    student = Student.objects.get(id=8)
    current_term = Term.get_active_term()
    
    print(f'Testing aggressive displacement for: {student.first_name} {student.last_name}')
    enrollment = student.enrollment_set.get(term=current_term)
    print(f'Enrollment type: {enrollment.enrollment_type}')
    
    # Get available slots
    engine = EnhancedSlotFinderEngine()
    available_slots = engine.availability_checker.get_available_slots(student)
    print(f'Available slots: {len(available_slots)}')
    
    # Check each available slot for PAIR_FULL groups
    for day, time_slot in available_slots:
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
        day_name = day_names[day] if day < len(day_names) else f'Day {day}'
        
        print(f'\n--- {day_name} {time_slot} ---')
        
        groups_at_time = ScheduledGroup.objects.filter(
            term=current_term,
            day_of_week=day,
            time_slot=time_slot
        ).select_related('coach').prefetch_related('members__student')
        
        for group in groups_at_time:
            # Check if it's a PAIR group
            if not engine.compatibility_scorer._is_group_type_compatible('PAIR', group.group_type):
                print(f'  {group.name}: Not PAIR compatible (type: {group.group_type})')
                continue
            
            # Get effective group type
            effective_group_type = engine._get_effective_group_type(group, current_term)
            current_size = group.get_current_size()
            
            print(f'  {group.name}: {effective_group_type}, size: {current_size}')
            
            # Check if it's PAIR_FULL
            if effective_group_type == 'PAIR_FULL' and current_size == 2:
                print(f'    💥 PAIR_FULL GROUP FOUND!')
                
                # Get current students
                current_students = []
                for member in group.members.all():
                    existing_student = member.student
                    if existing_student.id != student.id:
                        score = engine.compatibility_scorer.calculate_compatibility_score(
                            existing_student, group, group.coach
                        )
                        current_students.append((existing_student, score['total_score']))
                        print(f'      Current student: {existing_student.first_name} (score: {score["total_score"]})')
                
                if len(current_students) >= 1:
                    # Sort by score - find weakest fit
                    current_students.sort(key=lambda x: x[1])
                    weakest_student, weakest_score = current_students[0]
                    
                    print(f'      Weakest fit: {weakest_student.first_name} (score: {weakest_score})')
                    
                    # Calculate new student's score
                    new_student_score = engine.compatibility_scorer.calculate_compatibility_score(
                        student, group, group.coach
                    )
                    
                    print(f'      New student score: {new_student_score["total_score"]}')
                    print(f'      Score difference: {new_student_score["total_score"] - weakest_score}')
                    
                    # Check threshold
                    threshold_met = new_student_score['total_score'] >= weakest_score - 20
                    print(f'      Threshold met (-20): {threshold_met}')
                    
                    if threshold_met:
                        # Check alternatives for displaced student
                        displaced_alternatives = engine._find_direct_placements(weakest_student)
                        print(f'      Displaced alternatives: {len(displaced_alternatives)}')
                        
                        if len(displaced_alternatives) > 0:
                            best_alternative = max(displaced_alternatives, key=lambda x: x.score)
                            print(f'      Best alternative: {best_alternative.group.name} (score: {best_alternative.score})')
                            print(f'      ✅ DISPLACEMENT SHOULD BE CREATED!')
                        else:
                            print(f'      ❌ No alternatives for displaced student')
                    else:
                        print(f'      ❌ Threshold not met')

if __name__ == "__main__":
    debug_aggressive_displacement()
