#!/usr/bin/env python
"""
Debug script to check what students exist in the database
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')
django.setup()

from scheduler.models import Student, ScheduledGroup, Term, Enrollment

def debug_students():
    print("🔍 DEBUGGING AVAILABLE STUDENTS")
    print("=" * 50)
    
    # Get all students
    students = Student.objects.all().order_by('id')
    print(f"Total students in database: {students.count()}")
    
    if students.count() == 0:
        print("❌ No students found in database")
        return
    
    print(f"\nFirst 10 students:")
    for student in students[:10]:
        print(f"   ID {student.id}: {student.first_name} {student.last_name} (Year {student.year_level}, Skill: {student.skill_level})")
    
    # Check for PAIR students specifically
    print(f"\n🔍 PAIR Students:")
    current_term = Term.get_active_term()
    if current_term:
        pair_enrollments = Enrollment.objects.filter(
            term=current_term,
            enrollment_type='PAIR'
        ).select_related('student')
        
        print(f"Found {pair_enrollments.count()} PAIR students in current term:")
        for enrollment in pair_enrollments[:5]:
            student = enrollment.student
            print(f"   ID {student.id}: {student.first_name} {student.last_name}")
            print(f"      Year: {student.year_level}, Skill: {student.skill_level}")
            
            # Check current groups
            current_groups = ScheduledGroup.objects.filter(members=enrollment, term=current_term)
            if current_groups.exists():
                for group in current_groups:
                    print(f"      Currently in: {group.name}")
            else:
                print(f"      Currently in: No groups")
    else:
        print("❌ No active term found")
    
    # Check groups
    print(f"\n🔍 AVAILABLE GROUPS:")
    if current_term:
        groups = ScheduledGroup.objects.filter(term=current_term).order_by('id')
        print(f"Found {groups.count()} groups in current term:")
        for group in groups[:5]:
            print(f"   ID {group.id}: {group.name}")
            print(f"      Type: {group.group_type}, Size: {group.get_current_size()}/{group.max_capacity}")
    
    # Check if we can find a PAIR student to test with
    if current_term and pair_enrollments.exists():
        test_student = pair_enrollments.first().student
        print(f"\n🧪 TESTING WITH STUDENT: {test_student.first_name} {test_student.last_name} (ID: {test_student.id})")
        
        # Find a group with space
        available_groups = []
        for group in ScheduledGroup.objects.filter(term=current_term):
            if group.has_space():
                available_groups.append(group)
        
        if available_groups:
            test_group = available_groups[0]
            print(f"   Testing compatibility with: {test_group.name} (ID: {test_group.id})")
            
            # Test compatibility
            is_compatible = test_group.is_compatible_with_student(test_student)
            print(f"   Compatibility result: {'✅ COMPATIBLE' if is_compatible else '❌ NOT COMPATIBLE'}")
            
            # Show details
            print(f"   Group details:")
            print(f"      Target skill: {test_group.target_skill_level}")
            print(f"      Student skill: {test_student.skill_level}")
            print(f"      Group size: {test_group.get_current_size()}/{test_group.max_capacity}")
            print(f"      Has space: {test_group.has_space()}")
            
            # Show current members
            members = test_group.members.filter(term=current_term)
            print(f"      Current members: {members.count()}")
            for member in members:
                print(f"         - {member.student.first_name} {member.student.last_name} (Year {member.student.year_level})")

if __name__ == "__main__":
    debug_students()
