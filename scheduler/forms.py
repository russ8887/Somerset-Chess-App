# scheduler/forms.py

from django import forms
from .models import LessonNote

class LessonNoteForm(forms.ModelForm):
    class Meta:
        model = LessonNote
        fields = ['topics_covered', 'coach_comments']
        widgets = {
            'topics_covered': forms.Textarea(attrs={'rows': 2, 'class': 'form-control', 'placeholder': 'What was covered?'}),
            'coach_comments': forms.Textarea(attrs={'rows': 2, 'class': 'form-control', 'placeholder': 'Notes for next time...'}),
        }