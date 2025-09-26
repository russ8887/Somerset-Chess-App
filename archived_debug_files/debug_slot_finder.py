#!/usr/bin/env python
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')
django.setup()

from scheduler.models import Student, ScheduledGroup, Term, Enrollment

def debug_pair_student():
    print("=== DEBUGGING PAIR STUDENT SLOT FINDER ===")
    
    # Get a PAIR student
    current_term = Term.get_active_term()
    print(f"Active term: {current_term}")
    
    pair_students = Enrollment.objects.filter(term=current_term, enrollment_type='PAIR').select_related('student')[:3]
    
    for enrollment in pair_students:
        student = enrollment.student
        print(f"\n--- Student: {student.first_name} {student.last_name} (ID: {student.id}) ---")
        
        # Check current groups
        current_groups = ScheduledGroup.objects.filter(members=enrollment, term=current_term)
        print(f"Current groups: {[g.name for g in current_groups]}")
        
        for group in current_groups:
            print(f"  - {group.name} (Day {group.day_of_week}, {group.time_slot})")
            members = group.members.filter(term=current_term)
            print(f"    Members: {[(m.student.first_name, m.enrollment_type) for m in members]}")
        
        # Test slot finder
        print(f"\nTesting slot finder for {student.first_name}...")
        try:
            from scheduler.views import find_better_slot_api
            # We can't easily test the API directly, so let's test the core logic
            from scheduler.slot_finder import EnhancedSlotFinderEngine
            
            engine = EnhancedSlotFinderEngine()
            recommendations = engine.find_optimal_slots(
                student, 
                max_results=10,
                include_swaps=True,
                include_chains=True,
                max_time_seconds=30
            )
            
            print(f"Found {len(recommendations)} recommendations:")
            for i, rec in enumerate(recommendations):
                print(f"  {i+1}. {rec.group.name} (Score: {rec.score}, Type: {rec.placement_type})")
                print(f"      Day {rec.group.day_of_week}, {rec.group.time_slot}")
                print(f"      Current size: {rec.group.get_current_size()}/{rec.group.max_capacity}")
                
        except Exception as e:
            print(f"Error testing slot finder: {e}")
            import traceback
            traceback.print_exc()
        
        break  # Just test the first student

def debug_group_types():
    print("\n=== DEBUGGING GROUP TYPES ===")
    
    current_term = Term.get_active_term()
    groups = ScheduledGroup.objects.filter(term=current_term)[:10]
    
    for group in groups:
        members = group.members.filter(term=current_term)
        member_types = [m.enrollment_type for m in members]
        print(f"\nGroup: {group.name}")
        print(f"  Static type: {group.group_type}")
        print(f"  Members: {len(members)}/{group.max_capacity}")
        print(f"  Member types: {member_types}")
        
        # Test dynamic type detection
        try:
            from scheduler.slot_finder import EnhancedSlotFinderEngine
            engine = EnhancedSlotFinderEngine()
            dynamic_type = engine._get_effective_group_type(group, current_term)
            print(f"  Dynamic type: {dynamic_type}")
        except Exception as e:
            print(f"  Dynamic type error: {e}")

if __name__ == "__main__":
    debug_pair_student()
    debug_group_types()
