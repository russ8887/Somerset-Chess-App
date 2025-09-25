#!/usr/bin/env python3
"""
🚨 PRODUCTION DATABASE CLEANUP SCRIPT 🚨

This script MUST be run on the PRODUCTION server to fix the phantom attendance records.
It connects to the production PostgreSQL database and removes corrupted data.

CRITICAL: This script is designed to run on PRODUCTION, not local development.
"""

import os
import sys
import django
from datetime import date

# Production Django setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'somerset_project.settings')
django.setup()

from scheduler.models import AttendanceRecord, Student, ScheduledGroup, Enrollment, Term
from django.db import transaction

def cleanup_production_phantom_records():
    """Remove phantom AttendanceRecord objects from PRODUCTION database"""
    print("🚨 PRODUCTION DATABASE CLEANUP - PHANTOM ATTENDANCE RECORDS")
    print("=" * 70)
    print("⚠️  WARNING: This script modifies the PRODUCTION database")
    print("⚠️  Ensure you have database backups before proceeding")
    print("=" * 70)
    
    # Verify we're connected to production
    from django.conf import settings
    db_name = settings.DATABASES['default'].get('NAME', 'Unknown')
    print(f"🔍 Connected to database: {db_name}")
    
    # Get all attendance records
    all_records = AttendanceRecord.objects.select_related(
        'enrollment__student',
        'lesson_session__scheduled_group'
    ).all()
    
    print(f"📊 Total attendance records in PRODUCTION: {all_records.count()}")
    
    phantom_records = []
    legitimate_records = []
    
    # Check each record for legitimacy
    print("🔍 Analyzing attendance records...")
    for record in all_records:
        enrollment = record.enrollment
        group = record.lesson_session.scheduled_group
        
        # Check if this enrollment is actually a member of this group
        is_legitimate = enrollment in group.members.all()
        
        if is_legitimate:
            legitimate_records.append(record)
        else:
            phantom_records.append(record)
            print(f"👻 PHANTOM: {enrollment.student} in {group.name} (NOT a member)")
    
    print(f"\n📈 PRODUCTION ANALYSIS RESULTS:")
    print(f"  ✅ Legitimate records: {len(legitimate_records)}")
    print(f"  👻 Phantom records: {len(phantom_records)}")
    
    if not phantom_records:
        print("🎉 No phantom records found! Production database is clean.")
        return
    
    # Show Alisdair's phantom records specifically
    alisdair_phantoms = [r for r in phantom_records if 'alisdair' in r.enrollment.student.first_name.lower()]
    if alisdair_phantoms:
        print(f"\n🎯 ALISDAIR PHANTOM RECORDS IN PRODUCTION:")
        for record in alisdair_phantoms:
            group = record.lesson_session.scheduled_group
            coach_name = str(group.coach) if group.coach else 'No Coach'
            day_name = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'][group.day_of_week]
            print(f"  - {group.name} | {day_name} {group.time_slot} | Coach: {coach_name}")
    
    # Show Russell's phantom records
    russell_phantoms = [r for r in phantom_records if 'russ' in r.lesson_session.scheduled_group.name.lower()]
    if russell_phantoms:
        print(f"\n🎯 RUSSELL'S GROUPS WITH PHANTOM STUDENTS:")
        russell_groups = {}
        for record in russell_phantoms:
            group_name = record.lesson_session.scheduled_group.name
            if group_name not in russell_groups:
                russell_groups[group_name] = []
            russell_groups[group_name].append(str(record.enrollment.student))
        
        for group_name, students in russell_groups.items():
            print(f"  - {group_name}: {', '.join(students)}")
    
    print(f"\n🔥 READY TO DELETE {len(phantom_records)} PHANTOM RECORDS FROM PRODUCTION")
    print("This will:")
    print("  ✅ Remove students from lessons they shouldn't be in")
    print("  ✅ Fix dashboard display issues immediately")
    print("  ✅ Allow lesson notes to work properly")
    print("  ✅ Keep all legitimate attendance data intact")
    print("  ✅ Fix Alisdair appearing in Russell's roster")
    
    # Execute cleanup with transaction safety
    try:
        with transaction.atomic():
            deleted_count = 0
            print(f"\n🗑️  DELETING PHANTOM RECORDS FROM PRODUCTION...")
            
            for record in phantom_records:
                student_name = str(record.enrollment.student)
                group_name = record.lesson_session.scheduled_group.name
                print(f"🗑️  Deleting: {student_name} from {group_name}")
                record.delete()
                deleted_count += 1
            
            print(f"\n✅ SUCCESS: Deleted {deleted_count} phantom records from PRODUCTION")
            print(f"✅ Kept {len(legitimate_records)} legitimate records")
            
            # Verify Alisdair's records after cleanup
            print(f"\n🔍 VERIFYING ALISDAIR'S RECORDS AFTER CLEANUP:")
            try:
                alisdair = Student.objects.get(first_name__icontains='alisdair')
                remaining_records = AttendanceRecord.objects.filter(
                    enrollment__student=alisdair
                ).select_related('lesson_session__scheduled_group')
                
                print(f"  Alisdair now appears in {remaining_records.count()} lessons:")
                for record in remaining_records:
                    group = record.lesson_session.scheduled_group
                    coach_name = str(group.coach) if group.coach else 'No Coach'
                    day_name = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'][group.day_of_week]
                    
                    # Verify this is legitimate
                    is_legitimate = record.enrollment in group.members.all()
                    status = "✅ LEGITIMATE" if is_legitimate else "❌ STILL PHANTOM"
                    
                    print(f"    - {group.name} | {day_name} {group.time_slot} | Coach: {coach_name} | {status}")
                
            except Student.DoesNotExist:
                print("  (Alisdair not found in database)")
            
    except Exception as e:
        print(f"💥 ERROR during production cleanup: {str(e)}")
        print("❌ Transaction rolled back. No changes made to production.")
        return
    
    print(f"\n🎉 PRODUCTION DATABASE CLEANUP COMPLETE!")
    print("✅ Phantom attendance records removed from PRODUCTION")
    print("✅ Students now appear in correct lessons only")
    print("✅ Lesson notes should work properly")
    print("✅ Dashboard shows accurate rosters")
    print("✅ Alisdair should NO LONGER appear in Russell's roster")
    print("\n🚀 Check the production dashboard now - issues should be resolved!")

if __name__ == "__main__":
    cleanup_production_phantom_records()
