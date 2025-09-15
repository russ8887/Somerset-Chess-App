from django import forms
from django.core.exceptions import ValidationError
from .models import Term
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
            required_columns = ['first_name', 'last_name', 'school_class', 'year_level', 'enrollment_type']
            
            if not reader.fieldnames:
                raise ValidationError('CSV file appears to be empty or invalid.')
            
            missing_columns = [col for col in required_columns if col not in reader.fieldnames]
            if missing_columns:
                raise ValidationError(f'CSV file is missing required columns: {", ".join(missing_columns)}')
                
        except UnicodeDecodeError:
            raise ValidationError('File encoding error. Please save your CSV file with UTF-8 encoding.')
        except Exception as e:
            raise ValidationError(f'Error reading CSV file: {str(e)}')
        
        return csv_file

class LessonNoteForm(forms.ModelForm):
    from .models import LessonNote
    
    class Meta:
        model = LessonNote
        fields = ['student_understanding', 'topics_covered', 'coach_comments']
        widgets = {
            'topics_covered': forms.Textarea(attrs={'rows': 3}),
            'coach_comments': forms.Textarea(attrs={'rows': 3}),
        }
