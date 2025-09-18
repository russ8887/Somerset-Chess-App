from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q
from datetime import date, timedelta
import json

from .models import OneOffEvent, Student, SchoolClass, TimeSlot, Coach
from .event_forms import (
    PublicHolidayForm, PupilFreeDayForm, CampEventForm, 
    ExcursionEventForm, IndividualStudentEventForm, CustomEventForm
)

@login_required
def event_management_dashboard(request):
    """Main dashboard for event management"""
    
    # Get upcoming events
    upcoming_events = OneOffEvent.objects.filter(
        event_date__gte=date.today()
    ).order_by('event_date')[:10]
    
    # Get recent events
    recent_events = OneOffEvent.objects.filter(
        event_date__lt=date.today()
    ).order_by('-event_date')[:5]
    
    # Get statistics
    stats = {
        'total_events': OneOffEvent.objects.count(),
        'upcoming_events': upcoming_events.count(),
        'events_this_week': OneOffEvent.objects.filter(
            event_date__range=[
                date.today(),
                date.today() + timedelta(days=7)
            ]
        ).count(),
        'processed_events': OneOffEvent.objects.filter(is_processed=True).count(),
    }
    
    context = {
        'upcoming_events': upcoming_events,
        'recent_events': recent_events,
        'stats': stats,
        'today': date.today(),
    }
    
    return render(request, 'scheduler/event_management_dashboard.html', context)

@login_required
def create_public_holiday(request):
    """Create a public holiday event"""
    
    if request.method == 'POST':
        form = PublicHolidayForm(request.POST)
        if form.is_valid():
            event = form.save()
            
            # Set created_by if coach exists
            try:
                coach = Coach.objects.get(user=request.user)
                event.created_by = coach
                event.save()
            except Coach.DoesNotExist:
                pass
            
            affected_count = event.get_affected_students_count()
            messages.success(
                request, 
                f'Public Holiday "{event.name}" created successfully! '
                f'Will affect approximately {affected_count} students.'
            )
            return redirect('event_management_dashboard')
    else:
        form = PublicHolidayForm()
    
    context = {
        'form': form,
        'event_type': 'Public Holiday',
        'description': 'Create a public holiday that will mark all students absent.',
        'preview_info': 'All students will be marked absent with reason "Public Holiday"'
    }
    
    return render(request, 'scheduler/create_event.html', context)

@login_required
def create_pupil_free_day(request):
    """Create a pupil free day event"""
    
    if request.method == 'POST':
        form = PupilFreeDayForm(request.POST)
        if form.is_valid():
            event = form.save()
            
            # Set created_by if coach exists
            try:
                coach = Coach.objects.get(user=request.user)
                event.created_by = coach
                event.save()
            except Coach.DoesNotExist:
                pass
            
            affected_count = event.get_affected_students_count()
            messages.success(
                request, 
                f'Pupil Free Day "{event.name}" created successfully! '
                f'Will affect approximately {affected_count} students.'
            )
            return redirect('event_management_dashboard')
    else:
        form = PupilFreeDayForm()
    
    context = {
        'form': form,
        'event_type': 'Pupil Free Day',
        'description': 'Create a pupil free day that will mark all students absent.',
        'preview_info': 'All students will be marked absent with reason "Pupil Free Day"'
    }
    
    return render(request, 'scheduler/create_event.html', context)

@login_required
def create_camp_event(request):
    """Create a multi-day camp event"""
    
    if request.method == 'POST':
        form = CampEventForm(request.POST)
        if form.is_valid():
            events = form.save()
            
            # Set created_by for all events if coach exists
            try:
                coach = Coach.objects.get(user=request.user)
                for event in events:
                    event.created_by = coach
                    event.save()
            except Coach.DoesNotExist:
                pass
            
            total_affected = sum(event.get_affected_students_count() for event in events)
            messages.success(
                request, 
                f'Camp events created successfully! {len(events)} events created '
                f'affecting approximately {total_affected} student-days.'
            )
            return redirect('event_management_dashboard')
    else:
        form = CampEventForm()
    
    context = {
        'form': form,
        'event_type': 'Camp Event',
        'description': 'Create a multi-day camp event for specific year levels.',
        'preview_info': 'Separate events will be created for each day of the camp'
    }
    
    return render(request, 'scheduler/create_camp_event.html', context)

@login_required
def create_excursion_event(request):
    """Create a class excursion event"""
    
    if request.method == 'POST':
        form = ExcursionEventForm(request.POST)
        if form.is_valid():
            event = form.save()
            
            # Set created_by if coach exists
            try:
                coach = Coach.objects.get(user=request.user)
                event.created_by = coach
                event.save()
            except Coach.DoesNotExist:
                pass
            
            affected_count = event.get_affected_students_count()
            time_info = "all day" if not event.time_slots.exists() else f"{event.time_slots.count()} time slots"
            
            messages.success(
                request, 
                f'Excursion "{event.name}" created successfully! '
                f'Will affect approximately {affected_count} students for {time_info}.'
            )
            return redirect('event_management_dashboard')
    else:
        form = ExcursionEventForm()
    
    context = {
        'form': form,
        'event_type': 'Class Excursion',
        'description': 'Create an excursion event for specific classes.',
        'preview_info': 'Selected classes will be marked absent for the specified time period'
    }
    
    return render(request, 'scheduler/create_excursion_event.html', context)

@login_required
def create_individual_event(request):
    """Create an event for individual students"""
    
    if request.method == 'POST':
        form = IndividualStudentEventForm(request.POST)
        if form.is_valid():
            event = form.save()
            
            # Set created_by if coach exists
            try:
                coach = Coach.objects.get(user=request.user)
                event.created_by = coach
                event.save()
            except Coach.DoesNotExist:
                pass
            
            affected_count = event.get_affected_students_count()
            time_info = "all day" if not event.time_slots.exists() else f"{event.time_slots.count()} time slots"
            
            messages.success(
                request, 
                f'Individual event "{event.name}" created successfully! '
                f'Will affect {affected_count} students for {time_info}.'
            )
            return redirect('event_management_dashboard')
    else:
        form = IndividualStudentEventForm()
    
    context = {
        'form': form,
        'event_type': 'Individual Students',
        'description': 'Create an event affecting specific individual students.',
        'preview_info': 'Only selected students will be marked absent'
    }
    
    return render(request, 'scheduler/create_individual_event.html', context)

@login_required
def create_custom_event(request):
    """Create a fully custom event"""
    
    if request.method == 'POST':
        form = CustomEventForm(request.POST)
        if form.is_valid():
            event = form.save()
            
            # Set created_by if coach exists
            try:
                coach = Coach.objects.get(user=request.user)
                event.created_by = coach
                event.save()
            except Coach.DoesNotExist:
                pass
            
            affected_count = event.get_affected_students_count()
            messages.success(
                request, 
                f'Custom event "{event.name}" created successfully! '
                f'Will affect approximately {affected_count} students.'
            )
            return redirect('event_management_dashboard')
    else:
        form = CustomEventForm()
    
    context = {
        'form': form,
        'event_type': 'Custom Event',
        'description': 'Create a fully customizable event with all options.',
        'preview_info': 'Configure all aspects of the event manually'
    }
    
    return render(request, 'scheduler/create_custom_event.html', context)

@login_required
def event_preview(request):
    """Preview an event before creating it"""
    
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    event_type = request.POST.get('event_type')
    
    try:
        if event_type == 'public_holiday':
            form = PublicHolidayForm(request.POST)
        elif event_type == 'pupil_free_day':
            form = PupilFreeDayForm(request.POST)
        elif event_type == 'camp':
            form = CampEventForm(request.POST)
        elif event_type == 'excursion':
            form = ExcursionEventForm(request.POST)
        elif event_type == 'individual':
            form = IndividualStudentEventForm(request.POST)
        elif event_type == 'custom':
            form = CustomEventForm(request.POST)
        else:
            return JsonResponse({'error': 'Invalid event type'}, status=400)
        
        if form.is_valid():
            # Create a temporary instance without saving
            if event_type == 'camp':
                # Special handling for camp events
                start_date = form.cleaned_data['start_date']
                end_date = form.cleaned_data['end_date']
                year_levels = form.cleaned_data['year_levels']
                
                affected_classes = SchoolClass.objects.filter(
                    name__regex=r'^[' + ''.join(year_levels) + r'][A-Z]$'
                )
                
                total_students = sum(sc.student_set.count() for sc in affected_classes)
                duration = (end_date - start_date).days + 1
                
                preview_data = {
                    'valid': True,
                    'event_name': form.cleaned_data['camp_name'],
                    'date_range': f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')}",
                    'duration': f"{duration} days",
                    'affected_students': total_students,
                    'affected_groups': f"{len(year_levels)} year levels ({', '.join([f'Year {yl}' for yl in year_levels])})",
                    'events_created': duration,
                    'total_student_days': total_students * duration
                }
            else:
                # Regular event preview
                instance = form.save(commit=False)
                
                # Calculate affected students
                affected_count = 0
                affected_groups = []
                
                if hasattr(form, 'cleaned_data'):
                    if 'school_classes' in form.cleaned_data and form.cleaned_data['school_classes']:
                        classes = form.cleaned_data['school_classes']
                        affected_count += sum(sc.student_set.count() for sc in classes)
                        affected_groups.extend([sc.name for sc in classes])
                    
                    if 'students' in form.cleaned_data and form.cleaned_data['students']:
                        students = form.cleaned_data['students']
                        affected_count += students.count()
                        affected_groups.append(f"{students.count()} individual students")
                
                # For public holiday and pupil free day, affect all students
                if event_type in ['public_holiday', 'pupil_free_day']:
                    all_classes = SchoolClass.objects.all()
                    affected_count = sum(sc.student_set.count() for sc in all_classes)
                    affected_groups = ['All students']
                
                preview_data = {
                    'valid': True,
                    'event_name': instance.name,
                    'event_date': instance.event_date.strftime('%b %d, %Y'),
                    'affected_students': affected_count,
                    'affected_groups': ', '.join(affected_groups) if affected_groups else 'None',
                    'time_slots': 'All day' if not hasattr(instance, 'time_slots') or not instance.time_slots.exists() else f"{instance.time_slots.count()} time slots",
                    'reason': instance.reason
                }
            
            return JsonResponse(preview_data)
        else:
            return JsonResponse({
                'valid': False,
                'errors': form.errors
            })
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
@require_http_methods(["GET"])
def search_students(request):
    """AJAX endpoint for searching students"""
    
    query = request.GET.get('q', '').strip()
    year_level = request.GET.get('year_level', '').strip()
    
    students = Student.objects.all()
    
    if query:
        students = students.filter(
            Q(first_name__icontains=query) | 
            Q(last_name__icontains=query)
        )
    
    if year_level:
        students = students.filter(year_level=int(year_level))
    
    students = students.order_by('year_level', 'last_name', 'first_name')[:50]
    
    student_data = []
    for student in students:
        student_data.append({
            'id': student.id,
            'name': f"{student.first_name} {student.last_name}",
            'year_level': student.year_level,
            'school_class': student.school_class.name if student.school_class else 'N/A'
        })
    
    return JsonResponse({'students': student_data})

@login_required
def event_detail(request, event_id):
    """View details of a specific event"""
    
    event = get_object_or_404(OneOffEvent, id=event_id)
    
    context = {
        'event': event,
        'affected_students_count': event.get_affected_students_count(),
        'is_multi_day': event.is_multi_day(),
        'duration_days': event.get_duration_days(),
    }
    
    return render(request, 'scheduler/event_detail.html', context)

@login_required
def delete_event(request, event_id):
    """Delete an event"""
    
    event = get_object_or_404(OneOffEvent, id=event_id)
    
    if request.method == 'POST':
        event_name = event.name
        event.delete()
        messages.success(request, f'Event "{event_name}" has been deleted.')
        return redirect('event_management_dashboard')
    
    context = {
        'event': event,
        'affected_students_count': event.get_affected_students_count(),
    }
    
    return render(request, 'scheduler/delete_event_confirm.html', context)

@login_required
def quick_event_actions(request):
    """Handle quick event creation from dashboard"""
    
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    action = request.POST.get('action')
    event_date = request.POST.get('date', date.today().isoformat())
    
    try:
        event_date = date.fromisoformat(event_date)
    except ValueError:
        event_date = date.today()
    
    try:
        coach = Coach.objects.get(user=request.user)
    except Coach.DoesNotExist:
        coach = None
    
    if action == 'public_holiday':
        event = OneOffEvent.objects.create(
            name=f'Public Holiday - {event_date.strftime("%B %d, %Y")}',
            event_type=OneOffEvent.EventType.PUBLIC_HOLIDAY,
            event_date=event_date,
            reason='Public Holiday',
            created_by=coach
        )
        event.school_classes.set(SchoolClass.objects.all())
        
        affected_count = event.get_affected_students_count()
        return JsonResponse({
            'success': True,
            'message': f'Public Holiday created for {event_date.strftime("%B %d, %Y")}. {affected_count} students will be marked absent.',
            'event_id': event.id
        })
    
    elif action == 'pupil_free_day':
        event = OneOffEvent.objects.create(
            name=f'Pupil Free Day - {event_date.strftime("%B %d, %Y")}',
            event_type=OneOffEvent.EventType.PUPIL_FREE_DAY,
            event_date=event_date,
            reason='Pupil Free Day',
            created_by=coach
        )
        event.school_classes.set(SchoolClass.objects.all())
        
        affected_count = event.get_affected_students_count()
        return JsonResponse({
            'success': True,
            'message': f'Pupil Free Day created for {event_date.strftime("%B %d, %Y")}. {affected_count} students will be marked absent.',
            'event_id': event.id
        })
    
    else:
        return JsonResponse({'error': 'Invalid action'}, status=400)
