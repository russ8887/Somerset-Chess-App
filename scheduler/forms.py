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
            
            # Check if file has content
            if not content.strip():
                raise ValidationError('CSV file appears to be empty.')
            
            # Try to detect format by reading the first line
            lines = content.strip().split('\n')
            if not lines:
                raise ValidationError('CSV file appears to be empty.')
            
            header_line = lines[0].lower()
            print(f"DEBUG: CSV header line: {header_line}")
            
            # Detect format based on headers - be more flexible
            is_new_format = 'group of' in header_line and ('students_nameandclass' in header_line or 'nameandclass' in header_line)
            is_old_format = 'first_name' in header_line and 'last_name' in header_line
            
            print(f"DEBUG: is_new_format: {is_new_format}, is_old_format: {is_old_format}")
            
            if not is_old_format and not is_new_format:
                # Try to parse as CSV to get actual fieldnames
                try:
                    reader = csv.DictReader(io.StringIO(content))
                    fieldnames = reader.fieldnames or []
                    print(f"DEBUG: Actual fieldnames: {fieldnames}")
                    
                    raise ValidationError(
                        f'CSV file format not recognized. Found columns: {", ".join(fieldnames)}\n'
                        f'Expected Format 1: first_name, last_name, school_class, year_level, enrollment_type\n'
                        f'Expected Format 2: Group of:, STUDENTS_nameandclass'
                    )
                except:
                    raise ValidationError('CSV file format not recognized and cannot parse headers.')
                
        except UnicodeDecodeError:
            raise ValidationError('File encoding error. Please save your CSV file with UTF-8 encoding.')
        except ValidationError:
            raise  # Re-raise validation errors
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
