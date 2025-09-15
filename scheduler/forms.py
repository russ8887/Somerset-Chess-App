from django import forms
from django.core.exceptions import ValidationError
from .models import Term, LessonNote
import csv
import io

class CSVImportForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV File",
        help_text="Upload a CSV file with columns: first_name, last_name, school_class, year_level, enrollment_type (1=Solo, 2=Pair, 3=Group)"
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.all(),
        label="Term",
        help_text="Select the term to enroll students in"
    )
    
    def clean_csv_file(self):
        csv_file = self.cleaned_data['csv_file']
        
        if not csv_file.name.endswith('.csv'):
            raise ValidationError('File must be a CSV file.')
        
        # Read and validate CSV structure
        try:
            csv_file.seek(0)
            content = csv_file.read().decode('utf-8-sig')
            csv_file.seek(0)  # Reset file pointer
            
            # Check if file has required columns
            reader = csv.DictReader(io.StringIO(content))
            
            if not reader.fieldnames:
                raise ValidationError('CSV file appears to be empty or invalid.')
            
            # Check for either format
            old_format_columns = ['first_name', 'last_name', 'school_class', 'year_level', 'enrollment_type']
            new_format_columns = ['Group of:', 'STUDENTS_nameandclass']
            
            # Convert fieldnames to lowercase for case-insensitive comparison
            fieldnames_lower = [field.lower() for field in reader.fieldnames]
            
            # Check if it's the old format
            old_format_missing = [col for col in old_format_columns if col.lower() not in fieldnames_lower]
            is_old_format = len(old_format_missing) == 0
            
            # Check if it's the new format
            new_format_missing = [col for col in new_format_columns if col.lower() not in fieldnames_lower]
            is_new_format = len(new_format_missing) == 0
            
            if not is_old_format and not is_new_format:
                raise ValidationError(
                    f'CSV file format not recognized. Please use either:\n'
                    f'Format 1: {", ".join(old_format_columns)}\n'
                    f'Format 2: {", ".join(new_format_columns)}'
                )
                
        except UnicodeDecodeError:
            raise ValidationError('File encoding error. Please save your CSV file with UTF-8 encoding.')
        except Exception as e:
            raise ValidationError(f'Error reading CSV file: {str(e)}')
        
        return csv_file

class LessonNoteForm(forms.ModelForm):
    class Meta:
        model = LessonNote
        fields = ['student_understanding', 'topics_covered', 'coach_comments']
        widgets = {
            'topics_covered': forms.Textarea(attrs={'rows': 3}),
            'coach_comments': forms.Textarea(attrs={'rows': 3}),
        }
