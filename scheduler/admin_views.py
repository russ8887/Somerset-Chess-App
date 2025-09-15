import csv
import io
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from .forms import CSVImportForm
from .models import Student, SchoolClass, Enrollment

@staff_member_required
def import_students_csv(request):
    if request.method == 'POST':
        form = CSVImportForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = form.cleaned_data['csv_file']
            term = form.cleaned_data['term']
            
            # Process the CSV file
            try:
                csv_file.seek(0)
                content = csv_file.read().decode('utf-8-sig')
                reader = csv.DictReader(io.StringIO(content))
                
                enrollment_type_map = {
                    '1': 'SOLO',
                    '2': 'PAIR', 
                    '3': 'GROUP',
                }
                
                imported_count = 0
                skipped_count = 0
                errors = []
                
                for row_num, row in enumerate(reader, start=2):  # Start at 2 because row 1 is headers
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
        form = CSVImportForm()
    
    return render(request, 'admin/csv_import.html', {
        'form': form,
        'title': 'Import Students from CSV',
        'opts': Student._meta,
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
