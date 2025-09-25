#!/usr/bin/env python
"""
Debug script to check why Alasdair's compatibility is failing
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')
django.setup()

from scheduler.models import Student, ScheduledGroup, Term, Enrollment

def debug_student_compatibility():
    print("🔍 DEBUGGING STUDENT COMPATIBILITY")
    print("=" * 50)
    
    # Get Alasdair (student ID 294)
    try:
        student = Student.objects.get(pk=294)
        print(f"✅ Found student: {student.first_name} {student.last_name}")
        print(f"   Skill Level: {student.skill_level}")
        print(f"   Year Level: {student.year_level}")
        print(f"   School Class: {student.school_class}")
    except Student.DoesNotExist:
        print("❌ Student 294 not found")
        return
    
    # Get Russell's Tuesday 11:30am Group (group ID 218)
    try:
        group = ScheduledGroup.objects.get(pk=218)
        print(f"\n✅ Found group: {group.name}")
        print(f"   Target Skill Level: {group.target_skill_level}")
        print(f"   Group Type: {group.group_type}")
        print(f"   Max Capacity: {group.max_capacity}")
        print(f"   Current Size: {group.get_current_size()}")
        print(f"   Has Space: {group.has_space()}")
    except ScheduledGroup.DoesNotExist:
        print("❌ Group 218 not found")
        return
    
    # Check current members
    print(f"\n📋 Current Group Members:")
    current_term = Term.get_active_term()
    members = group.members.filter(term=current_term)
    for i, member in enumerate(members, 1):
        print(f"   {i}. {member.student.first_name} {member.student.last_name}")
        print(f"      Skill: {member.student.skill_level}, Year: {member.student.year_level}")
        print(f"      Enrollment Type: {member.enrollment_type}")
    
    if not members.exists():
        print("   (No current members)")
    
    # Calculate average year level
    avg_year = group.get_average_year_level()
    print(f"\n📊 Group Statistics:")
    print(f"   Average Year Level: {avg_year}")
    
    # Test compatibility step by step
    print(f"\n🧪 COMPATIBILITY TESTS:")
    
    # 1. Skill level test
    skill_diff = abs(ord(student.skill_level) - ord(group.target_skill_level))
    skill_compatible = skill_diff <= 1
    print(f"   1. Skill Level Test:")
    print(f"      Student: {student.skill_level} vs Group Target: {group.target_skill_level}")
    print(f"      Difference: {skill_diff} (max allowed: 1)")
    print(f"      Result: {'✅ PASS' if skill_compatible else '❌ FAIL'}")
    
    # 2. Year level test
    if avg_year > 0:
        year_diff = abs(student.year_level - avg_year)
        year_compatible = year_diff <= 2
        print(f"   2. Year Level Test:")
        print(f"      Student: {student.year_level} vs Group Average: {avg_year}")
        print(f"      Difference: {year_diff} (max allowed: 2)")
        print(f"      Result: {'✅ PASS' if year_compatible else '❌ FAIL'}")
    else:
        year_compatible = True
        print(f"   2. Year Level Test:")
        print(f"      Group is empty, so year level test passes")
        print(f"      Result: ✅ PASS")
    
    # 3. Space test
    has_space = group.has_space()
    print(f"   3. Space Test:")
    print(f"      Current: {group.get_current_size()}/{group.get_type_based_max_capacity()}")
    print(f"      Result: {'✅ PASS' if has_space else '❌ FAIL'}")
    
    # Final compatibility
    overall_compatible = skill_compatible and year_compatible and has_space
    print(f"\n🎯 OVERALL COMPATIBILITY:")
    print(f"   Result: {'✅ COMPATIBLE' if overall_compatible else '❌ NOT COMPATIBLE'}")
    
    # Test the actual method
    method_result = group.is_compatible_with_student(student)
    print(f"   Method Result: {'✅ COMPATIBLE' if method_result else '❌ NOT COMPATIBLE'}")
    
    if overall_compatible != method_result:
        print("   ⚠️  MISMATCH between manual calculation and method!")

if __name__ == "__main__":
    debug_student_compatibility()
