from django import forms
from django.forms import widgets
from datetime import date, timedelta
from .models import OneOffEvent, Student, SchoolClass, TimeSlot, Coach

class BaseEventForm(forms.ModelForm):
    """Base form for all event types with common functionality"""
    
    class Meta:
        model = OneOffEvent
        fields = ['name', 'event_date', 'reason']
        widgets = {
            'event_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control',
                'min': date.today().isoformat()
            }),
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Event name will be auto-generated'
            }),
            'reason': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Reason for absence'
            })
        }

class PublicHolidayForm(BaseEventForm):
    """Form for creating public holiday events"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs['placeholder'] = 'e.g., Christmas Day, Australia Day'
        self.fields['reason'].initial = 'Public Holiday'
        self.fields['reason'].widget.attrs['readonly'] = True
        
    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.event_type = OneOffEvent.EventType.PUBLIC_HOLIDAY
        instance.reason = 'Public Holiday'
        
        if commit:
            instance.save()
            # Add all school classes to affect everyone
            instance.school_classes.set(SchoolClass.objects.all())
            
        return instance

class PupilFreeDayForm(BaseEventForm):
    """Form for creating pupil free day events"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs['placeholder'] = 'e.g., Staff Development Day'
        self.fields['reason'].initial = 'Pupil Free Day'
        self.fields['reason'].widget.attrs['readonly'] = True
        
    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.event_type = OneOffEvent.EventType.PUPIL_FREE_DAY
        instance.reason = 'Pupil Free Day'
        
        if commit:
            instance.save()
            # Add all school classes to affect everyone
            instance.school_classes.set(SchoolClass.objects.all())
            
        return instance

class CampEventForm(forms.ModelForm):
    """Form for creating multi-day camp events"""
    
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'min': date.today().isoformat()
        }),
        label='Start Date'
    )
    
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'min': date.today().isoformat()
        }),
        label='End Date'
    )
    
    year_levels = forms.MultipleChoiceField(
        choices=[
            ('P', 'Prep'),
            ('1', 'Year 1'),
            ('2', 'Year 2'),
            ('3', 'Year 3'),
            ('4', 'Year 4'),
            ('5', 'Year 5'),
            ('6', 'Year 6'),
        ],
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        label='Year Levels Affected'
    )
    
    camp_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., Year 4 Camp, Leadership Camp'
        }),
        label='Camp Name'
    )
    
    class Meta:
        model = OneOffEvent
        fields = ['camp_name', 'start_date', 'end_date', 'year_levels']
    
    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        
        if start_date and end_date:
            if end_date < start_date:
                raise forms.ValidationError("End date must be after start date.")
            
            if (end_date - start_date).days > 14:
                raise forms.ValidationError("Camp duration cannot exceed 14 days.")
        
        return cleaned_data
    
    def save(self, commit=True):
        """Create multiple events for multi-day camp"""
        start_date = self.cleaned_data['start_date']
        end_date = self.cleaned_data['end_date']
        camp_name = self.cleaned_data['camp_name']
        year_levels = self.cleaned_data['year_levels']
        
        # Get affected school classes based on year levels
        affected_classes = SchoolClass.objects.filter(
            name__regex=r'^[' + ''.join(year_levels) + r'][A-Z]$'
        )
        
        # Create events using the model's class method
        events = OneOffEvent.create_multi_day_event(
            name=camp_name,
            event_type=OneOffEvent.EventType.CAMP,
            start_date=start_date,
            end_date=end_date,
            reason='School Camp',
            school_classes=affected_classes
        )
        
        return events

class ExcursionEventForm(forms.ModelForm):
    """Form for creating class excursion events"""
    
    event_date = forms.DateField(
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'min': date.today().isoformat()
        })
    )
    
    school_classes = forms.ModelMultipleChoiceField(
        queryset=SchoolClass.objects.all().order_by('name'),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        label='Classes Going on Excursion'
    )
    
    time_slots = forms.ModelMultipleChoiceField(
        queryset=TimeSlot.objects.all().order_by('start_time'),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        required=False,
        label='Time Slots Affected (leave blank for all-day excursion)',
        help_text='Select specific time slots, or leave blank for an all-day excursion'
    )
    
    excursion_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., Zoo Excursion, Museum Visit'
        }),
        label='Excursion Name'
    )
    
    class Meta:
        model = OneOffEvent
        fields = ['excursion_name', 'event_date', 'school_classes', 'time_slots']
    
    def save(self, commit=True):
        instance = OneOffEvent(
            name=self.cleaned_data['excursion_name'],
            event_type=OneOffEvent.EventType.EXCURSION,
            event_date=self.cleaned_data['event_date'],
            reason='Class Excursion'
        )
        
        if commit:
            instance.save()
            instance.school_classes.set(self.cleaned_data['school_classes'])
            if self.cleaned_data['time_slots']:
                instance.time_slots.set(self.cleaned_data['time_slots'])
        
        return instance

class IndividualStudentEventForm(forms.ModelForm):
    """Form for creating events affecting individual students"""
    
    DURATION_CHOICES = [
        ('full_day', 'Full Day'),
        ('specific_times', 'Specific Time Slots'),
    ]
    
    event_date = forms.DateField(
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'min': date.today().isoformat()
        })
    )
    
    duration_type = forms.ChoiceField(
        choices=DURATION_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        initial='full_day',
        label='Duration'
    )
    
    time_slots = forms.ModelMultipleChoiceField(
        queryset=TimeSlot.objects.all().order_by('start_time'),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        required=False,
        label='Time Slots (only if "Specific Time Slots" selected above)'
    )
    
    # Student selection fields
    selection_method = forms.ChoiceField(
        choices=[
            ('search', 'Search by Name'),
            ('browse', 'Browse by Year Level'),
        ],
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        initial='search',
        label='Student Selection Method'
    )
    
    student_search = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Type student name to search...',
            'id': 'student-search-input'
        }),
        label='Search Students'
    )
    
    year_level_filter = forms.ChoiceField(
        choices=[('', 'All Years')] + [('P', 'Prep')] + [(str(i), f'Year {i}') for i in range(1, 7)],
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='Filter by Year Level'
    )
    
    students = forms.ModelMultipleChoiceField(
        queryset=Student.objects.all().order_by('year_level', 'last_name', 'first_name'),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        label='Select Students'
    )
    
    event_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., Medical Appointment, Family Holiday'
        }),
        label='Event Name'
    )
    
    reason = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Reason for absence'
        })
    )
    
    class Meta:
        model = OneOffEvent
        fields = ['event_name', 'event_date', 'duration_type', 'time_slots', 
                 'selection_method', 'student_search', 'year_level_filter', 'students', 'reason']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make the students field dynamic based on filters
        if 'year_level_filter' in self.data and self.data['year_level_filter']:
            year_level_filter = self.data['year_level_filter']
            if year_level_filter == 'P':
                # Handle Prep level
                self.fields['students'].queryset = Student.objects.filter(
                    year_level='P'
                ).order_by('last_name', 'first_name')
            else:
                # Handle numeric year levels
                year_level = int(year_level_filter)
                self.fields['students'].queryset = Student.objects.filter(
                    year_level=year_level
                ).order_by('last_name', 'first_name')
    
    def clean(self):
        cleaned_data = super().clean()
        duration_type = cleaned_data.get('duration_type')
        time_slots = cleaned_data.get('time_slots')
        
        if duration_type == 'specific_times' and not time_slots:
            raise forms.ValidationError(
                "Please select time slots when choosing 'Specific Time Slots' duration."
            )
        
        return cleaned_data
    
    def save(self, commit=True):
        instance = OneOffEvent(
            name=self.cleaned_data['event_name'],
            event_type=OneOffEvent.EventType.INDIVIDUAL,
            event_date=self.cleaned_data['event_date'],
            reason=self.cleaned_data['reason']
        )
        
        if commit:
            instance.save()
            instance.students.set(self.cleaned_data['students'])
            
            # Only set time slots if specific times selected
            if self.cleaned_data['duration_type'] == 'specific_times':
                instance.time_slots.set(self.cleaned_data['time_slots'])
        
        return instance

class CustomEventForm(forms.ModelForm):
    """Form for creating fully custom events"""
    
    class Meta:
        model = OneOffEvent
        fields = ['name', 'event_type', 'event_date', 'end_date', 'time_slots', 
                 'students', 'school_classes', 'year_levels', 'reason']
        widgets = {
            'event_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control',
                'min': date.today().isoformat()
            }),
            'end_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control',
                'min': date.today().isoformat()
            }),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'reason': forms.TextInput(attrs={'class': 'form-control'}),
            'year_levels': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., 3,4,5 (comma-separated)'
            }),
            'event_type': forms.Select(attrs={'class': 'form-control'}),
            'time_slots': forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
            'students': forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
            'school_classes': forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        }
    
    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('event_date')
        end_date = cleaned_data.get('end_date')
        
        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError("End date must be after start date.")
        
        return cleaned_data
