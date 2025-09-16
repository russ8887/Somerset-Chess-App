import csv
import io
import re
from datetime import time
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from .forms import CSVImportForm, LessonCSVImportForm
from .models import Student, SchoolClass, Enrollment, ScheduledGroup, Coach, TimeSlot, Term
from django.contrib.auth.models import User

def parse_student_name_and_class(name_string):
    """
    Parse student name and class from formats like:
    "Emmanuel Puljich (1C)-3" -> first_name="Emmanuel", last_name="Puljich", school_class="1C"
    """
    # Pattern to match: "FirstName LastName (ClassCode)-EnrollmentType"
    pattern = r'^(.+?)\s+\(([^)]+)\)-\d+$'
    match = re.match(pattern, name_string.strip())
    
    if not match:
        raise ValueError(f"Cannot parse name format: {name_string}")
    
    full_name = match.group(1).strip()
    school_class = match.group(2).strip()
    
    # Split full name into first and last name
    name_parts = full_name.split()
    if len(name_parts) < 2:
        raise ValueError(f"Name must have at least first and last name: {full_name}")
    
    first_name = name_parts[0]
    last_name = ' '.join(name_parts[1:])  # Handle multi-word last names
    
    return first_name, last_name, school_class

def extract_year_level_from_class(school_class):
    """
    Extract year level from class codes like:
    "1C" -> 1, "4G" -> 4, "Prep W" -> 0, "6J" -> 6
    """
    if school_class.lower().startswith('prep'):
        return 0
    
    # Extract the first number from the class code
    match = re.match(r'^(\d+)', school_class)
    if match:
        return int(match.group(1))
    
    raise ValueError(f"Cannot extract year level from class: {school_class}")

def parse_lesson_schedule_string(lesson_string):
    """
    Parse lesson schedule strings like:
    "Term 3 Week 3A Liam's Tuesday 11:00am Group" -> 
    {
        'term': 'Term 3',
        'week': 'Week 3A', 
        'coach_name': 'Liam',
        'day': 'Tuesday',
        'time': '11:00am'
    }
    """
    # Pattern to match: "Term X Week YZ Coach's Day HH:MMam/pm Group"
    pattern = r'Term\s+\d+\s+Week\s+\d+[AB]\s+([^\']+)\'s\s+(\w+)\s+(\d{1,2}:\d{2}(?:am|pm))\s+Group'
    match = re.search(pattern, lesson_string.strip())
    
    if not match:
        raise ValueError(f"Cannot parse lesson schedule format: {lesson_string}")
    
    coach_name = match.group(1).strip()
    day = match.group(2).strip()
    time_str = match.group(3).strip()
    
    # Extract term and week from the beginning
    term_match = re.search(r'Term\s+(\d+)', lesson_string)
    week_match = re.search(r'Week\s+(\d+[AB])', lesson_string)
    
    if not term_match or not week_match:
        raise ValueError(f"Cannot extract term/week from: {lesson_string}")
    
    return {
        'term': f"Term {term_match.group(1)}",
        'week': f"Week {week_match.group(1)}",
        'coach_name': coach_name,
        'day': day,
        'time': time_str
    }

def parse_time_string(time_str):
    """
    Convert time strings like "11:00am" or "2:20pm" to time objects
    """
    # Remove spaces and convert to lowercase
    time_str = time_str.replace(' ', '').lower()
    
    # Parse the time
    if time_str.endswith('am') or time_str.endswith('pm'):
        is_pm = time_str.endswith('pm')
        time_part = time_str[:-2]  # Remove am/pm
        
        if ':' in time_part:
            hour_str, minute_str = time_part.split(':')
            hour = int(hour_str)
            minute = int(minute_str)
        else:
            hour = int(time_part)
            minute = 0
        
        # Convert to 24-hour format
        if is_pm and hour != 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0
            
        return time(hour, minute)
    
    raise ValueError(f"Cannot parse time format: {time_str}")

def get_day_of_week_number(day_name):
    """
    Convert day names to numbers (Monday=0, Tuesday=1, etc.)
    """
    day_map = {
        'monday': 0,
        'tuesday': 1, 
        'wednesday': 2,
        'thursday': 3,
        'friday': 4,
        'saturday': 5,
        'sunday': 6
    }
    
    day_lower = day_name.lower()
    if day_lower in day_map:
        return day_map[day_lower]
    
    raise ValueError(f"Unknown day name: {day_name}")

@staff_member_required
def import_students_csv(request):
    if request.method == 'POST':
        form = CSVImportForm(request.POST, request.FILES)
        print(f"DEBUG: Form submitted. Files: {request.FILES}")
        print(f"DEBUG: Form data: {request.POST}")
        print(f"DEBUG: Form errors: {form.errors}")
        
        if form.is_valid():
            print("DEBUG: Form is valid - proceeding with import")
            csv_file = form.cleaned_data['csv_file']
            term = form.cleaned_data['term']
            
            # Process the CSV file
            try:
                csv_file.seek(0)
                content = csv_file.read().decode('utf-8-sig')
                
                # Try to detect CSV format by reading the first line
                lines = content.strip().split('\n')
                if not lines:
                    messages.error(request, 'CSV file is empty.')
                    return render(request, 'admin/csv_import.html', {
                        'form': form,
                        'title': 'Import Students from CSV',
                        'opts': Student._meta,
                    })
                
                header_line = lines[0].lower()
                
                # Detect format based on headers
                is_new_format = 'group of' in header_line and 'students_nameandclass' in header_line
                is_old_format = 'first_name' in header_line and 'last_name' in header_line
                
                enrollment_type_map = {
                    '1': 'SOLO',
                    '2': 'PAIR', 
                    '3': 'GROUP',
                }
                
                imported_count = 0
                skipped_count = 0
                errors = []
                
                if is_new_format:
                    # Handle new format: "Group of:,STUDENTS_nameandclass"
                    reader = csv.reader(io.StringIO(content))
                    next(reader)  # Skip header row
                    
                    for row_num, row in enumerate(reader, start=2):
                        if len(row) < 2:
                            errors.append(f"Row {row_num}: Insufficient columns")
                            skipped_count += 1
                            continue
                            
                        try:
                            enrollment_type_code = row[0].strip()
                            name_and_class = row[1].strip()
                            
                            if not enrollment_type_code or not name_and_class:
                                errors.append(f"Row {row_num}: Missing enrollment type or student data")
                                skipped_count += 1
                                continue
                            
                            # Parse the name and class
                            first_name, last_name, school_class_name = parse_student_name_and_class(name_and_class)
                            
                            # Extract year level from class
                            year_level = extract_year_level_from_class(school_class_name)
                            
                            # Validate enrollment type
                            enrollment_type = enrollment_type_map.get(enrollment_type_code)
                            if not enrollment_type:
                                errors.append(f"Row {row_num}: Invalid enrollment_type '{enrollment_type_code}' for {first_name} {last_name}")
                                skipped_count += 1
                                continue
                            
                            # Create or get school class
                            school_class, _ = SchoolClass.objects.get_or_create(
                                name=school_class_name
                            )
                            
                            # Create or update student
                            student, created = Student.objects.update_or_create(
                                first_name=first_name,
                                last_name=last_name,
                                defaults={
                                    'year_level': year_level,
                                    'school_class': school_class,
                                }
                            )
                            
                            # Create enrollment
                            enrollment, enrollment_created = Enrollment.objects.get_or_create(
                                student=student,
                                term=term,
                                defaults={'enrollment_type': enrollment_type}
                            )
                            
                            if enrollment_created or created:
                                imported_count += 1
                                
                        except Exception as e:
                            errors.append(f"Row {row_num}: Error processing {name_and_class}: {str(e)}")
                            skipped_count += 1
                
                elif is_old_format:
                    # Handle old format: "first_name,last_name,school_class,year_level,enrollment_type"
                    reader = csv.DictReader(io.StringIO(content))
                    
                    for row_num, row in enumerate(reader, start=2):
                        try:
                            # Clean data
                            first_name = row['first_name'].strip()
                            last_name = row['last_name'].strip()
                            school_class_name = row['school_class'].strip()
                            year_level = row['year_level'].strip()
                            enrollment_type_code = row['enrollment_type'].strip()
                            
                            # Validate required fields
                            if not first_name or not last_name:
                                errors.append(f"Row {row_num}: Missing first_name or last_name")
                                skipped_count += 1
                                continue
                            
                            # Validate year level
                            try:
                                year_level = int(year_level)
                            except ValueError:
                                errors.append(f"Row {row_num}: Invalid year_level '{year_level}' for {first_name} {last_name}")
                                skipped_count += 1
                                continue
                            
                            # Validate enrollment type
                            enrollment_type = enrollment_type_map.get(enrollment_type_code)
                            if not enrollment_type:
                                errors.append(f"Row {row_num}: Invalid enrollment_type '{enrollment_type_code}' for {first_name} {last_name}")
                                skipped_count += 1
                                continue
                            
                            # Create or get school class
                            school_class, _ = SchoolClass.objects.get_or_create(
                                name=school_class_name
                            )
                            
                            # Create or update student
                            student, created = Student.objects.update_or_create(
                                first_name=first_name,
                                last_name=last_name,
                                defaults={
                                    'year_level': year_level,
                                    'school_class': school_class,
                                }
                            )
                            
                            # Create enrollment
                            enrollment, enrollment_created = Enrollment.objects.get_or_create(
                                student=student,
                                term=term,
                                defaults={'enrollment_type': enrollment_type}
                            )
                            
                            if enrollment_created or created:
                                imported_count += 1
                            
                        except Exception as e:
                            errors.append(f"Row {row_num}: Error processing {row.get('first_name', 'Unknown')} {row.get('last_name', 'Unknown')}: {str(e)}")
                            skipped_count += 1
                
                else:
                    messages.error(request, 'Unrecognized CSV format. Please use either the standard format (first_name, last_name, school_class, year_level, enrollment_type) or the new format (Group of:, STUDENTS_nameandclass).')
                    return render(request, 'admin/csv_import.html', {
                        'form': form,
                        'title': 'Import Students from CSV',
                        'opts': Student._meta,
                    })
                
                # Show results
                if imported_count > 0:
                    messages.success(request, f'Successfully imported {imported_count} students into {term.name}.')
                
                if skipped_count > 0:
                    messages.warning(request, f'Skipped {skipped_count} rows due to errors.')
                
                if errors:
                    error_message = "Errors encountered:\n" + "\n".join(errors[:10])  # Show first 10 errors
                    if len(errors) > 10:
                        error_message += f"\n... and {len(errors) - 10} more errors."
                    messages.error(request, error_message)
                
                if imported_count > 0:
                    return redirect('/admin/scheduler/student/')
                    
            except Exception as e:
                messages.error(request, f'Error processing CSV file: {str(e)}')
        else:
            # Form is NOT valid - this is likely why it was failing silently
            print("DEBUG: Form is NOT valid!")
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
            print(f"DEBUG: Rendering form with errors: {form.errors}")
    else:
        print("DEBUG: GET request - showing empty form")
        form = CSVImportForm()
    
    return render(request, 'admin/csv_import.html', {
        'form': form,
        'title': 'Import Students from CSV',
        'opts': Student._meta,
    })

@staff_member_required
def import_lessons_csv(request):
    if request.method == 'POST':
        form = LessonCSVImportForm(request.POST, request.FILES)
        
        if form.is_valid():
            csv_file = form.cleaned_data['csv_file']
            term = form.cleaned_data['term']
            
            try:
                csv_file.seek(0)
                content = csv_file.read().decode('utf-8-sig')
                
                reader = csv.DictReader(io.StringIO(content))
                
                enrollment_type_map = {
                    '1': 'SOLO',
                    '2': 'PAIR', 
                    '3': 'GROUP',
                }
                
                imported_groups = 0
                imported_enrollments = 0
                skipped_count = 0
                errors = []
                
                # Track unique groups to avoid duplicates
                processed_groups = {}
                
                for row_num, row in enumerate(reader, start=2):
                    try:
                        enrollment_type_code = row.get('Group of:', '').strip()
                        name_and_class = row.get('STUDENTS_nameandclass', '').strip()
                        coach_name = row.get('Regular Coach', '').strip()
                        lesson_data = row.get('2025 Regular Lessons', '').strip()
                        
                        if not all([enrollment_type_code, name_and_class, coach_name, lesson_data]):
                            errors.append(f"Row {row_num}: Missing required data")
                            skipped_count += 1
                            continue
                        
                        # Parse student information
                        try:
                            first_name, last_name, school_class_name = parse_student_name_and_class(name_and_class)
                            year_level = extract_year_level_from_class(school_class_name)
                        except Exception as e:
                            errors.append(f"Row {row_num}: Error parsing student data: {str(e)}")
                            skipped_count += 1
                            continue
                        
                        # Validate enrollment type
                        enrollment_type = enrollment_type_map.get(enrollment_type_code)
                        if not enrollment_type:
                            errors.append(f"Row {row_num}: Invalid enrollment_type '{enrollment_type_code}'")
                            skipped_count += 1
                            continue
                        
                        # Create or get student and enrollment
                        school_class, _ = SchoolClass.objects.get_or_create(name=school_class_name)
                        student, _ = Student.objects.update_or_create(
                            first_name=first_name,
                            last_name=last_name,
                            defaults={
                                'year_level': year_level,
                                'school_class': school_class,
                            }
                        )
                        enrollment, enrollment_created = Enrollment.objects.get_or_create(
                            student=student,
                            term=term,
                            defaults={'enrollment_type': enrollment_type}
                        )
                        if enrollment_created:
                            imported_enrollments += 1
                        
                        # Parse lesson schedule data
                        lesson_strings = [s.strip() for s in lesson_data.split(',') if s.strip()]
                        
                        for lesson_string in lesson_strings:
                            try:
                                # Parse the lesson schedule
                                schedule_info = parse_lesson_schedule_string(lesson_string)
                                
                                # Create a unique group identifier
                                group_key = f"{coach_name}_{schedule_info['day']}_{schedule_info['time']}"
                                
                                if group_key not in processed_groups:
                                    # Find or create coach
                                    coach = None
                                    try:
                                        # Try to find coach by first name
                                        coach_user = User.objects.filter(first_name__iexact=coach_name).first()
                                        if coach_user:
                                            coach, _ = Coach.objects.get_or_create(user=coach_user)
                                        else:
                                            # Create a coach entry without a user (can be linked later)
                                            coach, _ = Coach.objects.get_or_create(
                                                user=None,
                                                defaults={'is_head_coach': False}
                                            )
                                    except Exception as e:
                                        errors.append(f"Row {row_num}: Error finding/creating coach '{coach_name}': {str(e)}")
                                        continue
                                    
                                    # Parse time and create time slot
                                    try:
                                        start_time = parse_time_string(schedule_info['time'])
                                        # Assume 30-minute lessons
                                        end_hour = start_time.hour
                                        end_minute = start_time.minute + 30
                                        if end_minute >= 60:
                                            end_hour += 1
                                            end_minute -= 60
                                        end_time = time(end_hour, end_minute)
                                        
                                        time_slot, _ = TimeSlot.objects.get_or_create(
                                            start_time=start_time,
                                            end_time=end_time
                                        )
                                    except Exception as e:
                                        errors.append(f"Row {row_num}: Error parsing time '{schedule_info['time']}': {str(e)}")
                                        continue
                                    
                                    # Create group name
                                    group_name = f"{coach_name}'s {schedule_info['day']} {schedule_info['time']} Group"
                                    
                                    # Create scheduled group
                                    try:
                                        day_number = get_day_of_week_number(schedule_info['day'])
                                        
                                        scheduled_group, group_created = ScheduledGroup.objects.get_or_create(
                                            name=group_name,
                                            term=term,
                                            day_of_week=day_number,
                                            time_slot=time_slot,
                                            defaults={'coach': coach}
                                        )
                                        
                                        if group_created:
                                            imported_groups += 1
                                        
                                        processed_groups[group_key] = scheduled_group
                                        
                                    except Exception as e:
                                        errors.append(f"Row {row_num}: Error creating scheduled group: {str(e)}")
                                        continue
                                
                                # Add enrollment to the group
                                scheduled_group = processed_groups[group_key]
                                scheduled_group.members.add(enrollment)
                                
                            except Exception as e:
                                errors.append(f"Row {row_num}: Error processing lesson '{lesson_string}': {str(e)}")
                                continue
                        
                    except Exception as e:
                        errors.append(f"Row {row_num}: Unexpected error: {str(e)}")
                        skipped_count += 1
                        continue
                
                # Show results
                if imported_groups > 0 or imported_enrollments > 0:
                    messages.success(request, f'Successfully imported {imported_groups} scheduled groups and {imported_enrollments} enrollments into {term.name}.')
                
                if skipped_count > 0:
                    messages.warning(request, f'Skipped {skipped_count} rows due to errors.')
                
                if errors:
                    error_message = "Errors encountered:\n" + "\n".join(errors[:10])  # Show first 10 errors
                    if len(errors) > 10:
                        error_message += f"\n... and {len(errors) - 10} more errors."
                    messages.error(request, error_message)
                
                if imported_groups > 0:
                    return redirect('/admin/scheduler/scheduledgroup/')
                    
            except Exception as e:
                messages.error(request, f'Error processing CSV file: {str(e)}')
        else:
            for field, field_errors in form.errors.items():
                for error in field_errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = LessonCSVImportForm()
    
    return render(request, 'admin/csv_import.html', {
        'form': form,
        'title': 'Import Lessons from CSV',
        'opts': ScheduledGroup._meta,
    })

@staff_member_required
def download_csv_template(request):
    """Download a CSV template file"""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="student_import_template.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['first_name', 'last_name', 'school_class', 'year_level', 'enrollment_type'])
    writer.writerow(['John', 'Smith', '4G', '4', '1'])
    writer.writerow(['Jane', 'Doe', '5P', '5', '2'])
    writer.writerow(['Bob', 'Johnson', '6A', '6', '3'])
    
    return response
