#!/usr/bin/env python
"""
Test script for the enhanced lesson import with lesson balance functionality
"""

import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')
django.setup()

from scheduler.models import Term, Enrollment, Student
from scheduler.admin_views import parse_student_name_and_class

def test_lesson_balance_import():
    """Test the lesson balance import functionality"""
    
    print("=== Testing Lesson Balance Import Functionality ===\n")
    
    # Test 1: Parse student name and class
    print("1. Testing student name parsing:")
    test_names = [
        "Johnny Ratcliffe (6B)-2",
        "Knox Black (6J)-2", 
        "Isabella Jennings (6J)-1",
        "Channing Claydon (2L)-3",
        "Jack Gordon (3H)-3"
    ]
    
    for name in test_names:
        try:
            first_name, last_name, school_class = parse_student_name_and_class(name)
            print(f"  ✓ '{name}' → {first_name} {last_name} ({school_class})")
        except Exception as e:
            print(f"  ✗ '{name}' → Error: {e}")
    
    print()
    
    # Test 2: Check active term
    print("2. Checking active term:")
    active_term = Term.get_active_term()
    if active_term:
        print(f"  ✓ Active term: {active_term.name}")
    else:
        print("  ✗ No active term found")
    
    print()
    
    # Test 3: Check existing enrollments and their lesson balances
    print("3. Checking existing enrollments and lesson balances:")
    enrollments = Enrollment.objects.all()[:10]  # Show first 10
    
    if enrollments:
        for enrollment in enrollments:
            balance = enrollment.get_lesson_balance()
            status = enrollment.get_balance_status()
            print(f"  {enrollment.student.first_name} {enrollment.student.last_name}: "
                  f"Target={enrollment.target_lessons}, "
                  f"Carried={enrollment.lessons_carried_forward}, "
                  f"Adjusted={enrollment.adjusted_target}, "
                  f"Balance={balance} ({status['text']})")
    else:
        print("  No enrollments found")
    
    print()
    
    # Test 4: Simulate lesson balance updates
    print("4. Testing lesson balance update logic:")
    test_cases = [
        ("Johnny Ratcliffe", 1),   # Owes 1 lesson
        ("Knox Black", 1),         # Owes 1 lesson  
        ("Isabella Jennings", 1),  # Owes 1 lesson
        ("Channing Claydon", -1),  # Has 1 credit
        ("Jack Gordon", -1),       # Has 1 credit
    ]
    
    for student_name, lessons_left in test_cases:
        try:
            # Find student by first name (simplified for testing)
            first_name = student_name.split()[0]
            student = Student.objects.filter(first_name=first_name).first()
            
            if student and active_term:
                enrollment = Enrollment.objects.filter(student=student, term=active_term).first()
                if enrollment:
                    old_balance = enrollment.lessons_carried_forward
                    enrollment.lessons_carried_forward = lessons_left
                    enrollment.save()  # This will auto-calculate adjusted_target
                    
                    print(f"  ✓ {student_name}: {old_balance} → {lessons_left} "
                          f"(Adjusted target: {enrollment.adjusted_target})")
                else:
                    print(f"  ✗ {student_name}: No enrollment found for active term")
            else:
                print(f"  ✗ {student_name}: Student not found or no active term")
                
        except Exception as e:
            print(f"  ✗ {student_name}: Error - {e}")
    
    print()
    print("=== Test Complete ===")

if __name__ == "__main__":
    test_lesson_balance_import()
