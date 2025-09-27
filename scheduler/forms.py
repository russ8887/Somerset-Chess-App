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

class LessonCSVImportForm(forms.Form):
    csv_file = forms.FileField(
        label="Lesson CSV File",
        help_text="Upload a CSV file with lesson schedule data. All lessons will be imported to the currently active term."
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Get the active term to show in the form
        active_term = Term.get_active_term()
        if active_term:
            self.fields['csv_file'].help_text = f"Upload a CSV file with lesson schedule data. All lessons will be imported to: {active_term.name}"
        else:
            self.fields['csv_file'].help_text = "Upload a CSV file with lesson schedule data. WARNING: No active term is set - please set an active term in the Terms admin first."
    
    def clean(self):
        cleaned_data = super().clean()
        
        # Check that there's an active term
        active_term = Term.get_active_term()
        if not active_term:
            raise ValidationError('No active term is set. Please go to the Terms admin and set one term as active before importing lessons.')
        
        return cleaned_data
    
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
            
            # Try to parse as CSV to get actual fieldnames
            reader = csv.DictReader(io.StringIO(content))
            fieldnames = reader.fieldnames or []
            
            # Check for required columns for lesson import (updated for GROUP_link format)
            required_columns = ['Group of:', 'STUDENTS_nameandclass', 'Regular Coach', 'GROUP_link']
            missing_columns = [col for col in required_columns if col not in fieldnames]
            
            if missing_columns:
                raise ValidationError(
                    f'CSV file is missing required columns: {", ".join(missing_columns)}\n'
                    f'Found columns: {", ".join(fieldnames)}\n'
                    f'Expected columns: Group of:, STUDENTS_nameandclass, Regular Coach, GROUP_link'
                )
                
        except UnicodeDecodeError:
            raise ValidationError('File encoding error. Please save your CSV file with UTF-8 encoding.')
        except ValidationError:
            raise  # Re-raise validation errors
        except Exception as e:
            raise ValidationError(f'Error reading CSV file: {str(e)}')
        
        return csv_file

class LessonNoteForm(forms.ModelForm):
    # Predefined topic choices
    TOPIC_CHOICES = [
        ('opening_principles', 'Opening Principles'),
        ('tactical_patterns', 'Tactical Patterns (Pins, Forks, Skewers)'),
        ('endgame_basics', 'Endgame Basics'),
        ('piece_development', 'Piece Development'),
        ('castling_safety', 'Castling & King Safety'),
        ('pawn_structure', 'Pawn Structure'),
        ('time_management', 'Time Management'),
        ('tournament_prep', 'Tournament Preparation'),
        ('problem_solving', 'Problem Solving'),
        ('game_analysis', 'Game Analysis'),
        ('notation', 'Chess Notation'),
        ('basic_rules', 'Basic Rules & Movement'),
        ('checkmate_patterns', 'Checkmate Patterns'),
        ('piece_values', 'Piece Values & Trading'),
        ('center_control', 'Center Control'),
    ]
    
    # Student understanding rating choices
    UNDERSTANDING_CHOICES = [
        ('1', '⭐ Struggling - Needs significant help'),
        ('2', '⭐⭐ Developing - Some understanding'),
        ('3', '⭐⭐⭐ Good - Grasps most concepts'),
        ('4', '⭐⭐⭐⭐ Very Good - Strong understanding'),
        ('5', '⭐⭐⭐⭐⭐ Excellent - Mastered concepts'),
    ]
    
    # Enhanced fields with clean dropdown approach
    topics_covered_choices = forms.MultipleChoiceField(
        choices=TOPIC_CHOICES,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-select',
            'style': 'height: 120px;',
            'data-placeholder': 'Select topics covered...'
        }),
        required=False,
        label="Topics Covered"
    )
    
    topics_covered_custom = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 2, 
            'class': 'form-control',
            'placeholder': 'Additional topics or details...'
        }),
        required=False,
        label="Additional Topics"
    )
    
    student_understanding_rating = forms.ChoiceField(
        choices=[('', 'Select understanding level...')] + UNDERSTANDING_CHOICES,
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        required=False,
        label="Student Understanding Level"
    )
    
    student_understanding_notes = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 2, 
            'class': 'form-control',
            'placeholder': 'Additional notes about student understanding...'
        }),
        required=False,
        label="Understanding Notes"
    )
    
    class Meta:
        model = LessonNote
        fields = ['student_understanding', 'topics_covered', 'coach_comments']
        widgets = {
            'topics_covered': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Legacy field - use checkboxes above for new notes'}),
            'coach_comments': forms.Textarea(attrs={'rows': 3, 'placeholder': 'General comments about the lesson...'}),
            'student_understanding': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Legacy field - use rating above for new notes'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # If editing an existing note, try to parse the data
        if self.instance and self.instance.pk:
            # Try to extract topics from existing data
            existing_topics = self.instance.topics_covered or ''
            existing_understanding = self.instance.student_understanding or ''
            
            # Set initial values for new fields based on existing data
            if existing_topics:
                # Try to match existing topics to predefined choices
                matched_topics = []
                for choice_value, choice_label in self.TOPIC_CHOICES:
                    if choice_label.lower() in existing_topics.lower():
                        matched_topics.append(choice_value)
                
                if matched_topics:
                    self.fields['topics_covered_choices'].initial = matched_topics
                else:
                    self.fields['topics_covered_custom'].initial = existing_topics
            
            # Try to extract rating from existing understanding
            if existing_understanding:
                # Look for star ratings or numbers
                if '⭐⭐⭐⭐⭐' in existing_understanding or '5' in existing_understanding:
                    self.fields['student_understanding_rating'].initial = '5'
                elif '⭐⭐⭐⭐' in existing_understanding or '4' in existing_understanding:
                    self.fields['student_understanding_rating'].initial = '4'
                elif '⭐⭐⭐' in existing_understanding or '3' in existing_understanding:
                    self.fields['student_understanding_rating'].initial = '3'
                elif '⭐⭐' in existing_understanding or '2' in existing_understanding:
                    self.fields['student_understanding_rating'].initial = '2'
                elif '⭐' in existing_understanding or '1' in existing_understanding:
                    self.fields['student_understanding_rating'].initial = '1'
                
                self.fields['student_understanding_notes'].initial = existing_understanding
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Combine the new structured fields with the legacy fields
        topics_list = []
        
        # Add selected predefined topics
        selected_topics = self.cleaned_data.get('topics_covered_choices', [])
        for topic_value in selected_topics:
            topic_label = dict(self.TOPIC_CHOICES).get(topic_value, topic_value)
            topics_list.append(topic_label)
        
        # Add custom topics
        custom_topics = self.cleaned_data.get('topics_covered_custom', '').strip()
        if custom_topics:
            topics_list.append(custom_topics)
        
        # Combine with existing topics_covered field if it has content
        existing_topics = self.cleaned_data.get('topics_covered', '').strip()
        if existing_topics and existing_topics not in str(topics_list):
            topics_list.append(existing_topics)
        
        # Update the topics_covered field
        instance.topics_covered = '; '.join(topics_list) if topics_list else ''
        
        # Combine understanding rating and notes
        understanding_parts = []
        
        # Add rating
        rating = self.cleaned_data.get('student_understanding_rating')
        if rating:
            rating_label = dict(self.UNDERSTANDING_CHOICES).get(rating, rating)
            understanding_parts.append(rating_label)
        
        # Add understanding notes
        understanding_notes = self.cleaned_data.get('student_understanding_notes', '').strip()
        if understanding_notes:
            understanding_parts.append(understanding_notes)
        
        # Combine with existing student_understanding field if it has content
        existing_understanding = self.cleaned_data.get('student_understanding', '').strip()
        if existing_understanding and existing_understanding not in str(understanding_parts):
            understanding_parts.append(existing_understanding)
        
        # Update the student_understanding field
        instance.student_understanding = '; '.join(understanding_parts) if understanding_parts else ''
        
        if commit:
            instance.save()
        return instance
