#!/usr/bin/env python
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')
django.setup()

from scheduler.models import Student, ScheduledGroup, Term, Enrollment
from scheduler.slot_finder import EnhancedSlotFinderEngine

def test_enhanced_slot_finder():
    print("=== TESTING ENHANCED SLOT FINDER ===")
    
    # Test with Thomas (ID 8) who had 0 recommendations before
    student = Student.objects.get(id=8)
    current_term = Term.get_active_term()
    
    print(f'Testing enhanced slot finder for: {student.first_name} {student.last_name}')
    enrollment = student.enrollment_set.get(term=current_term)
    print(f'Enrollment type: {enrollment.enrollment_type}')
    
    current_groups = list(enrollment.scheduledgroup_set.all())
    print(f'Current groups: {[g.name for g in current_groups]}')
    
    # Test the ENHANCED slot finder with displacement enabled
    engine = EnhancedSlotFinderEngine()
    try:
        recommendations = engine.find_optimal_slots(
            student, 
            max_results=20,  # More results
            include_swaps=True,
            include_chains=True,
            max_time_seconds=30
        )
        
        print(f'Found {len(recommendations)} recommendations:')
        for i, rec in enumerate(recommendations):
            print(f'  {i+1}. {rec.group.name} (Score: {rec.score}, Type: {rec.placement_type})')
            if hasattr(rec, 'benefits') and rec.benefits:
                if 'current_size' in rec.benefits:
                    print(f'      Current size: {rec.benefits["current_size"]}/{rec.group.max_capacity}')
                if 'enrollment_type' in rec.benefits:
                    print(f'      Student type: {rec.benefits["enrollment_type"]}')
                if 'effective_group_type' in rec.benefits:
                    print(f'      Group type: {rec.benefits["effective_group_type"]}')
            if hasattr(rec, 'swap_chain') and rec.swap_chain:
                print(f'      Swap chain: {len(rec.swap_chain)} moves')
                
    except Exception as e:
        print(f'Error testing enhanced slot finder: {e}')
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_enhanced_slot_finder()
