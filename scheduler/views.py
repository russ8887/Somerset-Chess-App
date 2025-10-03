from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.db.models import Exists, OuterRef, Count, Q, F, Avg, Max, Min, Case, When, Value, FloatField
from django.db import transaction
from datetime import date, timedelta

from .models import (
    AttendanceRecord, Coach, Enrollment, LessonNote, LessonSession,
    ScheduledGroup, ScheduledUnavailability, Student, Term, SchoolClass, TimeSlot, OneOffEvent
)
from .forms import LessonNoteForm
from django.http import JsonResponse
import json

def _prepare_lesson_context(lesson, editing_note_id=None, request=None):
    """A single, reliable helper to prepare all context for the lesson detail template."""
    lesson.has_absences = lesson.attendancerecord_set.filter(status='ABSENT').exists()
    
    # Add conflict information to attendance records
    for record in lesson.attendancerecord_set.all():
        # Cache conflict info to avoid repeated database queries
        if not hasattr(record, '_conflict_info'):
            record._conflict_info = record.get_scheduling_conflict()
    
    context = {'lesson': lesson, 'editing_note_id': editing_note_id}
    
    # Add request context if available (needed for template variables)
    if request:
        context['user'] = request.user
        # Get view_coach similar to DashboardView logic
        if hasattr(request.user, 'coach'):
            target_coach_id = request.GET.get('coach')
            if request.user.coach.is_head_coach and target_coach_id:
                try:
                    context['view_coach'] = Coach.objects.select_related('user').get(pk=target_coach_id)
                except Coach.DoesNotExist:
                    context['view_coach'] = request.user.coach
            else:
                context['view_coach'] = request.user.coach
        else:
            context['view_coach'] = None
    
    return context

# --- Main Page Views ---

class CoachLoginView(LoginView):
    template_name = 'scheduler/login.html'
    success_url = reverse_lazy('dashboard')

class DashboardView(LoginRequiredMixin, ListView):
    model = LessonSession
    template_name = 'scheduler/dashboard.html'
    context_object_name = 'lessons'

    def get_view_coach(self):
        if hasattr(self, '_view_coach'):
            return self._view_coach
        target_coach_id = self.request.GET.get('coach')
        if self.request.user.coach.is_head_coach and target_coach_id:
            self._view_coach = get_object_or_404(Coach.objects.select_related('user'), pk=target_coach_id)
        else:
            self._view_coach = self.request.user.coach
        return self._view_coach

    def get_date(self):
        date_str = self.request.GET.get('date', None)
        if date_str:
            try: return date.fromisoformat(date_str)
            except (ValueError, TypeError): pass
        return date.today()

    # scheduler/views.py in class DashboardView

    def get_queryset(self):
        if not hasattr(self.request.user, 'coach'):
            return LessonSession.objects.none()

        view_date = self.get_date()
        view_coach = self.get_view_coach()
        
        # --- NEW: Self-healing logic to create missing past lessons ---
        view_day_of_week = view_date.weekday() # 0=Monday, 1=Tuesday, etc.
        
        # 1. Find all groups that SHOULD have a lesson on this day
        expected_groups = ScheduledGroup.objects.filter(
            coach=view_coach,
            term__start_date__lte=view_date,
            term__end_date__gte=view_date,
            day_of_week=view_day_of_week
        )
        
        # 2. For each of those groups, ensure a LessonSession object exists.
        #    get_or_create is smart: it only creates one if it doesn't already exist.
        for group in expected_groups:
            LessonSession.objects.get_or_create(
                scheduled_group=group,
                lesson_date=view_date
            )
        # --- END of new logic ---

        absent_records = AttendanceRecord.objects.filter(
            lesson_session=OuterRef('pk'), 
            status='ABSENT'
        )

        return LessonSession.objects.filter(
            scheduled_group__coach=view_coach, 
            lesson_date=view_date
        ).prefetch_related(
            'scheduled_group__members', 
            'attendancerecord_set'      
        ).annotate(
            has_absences=Exists(absent_records)
        ).order_by('scheduled_group__time_slot__start_time')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # FIXED: Removed problematic attendance record creation logic that was causing
        # students to appear in wrong lessons. The self-healing logic in get_queryset()
        # already handles creating LessonSession objects, and AttendanceRecord objects
        # should only be created when students actually attend lessons, not automatically
        # for all group members.

        # --- OneOffEvent Auto-Absence Logic ---
        view_date = self.get_date()
        view_coach = self.get_view_coach()

        # Get all OneOffEvent for this date
        one_off_events = OneOffEvent.objects.filter(event_date=view_date)

        for event in one_off_events:
            # Check if event affects all time slots or specific ones
            affected_time_slots = event.time_slots.all() if event.time_slots.exists() else TimeSlot.objects.all()

            # Find lessons that are affected by this event
            affected_lessons = []
            for lesson in context['lessons']:
                if lesson.scheduled_group.time_slot in affected_time_slots:
                    affected_lessons.append(lesson)

            if affected_lessons:
                # Get all students affected by this event
                affected_students = set()

                # Add directly assigned students
                affected_students.update(event.students.all())

                # Add students from affected school classes
                for school_class in event.school_classes.all():
                    affected_students.update(school_class.student_set.all())

                # Mark affected students as absent in affected lessons
                for lesson in affected_lessons:
                    for student in affected_students:
                        # Find enrollment for this student in the lesson's term
                        try:
                            enrollment = Enrollment.objects.get(
                                student=student,
                                term=lesson.scheduled_group.term
                            )
                            # Check if student is in this lesson
                            if enrollment in lesson.scheduled_group.members.all():
                                # Mark as absent with the specific event reason
                                # Map event types to appropriate absence reasons
                                reason_mapping = {
                                    'PUBLIC_HOLIDAY': 'CLASS_EVENT',
                                    'PUPIL_FREE_DAY': 'CLASS_EVENT', 
                                    'CAMP': 'CLASS_EVENT',
                                    'EXCURSION': 'CLASS_EVENT',
                                    'INDIVIDUAL': 'OTHER',
                                    'CUSTOM': 'CLASS_EVENT'
                                }
                                
                                absence_reason = reason_mapping.get(event.event_type, 'CLASS_EVENT')
                                
                                attendance_record, created = AttendanceRecord.objects.update_or_create(
                                    lesson_session=lesson,
                                    enrollment=enrollment,
                                    defaults={
                                        'status': 'ABSENT',
                                        'reason_for_absence': absence_reason
                                    }
                                )
                                
                                # Create a lesson note with the specific event reason for better tracking
                                if created or not hasattr(attendance_record, 'lessonnote'):
                                    LessonNote.objects.update_or_create(
                                        attendance_record=attendance_record,
                                        defaults={
                                            'coach_comments': f"Absent due to: {event.reason} ({event.name})"
                                        }
                                    )
                        except Enrollment.DoesNotExist:
                            continue  # Student not enrolled in this term

        # --- End OneOffEvent Logic ---
        context.update({
            'view_date': view_date, 'previous_day': view_date - timedelta(days=1), 
            'next_day': view_date + timedelta(days=1), 'today_date': date.today(),
            'view_coach': view_coach,
        })
        
        # FIXED: Always provide term_week_display to prevent template crashes
        current_term = Term.objects.filter(start_date__lte=view_date, end_date__gte=view_date).first()
        if current_term:
            context['term_week_display'] = f"{current_term.name}, Week {(view_date - current_term.start_date).days // 7 + 1}"
        else:
            # Provide fallback when viewing dates outside any term
            context['term_week_display'] = f"No term active for {view_date.strftime('%B %d, %Y')}"
        
        # --- Missed Lessons Notification ---
        # Find lessons with PENDING status that need attention
        pending_lessons = AttendanceRecord.objects.filter(
            lesson_session__scheduled_group__coach=view_coach,
            lesson_session__lesson_date__lte=view_date,
            status='PENDING'
        ).select_related(
            'lesson_session__scheduled_group',
            'enrollment__student'
        ).order_by('lesson_session__lesson_date')

        # Group by lesson for easier display
        missed_lessons = {}
        for record in pending_lessons:
            lesson_key = f"{record.lesson_session.lesson_date}_{record.lesson_session.scheduled_group.name}"
            if lesson_key not in missed_lessons:
                missed_lessons[lesson_key] = {
                    'lesson': record.lesson_session,
                    'pending_count': 0,
                    'students': []
                }
            missed_lessons[lesson_key]['pending_count'] += 1
            missed_lessons[lesson_key]['students'].append(record.enrollment.student)

        context['missed_lessons'] = list(missed_lessons.values())[:5]  # Show top 5
        context['total_missed'] = len(missed_lessons)

        if hasattr(self.request.user, 'coach') and self.request.user.coach.is_head_coach:
            context['all_coaches'] = Coach.objects.select_related('user').order_by('user__first_name')
            try:
                context['selected_coach_id'] = int(self.request.GET.get('coach', self.request.user.coach.id))
            except (ValueError, TypeError):
                context['selected_coach_id'] = self.request.user.coach.id
        return context

@login_required
def student_report_view(request, student_pk, term_pk):
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        student = get_object_or_404(Student, pk=student_pk)
        term = get_object_or_404(Term, pk=term_pk)
        
        # Get the viewing date and coach from request for back navigation
        view_date = request.GET.get('date', date.today().isoformat())
        view_coach = request.GET.get('coach', '')
        
        # Debug logging
        logger.info(f"Student report requested: Student ID {student_pk} ({student}), Term ID {term_pk} ({term})")
        
        records = AttendanceRecord.objects.filter(enrollment__student=student, enrollment__term=term) \
            .order_by('lesson_session__lesson_date').select_related('lesson_session__scheduled_group', 'lessonnote')

        # Debug logging
        logger.info(f"Found {records.count()} attendance records for {student} in {term}")
        
        # Calculate detailed attendance counts
        regular_present_count = records.filter(status='PRESENT').count()
        sick_present_count = records.filter(status='SICK_PRESENT').count()
        refuses_present_count = records.filter(status='REFUSES_PRESENT').count()
        fill_in_count = records.filter(status='FILL_IN').count()
        
        # Calculate absence counts by reason
        absent_count = records.filter(status='ABSENT').count()
        sick_absent_count = records.filter(status='ABSENT', reason_for_absence='SICK').count()
        teacher_refusal_count = records.filter(status='ABSENT', reason_for_absence='TEACHER_REFUSAL').count()
        class_event_count = records.filter(status='ABSENT', reason_for_absence='CLASS_EVENT').count()
        
        # Calculate totals
        total_attended = regular_present_count + sick_present_count + refuses_present_count + fill_in_count
        
        # Get individual availability data
        individual_unavailabilities = ScheduledUnavailability.objects.filter(students=student)
        
        # Get class-based unavailabilities (inherited from student's class)
        class_unavailabilities = ScheduledUnavailability.objects.filter(
            school_classes=student.school_class
        ) if student.school_class else ScheduledUnavailability.objects.none()
        
        time_slots = TimeSlot.objects.all().order_by('start_time')
        
        # Create comprehensive availability maps
        individual_unavailable_slots = {}
        class_unavailable_slots = {}
        
        # Map individual unavailabilities
        for unavail in individual_unavailabilities:
            day = unavail.day_of_week
            if day not in individual_unavailable_slots:
                individual_unavailable_slots[day] = []
            individual_unavailable_slots[day].append(unavail.time_slot.pk)
        
        # Map class-based unavailabilities
        for unavail in class_unavailabilities:
            day = unavail.day_of_week
            if day not in class_unavailable_slots:
                class_unavailable_slots[day] = []
            class_unavailable_slots[day].append(unavail.time_slot.pk)
        
        # Create combined unavailable slots for backward compatibility
        unavailable_slots = {}
        for day in range(5):  # Monday to Friday
            unavailable_slots[day] = []
            if day in individual_unavailable_slots:
                unavailable_slots[day].extend(individual_unavailable_slots[day])
            if day in class_unavailable_slots:
                unavailable_slots[day].extend(class_unavailable_slots[day])
            # Remove duplicates
            unavailable_slots[day] = list(set(unavailable_slots[day]))
        
        logger.info(f"Attendance summary: {total_attended} total attended, {absent_count} absent")

        context = {
            'student': student,
            'term': term,
            'records': records,
            'view_date': view_date,
            'view_coach': view_coach,
            # Detailed attendance counts
            'regular_present_count': regular_present_count,
            'sick_present_count': sick_present_count,
            'refuses_present_count': refuses_present_count,
            'fill_in_count': fill_in_count,
            'total_attended': total_attended,
            # Absence counts
            'absent_count': absent_count,
            'sick_absent_count': sick_absent_count,
            'teacher_refusal_count': teacher_refusal_count,
            'class_event_count': class_event_count,
            # Availability data
            'individual_unavailabilities': individual_unavailabilities,
            'class_unavailabilities': class_unavailabilities,
            'time_slots': time_slots,
            'unavailable_slots': unavailable_slots,
            'individual_unavailable_slots': individual_unavailable_slots,
            'class_unavailable_slots': class_unavailable_slots,
        }
        
        # Add debug info to template for troubleshooting
        if request.GET.get('debug'):
            context['debug_info'] = {
                'student_pk': student_pk,
                'term_pk': term_pk,
                'records_count': records.count(),
                'request_path': request.path,
                'is_htmx': request.headers.get('HX-Request', False)
            }
        
        return render(request, 'scheduler/student_report.html', context)
        
    except Exception as e:
        logger.error(f"Error in student_report_view: {str(e)}")
        # Return a simple error message for debugging
        return render(request, 'scheduler/student_report.html', {
            'error': f"Error loading student report: {str(e)}",
            'student': None,
            'term': None,
            'records': [],
            'view_date': date.today().isoformat(),
            'regular_present_count': 0,
            'sick_present_count': 0,
            'refuses_present_count': 0,
            'fill_in_count': 0,
            'total_attended': 0,
            'absent_count': 0,
            'sick_absent_count': 0,
            'teacher_refusal_count': 0,
            'class_event_count': 0,
        })

@login_required
def manage_availability(request):
    if request.method == 'POST':
        class_id = request.POST.get('school_class')
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        unavailabilities_to_clear = ScheduledUnavailability.objects.filter(school_classes=school_class)
        for ua in unavailabilities_to_clear:
            ua.school_classes.remove(school_class)
            if not ua.students.exists() and not ua.school_classes.exists():
                ua.delete()
        for key, value in request.POST.items():
            if key.startswith('slot_'):
                _, slot_id, day_index = key.split('_')
                time_slot = get_object_or_404(TimeSlot, pk=slot_id)
                unavailability, _ = ScheduledUnavailability.objects.get_or_create(
                    name=f"Recurring Unavailability", day_of_week=day_index, time_slot=time_slot
                )
                unavailability.school_classes.add(school_class)
        return redirect(f"{reverse('manage-availability')}?class_id={class_id}")
    
    all_classes = SchoolClass.objects.all().order_by('name')
    time_slots = TimeSlot.objects.all().order_by('start_time')
    days = [(i, d) for i, d in enumerate(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'])]
    selected_class_id = request.GET.get('class_id')
    availability_map = {}
    selected_class = None
    if selected_class_id:
        selected_class = get_object_or_404(SchoolClass, pk=selected_class_id)
        unavailable_slots = ScheduledUnavailability.objects.filter(school_classes=selected_class)
        for slot in unavailable_slots:
            availability_map[(slot.time_slot_id, slot.day_of_week)] = True
    context = {'all_classes': all_classes, 'selected_class': selected_class, 'time_slots': time_slots, 'days': days, 'availability_map': availability_map}
    return render(request, 'scheduler/availability_grid.html', context)

# --- Fill-in Management View ---

# scheduler/views.py

# scheduler/views.py

# scheduler/views.py

@login_required
def manage_lesson_view(request, lesson_pk):
    lesson = get_object_or_404(
        LessonSession.objects.select_related('scheduled_group__term', 'scheduled_group__time_slot'),
        pk=lesson_pk
    )
    term = lesson.scheduled_group.term

    if request.method == 'POST':
        # This POST logic for adding/removing remains the same and is correct
        if 'add_enrollment_pk' in request.POST:
            enrollment_to_add = get_object_or_404(Enrollment, pk=request.POST.get('add_enrollment_pk'))
            AttendanceRecord.objects.update_or_create(
                lesson_session=lesson,
                enrollment=enrollment_to_add,
                defaults={'status': 'FILL_IN'}
            )
        if 'remove_record_pk' in request.POST:
            record_to_remove = get_object_or_404(AttendanceRecord, pk=request.POST.get('remove_record_pk'))
            if record_to_remove.status == 'FILL_IN':
                record_to_remove.delete()
        return redirect('manage-lesson', lesson_pk=lesson.pk)

    # --- Prepare a SINGLE master list of candidates ---
    
    # 1. Get students already in this specific lesson
    present_student_ids = lesson.attendancerecord_set.all().values_list('enrollment__student_id', flat=True)

    # 2. Get all enrollments for the term, excluding those already in the lesson
    all_candidates = Enrollment.objects.filter(term=term) \
        .exclude(student_id__in=present_student_ids) \
        .select_related('student__school_class') \
        .annotate(
            actual_lessons=Count('attendancerecord', filter=Q(attendancerecord__status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']))
        )

    # 3. Determine which of them are "suggested" (available and not busy)
    busy_student_ids = AttendanceRecord.objects.filter(lesson_session__lesson_date=lesson.lesson_date).values_list('enrollment__student_id', flat=True)
    lesson_day = lesson.lesson_date.weekday()
    lesson_timeslot = lesson.scheduled_group.time_slot
    unavailable_student_ids = ScheduledUnavailability.objects.filter(day_of_week=lesson_day, time_slot=lesson_timeslot).values_list('students__id', flat=True)
    unavailable_class_student_ids = Student.objects.filter(school_class__scheduledunavailability__day_of_week=lesson_day, school_class__scheduledunavailability__time_slot=lesson_timeslot).values_list('id', flat=True)
    
    non_suggested_ids = set(list(busy_student_ids) + list(unavailable_student_ids) + list(unavailable_class_student_ids))

    # 4. Process each candidate and add calculated fields
    candidates_list = []
    for enrollment in all_candidates:
        # Set availability status
        enrollment.is_suggested = enrollment.student_id not in non_suggested_ids
        
        # Calculate lesson balance using the model method
        enrollment.effective_deficit = enrollment.get_lesson_balance()
        
        # Calculate progress color based on actual lessons vs expected
        actual_lessons = enrollment.actual_lessons
        if actual_lessons <= 2:
            enrollment.progress_color = 'danger'  # Red - far behind
        elif actual_lessons <= 4:
            enrollment.progress_color = 'warning'  # Orange - behind
        else:
            enrollment.progress_color = 'success'  # Green - on track
            
        candidates_list.append(enrollment)

    # 5. Sort by deficit (biggest deficit first), then by fewest actual lessons
    candidates_list.sort(key=lambda e: (-e.effective_deficit, e.actual_lessons))

    # Get the list of students currently in the lesson for display
    current_records = lesson.attendancerecord_set.select_related(
        'enrollment__student__school_class'
    ).order_by('enrollment__student__last_name')

    context = {
        'lesson': lesson,
        'current_records': current_records,
        'all_candidates': candidates_list,
    }
    return render(request, 'scheduler/manage_lesson.html', context)
# --- HTMX Helper Views ---

@login_required
@require_POST
def mark_attendance(request, pk, status):
    record = get_object_or_404(AttendanceRecord, pk=pk)
    new_status = status.upper()
    
    # Handle toggle behavior: if clicking the same status, go back to PENDING
    if record.status == new_status:
        record.status = 'PENDING'
    else:
        record.status = new_status
    
    # Clear absence reason if not absent
    if record.status != 'ABSENT':
        record.reason_for_absence = None
    
    record.save()
    context = _prepare_lesson_context(record.lesson_session, request=request)
    return render(request, 'scheduler/_lesson_detail.html', context)

@login_required
@require_POST
def save_reason(request, pk, reason_code):
    record = get_object_or_404(AttendanceRecord, pk=pk)
    if reason_code in AttendanceRecord.AbsenceReason.values:
        record.reason_for_absence = reason_code
        record.save()
    lesson = record.lesson_session
    context = _prepare_lesson_context(lesson, request=request)
    return render(request, 'scheduler/_lesson_detail.html', context)

@login_required
def create_note_view(request, record_pk):
    record = get_object_or_404(AttendanceRecord, pk=record_pk)
    note, created = LessonNote.objects.get_or_create(attendance_record=record)
    lesson = record.lesson_session
    
    if request.method == 'POST':
        form = LessonNoteForm(request.POST, instance=note)
        if form.is_valid():
            form.save()
            # After saving, redirect back to the same date
            lesson_date = lesson.lesson_date.isoformat()
            return redirect(f'/?date={lesson_date}')
    else:
        form = LessonNoteForm(instance=note)
    
    # Check if this is an HTMX request
    if request.headers.get('HX-Request'):
        # Return just the form for HTMX
        context = {
            'note_form': form,
            'note': note,
            'record': record
        }
        return render(request, 'scheduler/_note_form_response.html', context)
    else:
        # Return full lesson context for regular requests
        context = _prepare_lesson_context(lesson, editing_note_id=note.id)
        context['note_form'] = form
        return render(request, 'scheduler/_lesson_detail.html', context)


# --- New Visual Slot Finder API ---

@login_required
def get_available_slots_api(request, student_id):
    """Simple API to get all available slots for a student across all days/coaches"""
    if not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request'})
    
    try:
        student = get_object_or_404(Student, pk=student_id)
        current_term = Term.get_active_term()
        
        if not current_term:
            return JsonResponse({'success': False, 'error': 'No active term found'})
        
        # Get student's enrollment
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            student_enrollment_type = enrollment.enrollment_type
        except Enrollment.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Student not enrolled in current term'})
        
        # Get all available slots for this student
        available_slots = []
        
        # Check all days of the week
        for day in range(5):  # Monday to Friday
            day_name = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'][day]
            
            # Check if student is available on this day
            if not _is_student_available_on_day(student, day):
                continue
            
            # Get all time slots
            time_slots = TimeSlot.objects.all().order_by('start_time')
            
            for time_slot in time_slots:
                # Check if student is available at this specific time
                if not _is_student_available_at_time(student, day, time_slot):
                    continue
                
                # Find all groups at this day/time across all coaches
                groups_at_time = ScheduledGroup.objects.filter(
                    term=current_term,
                    day_of_week=day,
                    time_slot=time_slot
                ).select_related('coach').prefetch_related('members')
                
                for group in groups_at_time:
                    # Check if student can join this group
                    if _can_student_join_group(student, group, student_enrollment_type):
                        available_slots.append({
                            'group_id': group.id,
                            'group_name': group.name,
                            'day': day,
                            'day_name': day_name,
                            'time_slot': str(time_slot),
                            'coach_name': str(group.coach) if group.coach else 'No Coach',
                            'current_size': group.get_current_size(),
                            'max_capacity': group.get_type_based_max_capacity(),
                            'has_space': group.has_space(),
                            'group_type': group.group_type
                        })
        
        return JsonResponse({
            'success': True,
            'student_name': f"{student.first_name} {student.last_name}",
            'available_slots': available_slots,
            'total_slots': len(available_slots)
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def move_student_to_slot_api(request, student_id):
    """API to move a student to a specific slot"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST request required'})
    
    if not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request'})
    
    try:
        data = json.loads(request.body)
        student = get_object_or_404(Student, pk=student_id)
        target_group_id = data.get('group_id')
        
        if not target_group_id:
            return JsonResponse({'success': False, 'error': 'Group ID required'})
        
        target_group = get_object_or_404(ScheduledGroup, pk=target_group_id)
        current_term = Term.get_active_term()
        
        if not current_term:
            return JsonResponse({'success': False, 'error': 'No active term found'})
        
        # Get student's enrollment
        try:
            enrollment = student.enrollment_set.get(term=current_term)
        except Enrollment.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Student not enrolled in current term'})
        
        # Use atomic transaction for safety
        with transaction.atomic():
            # Find current groups for this student
            current_groups = ScheduledGroup.objects.filter(members=enrollment, term=current_term)
            
            # Remove from current groups
            for group in current_groups:
                group.members.remove(enrollment)
            
            # Add to new group
            target_group.members.add(enrollment)
            
            return JsonResponse({
                'success': True,
                'message': f'Successfully moved {student.first_name} to {target_group.name}',
                'new_group': target_group.name,
                'previous_groups': [g.name for g in current_groups]
            })
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# Helper functions for availability checking
def _is_student_available_on_day(student, day):
    """Check if student is available on a specific day"""
    # Check for individual unavailabilities
    individual_conflicts = ScheduledUnavailability.objects.filter(
        students=student,
        day_of_week=day
    ).exists()
    
    # Check for class-based unavailabilities
    class_conflicts = False
    if student.school_class:
        class_conflicts = ScheduledUnavailability.objects.filter(
            school_classes=student.school_class,
            day_of_week=day
        ).exists()
    
    return not (individual_conflicts or class_conflicts)


def _is_student_available_at_time(student, day, time_slot):
    """Check if student is available at a specific day/time"""
    conflict_info = student.has_scheduling_conflict(day, time_slot)
    return not conflict_info['has_conflict']


def _can_student_join_group(student, group, student_enrollment_type):
    """Check if student can join a specific group"""
    # Check if group has space or if we allow overfilling
    if not group.has_space():
        # For now, allow overfilling - we'll show visual indicators
        pass
    
    # Check basic compatibility
    if not group.is_compatible_with_student(student, student_enrollment_type):
        return False
    
    # Check if student is already in this group
    current_term = Term.get_active_term()
    if current_term:
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            if enrollment in group.members.all():
                return False
        except Enrollment.DoesNotExist:
            return False
    
    return True


@login_required
def manage_student_availability(request, student_pk):
    """Handle individual student availability management"""
    student = get_object_or_404(Student, pk=student_pk)
    
    if request.method == 'POST':
        # Clear existing individual unavailabilities for this student
        unavailabilities_to_clear = ScheduledUnavailability.objects.filter(students=student)
        for ua in unavailabilities_to_clear:
            ua.students.remove(student)
            # If no students or classes are left, delete the unavailability
            if not ua.students.exists() and not ua.school_classes.exists():
                ua.delete()
        
        # Process new unavailabilities
        for key, value in request.POST.items():
            if key.startswith('unavailable_'):
                # Parse the key: unavailable_{time_slot_pk}_{day_num}
                parts = key.split('_')
                if len(parts) == 3:
                    time_slot_pk = parts[1]
                    day_num = int(parts[2])
                    
                    try:
                        time_slot = TimeSlot.objects.get(pk=time_slot_pk)
                        # Create or get the unavailability record
                        unavailability, created = ScheduledUnavailability.objects.get_or_create(
                            name=f"Individual Unavailability - {student.first_name} {student.last_name}",
                            day_of_week=day_num,
                            time_slot=time_slot
                        )
                        unavailability.students.add(student)
                    except TimeSlot.DoesNotExist:
                        continue
        
        # Redirect back to student report
        return_url = request.POST.get('return_url', reverse('student-report', args=[student.pk, Term.get_active_term().pk]))
        return redirect(return_url)
    
    # If GET request, redirect to student report (shouldn't normally happen)
    active_term = Term.get_active_term()
    if active_term:
        return redirect('student-report', student_pk=student.pk, term_pk=active_term.pk)
    else:
        return redirect('dashboard')

@login_required
def view_lesson_note(request, pk):
    note = get_object_or_404(LessonNote, pk=pk)
    lesson = note.attendance_record.lesson_session
    
    # Check if this is an HTMX request
    if request.headers.get('HX-Request'):
        # Return just the note display for HTMX
        context = {
            'note': note,
            'record': note.attendance_record
        }
        return render(request, 'scheduler/_lesson_note_display.html', context)
    else:
        # Return full lesson context for regular requests
        context = _prepare_lesson_context(lesson)
        return render(request, 'scheduler/_lesson_detail.html', context)

@login_required
def edit_lesson_note(request, pk):
    note = get_object_or_404(LessonNote, pk=pk)
    lesson = note.attendance_record.lesson_session
    
    if request.method == 'POST':
        form = LessonNoteForm(request.POST, instance=note)
        if form.is_valid():
            form.save()
            # After saving, redirect back to the same date
            lesson_date = lesson.lesson_date.isoformat()
            return redirect(f'/?date={lesson_date}')
    else:
        # For a GET request, create the form here
        form = LessonNoteForm(instance=note)

    # Check if this is an HTMX request
    if request.headers.get('HX-Request'):
        # Return just the form for HTMX
        context = {
            'note_form': form,
            'note': note,
            'record': note.attendance_record
        }
        return render(request, 'scheduler/_note_form_response.html', context)
    else:
        # Return full lesson context for regular requests
        context = _prepare_lesson_context(lesson, editing_note_id=note.id)
        context['note_form'] = form
        return render(request, 'scheduler/_lesson_detail.html', context)


# --- Advanced Analytics Dashboard ---

@login_required
def analytics_dashboard(request):
    """Advanced analytics dashboard with comprehensive reporting"""
    
    # Check if user has permission (head coach or admin)
    if not (hasattr(request.user, 'coach') and request.user.coach.is_head_coach) and not request.user.is_staff:
        return redirect('dashboard')
    
    try:
        # Get current term
        current_term = Term.get_active_term()
        if not current_term:
            return render(request, 'scheduler/analytics_dashboard.html', {
                'error': 'No active term found. Please contact an administrator.'
            })
        
        # Date range filtering
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        
        if start_date:
            try:
                start_date = date.fromisoformat(start_date)
            except ValueError:
                start_date = current_term.start_date
        else:
            start_date = current_term.start_date
        
        if end_date:
            try:
                end_date = date.fromisoformat(end_date)
            except ValueError:
                end_date = current_term.end_date
        else:
            end_date = current_term.end_date
        
        # Student Progress Analytics
        try:
            student_analytics = _get_student_analytics(current_term, start_date, end_date)
        except Exception as e:
            student_analytics = {
                'total_students': 0, 'students_behind': 0, 'students_on_track': 0,
                'students_ahead': 0, 'lesson_balances': [], 'skill_distribution': [],
                'avg_attendance_rate': 0, 'error': f'Student analytics error: {str(e)}'
            }
        
        # Coach Performance Metrics
        try:
            coach_analytics = _get_coach_analytics(current_term, start_date, end_date)
        except Exception as e:
            coach_analytics = []
        
        # System Utilization Insights
        try:
            utilization_analytics = _get_utilization_analytics(current_term, start_date, end_date)
        except Exception as e:
            utilization_analytics = {
                'time_slot_utilization': [], 'day_utilization': [],
                'group_type_distribution': [], 'capacity_stats': {
                    'underutilized': 0, 'optimal': 0, 'full': 0, 'total': 0
                }
            }
        
        # Attendance Pattern Analysis
        try:
            attendance_analytics = _get_attendance_analytics(current_term, start_date, end_date)
        except Exception as e:
            attendance_analytics = {
                'total_records': 0, 'attendance_breakdown': [], 'absence_reasons': [],
                'weekly_trends': [], 'overall_attendance_rate': 0
            }
        
        context = {
            'current_term': current_term,
            'start_date': start_date,
            'end_date': end_date,
            'student_analytics': student_analytics,
            'coach_analytics': coach_analytics,
            'utilization_analytics': utilization_analytics,
            'attendance_analytics': attendance_analytics,
        }
        
        return render(request, 'scheduler/analytics_dashboard.html', context)
        
    except Exception as e:
        # Catch-all error handler
        return render(request, 'scheduler/analytics_dashboard.html', {
            'error': f'Analytics dashboard error: {str(e)}. Please contact an administrator.'
        })


def _get_student_analytics(term, start_date, end_date):
    """Calculate student progress analytics"""
    
    # Get all enrollments for the term
    enrollments = Enrollment.objects.filter(term=term).select_related('student').annotate(
        total_lessons=Count('attendancerecord', filter=Q(
            attendancerecord__lesson_session__lesson_date__range=[start_date, end_date]
        )),
        attended_lessons=Count('attendancerecord', filter=Q(
            attendancerecord__status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT'],
            attendancerecord__lesson_session__lesson_date__range=[start_date, end_date]
        )),
        absent_lessons=Count('attendancerecord', filter=Q(
            attendancerecord__status='ABSENT',
            attendancerecord__lesson_session__lesson_date__range=[start_date, end_date]
        ))
    )
    
    # Calculate lesson balance statistics
    lesson_balances = []
    students_behind = 0
    students_on_track = 0
    students_ahead = 0
    
    for enrollment in enrollments:
        balance = enrollment.get_lesson_balance()
        lesson_balances.append({
            'student': {
                'id': enrollment.student.id,
                'name': f"{enrollment.student.first_name} {enrollment.student.last_name}",
                'skill_level': enrollment.student.skill_level
            },
            'balance': balance,
            'attended': enrollment.attended_lessons,
            'total': enrollment.total_lessons,
            'target': enrollment.adjusted_target
        })
        
        if balance > 2:
            students_behind += 1
        elif balance < -1:
            students_ahead += 1
        else:
            students_on_track += 1
    
    # Sort by balance (most behind first)
    lesson_balances.sort(key=lambda x: x['balance'], reverse=True)
    
    # Skill level distribution
    skill_distribution = Student.objects.filter(enrollment__term=term).values('skill_level').annotate(
        count=Count('id')
    ).order_by('skill_level')
    
    # Calculate safe average attendance rate (avoiding division by zero)
    enrollments_with_rates = enrollments.annotate(
        attendance_rate=Case(
            When(total_lessons=0, then=Value(0.0)),
            default=F('attended_lessons') * 100.0 / F('total_lessons'),
            output_field=FloatField()
        )
    )
    avg_attendance_rate = enrollments_with_rates.aggregate(
        avg_rate=Avg('attendance_rate')
    )['avg_rate'] or 0
    
    return {
        'total_students': enrollments.count(),
        'students_behind': students_behind,
        'students_on_track': students_on_track,
        'students_ahead': students_ahead,
        'lesson_balances': lesson_balances[:20],  # Top 20 for display
        'skill_distribution': list(skill_distribution),
        'avg_attendance_rate': round(avg_attendance_rate, 1)
    }


def _get_coach_analytics(term, start_date, end_date):
    """Calculate coach performance metrics"""
    
    coaches = Coach.objects.all().select_related('user').annotate(
        total_lessons=Count('scheduledgroup__lessonsession', filter=Q(
            scheduledgroup__term=term,
            scheduledgroup__lessonsession__lesson_date__range=[start_date, end_date]
        )),
        total_students=Count('scheduledgroup__members', filter=Q(
            scheduledgroup__term=term
        ), distinct=True),
        total_groups=Count('scheduledgroup', filter=Q(
            scheduledgroup__term=term
        ))
    )
    
    coach_stats = []
    for coach in coaches:
        if coach.total_lessons > 0:
            # Calculate attendance rates for this coach's lessons
            attendance_records = AttendanceRecord.objects.filter(
                lesson_session__scheduled_group__coach=coach,
                lesson_session__scheduled_group__term=term,
                lesson_session__lesson_date__range=[start_date, end_date]
            )
            
            total_records = attendance_records.count()
            attended_records = attendance_records.filter(
                status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
            ).count()
            
            attendance_rate = (attended_records / total_records * 100) if total_records > 0 else 0
            
            # Calculate average group size
            avg_group_size = coach.scheduledgroup_set.filter(term=term).aggregate(
                avg_size=Avg('members__count')
            )['avg_size'] or 0
            
            coach_stats.append({
                'coach': coach,
                'total_lessons': coach.total_lessons,
                'total_students': coach.total_students,
                'total_groups': coach.total_groups,
                'attendance_rate': round(attendance_rate, 1),
                'avg_group_size': round(avg_group_size, 1)
            })
    
    # Sort by total lessons (most active first)
    coach_stats.sort(key=lambda x: x['total_lessons'], reverse=True)
    
    return coach_stats


def _get_utilization_analytics(term, start_date, end_date):
    """Calculate system utilization insights"""
    
    # Time slot utilization
    time_slots = TimeSlot.objects.all().annotate(
        group_count=Count('scheduledgroup', filter=Q(
            scheduledgroup__term=term
        )),
        lesson_count=Count('scheduledgroup__lessonsession', filter=Q(
            scheduledgroup__term=term,
            scheduledgroup__lessonsession__lesson_date__range=[start_date, end_date]
        ))
    ).order_by('start_time')
    
    # Day of week utilization
    day_utilization = []
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    
    for day_num, day_name in enumerate(days):
        group_count = ScheduledGroup.objects.filter(
            term=term,
            day_of_week=day_num
        ).count()
        
        lesson_count = LessonSession.objects.filter(
            scheduled_group__term=term,
            scheduled_group__day_of_week=day_num,
            lesson_date__range=[start_date, end_date]
        ).count()
        
        day_utilization.append({
            'day': day_name,
            'groups': group_count,
            'lessons': lesson_count
        })
    
    # Group type distribution
    group_types = ScheduledGroup.objects.filter(term=term).values('group_type').annotate(
        count=Count('id'),
        avg_size=Avg('members__count')
    ).order_by('group_type')
    
    # Capacity optimization
    groups_with_capacity = ScheduledGroup.objects.filter(term=term).annotate(
        current_size=Count('members')
    )
    
    underutilized_groups = 0
    optimal_groups = 0
    full_groups = 0
    
    for group in groups_with_capacity:
        max_capacity = group.get_type_based_max_capacity()
        utilization_rate = (group.current_size / max_capacity) if max_capacity > 0 else 0
        
        if utilization_rate < 0.7:
            underutilized_groups += 1
        elif utilization_rate >= 1.0:
            full_groups += 1
        else:
            optimal_groups += 1
    
    return {
        'time_slot_utilization': list(time_slots),
        'day_utilization': day_utilization,
        'group_type_distribution': list(group_types),
        'capacity_stats': {
            'underutilized': underutilized_groups,
            'optimal': optimal_groups,
            'full': full_groups,
            'total': groups_with_capacity.count()
        }
    }


def _get_attendance_analytics(term, start_date, end_date):
    """Calculate attendance pattern analysis"""
    
    # Overall attendance statistics
    total_records = AttendanceRecord.objects.filter(
        enrollment__term=term,
        lesson_session__lesson_date__range=[start_date, end_date]
    )
    
    attendance_stats = total_records.values('status').annotate(
        count=Count('id')
    ).order_by('status')
    
    # Absence reasons breakdown
    absence_reasons = total_records.filter(status='ABSENT').values('reason_for_absence').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Weekly attendance trends (last 8 weeks)
    weekly_trends = []
    current_date = end_date
    
    for week in range(8):
        week_start = current_date - timedelta(days=current_date.weekday() + (week * 7))
        week_end = week_start + timedelta(days=6)
        
        if week_start < start_date:
            break
        
        week_records = AttendanceRecord.objects.filter(
            enrollment__term=term,
            lesson_session__lesson_date__range=[week_start, week_end]
        )
        
        total_week = week_records.count()
        attended_week = week_records.filter(
            status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
        ).count()
        
        attendance_rate = (attended_week / total_week * 100) if total_week > 0 else 0
        
        weekly_trends.append({
            'week_start': week_start,
            'week_end': week_end,
            'attendance_rate': round(attendance_rate, 1),
            'total_lessons': total_week
        })
    
    weekly_trends.reverse()  # Show oldest to newest
    
    return {
        'total_records': total_records.count(),
        'attendance_breakdown': list(attendance_stats),
        'absence_reasons': list(absence_reasons),
        'weekly_trends': weekly_trends,
        'overall_attendance_rate': round(
            (total_records.filter(
                status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
            ).count() / total_records.count() * 100) if total_records.count() > 0 else 0, 1
        )
    }
