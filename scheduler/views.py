from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.db.models import Exists, OuterRef, Count, Q, F
from datetime import date, timedelta

from .models import (
    AttendanceRecord, Coach, Enrollment, LessonNote, LessonSession,
    ScheduledGroup, ScheduledUnavailability, Student, Term, SchoolClass, TimeSlot, OneOffEvent
)
from .forms import LessonNoteForm

def _prepare_lesson_context(lesson, editing_note_id=None):
    """A single, reliable helper to prepare all context for the lesson detail template."""
    lesson.has_absences = lesson.attendancerecord_set.filter(status='ABSENT').exists()
    return {'lesson': lesson, 'editing_note_id': editing_note_id}

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
        for lesson in context['lessons']:
            expected_enrollments = lesson.scheduled_group.members.all()
            existing_enrollments = {rec.enrollment for rec in lesson.attendancerecord_set.all()}
            missing_enrollments = [en for en in expected_enrollments if en not in existing_enrollments]
            if missing_enrollments:
                records_to_create = [AttendanceRecord(lesson_session=lesson, enrollment=en) for en in missing_enrollments]
                AttendanceRecord.objects.bulk_create(records_to_create)
                # Refresh the lesson's attendance records from database
                lesson._attendance_records_cache = None

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
        current_term = Term.objects.filter(start_date__lte=view_date, end_date__gte=view_date).first()
        if current_term:
            context['term_week_display'] = f"{current_term.name}, Week {(view_date - current_term.start_date).days // 7 + 1}"
        
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
        time_slots = TimeSlot.objects.all().order_by('start_time')
        
        # Create a map of unavailable slots for the form
        unavailable_slots = {}
        for unavail in individual_unavailabilities:
            day = unavail.day_of_week
            if day not in unavailable_slots:
                unavailable_slots[day] = []
            unavailable_slots[day].append(unavail.time_slot.pk)
        
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
            'time_slots': time_slots,
            'unavailable_slots': unavailable_slots,
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
    context = _prepare_lesson_context(record.lesson_session)
    return render(request, 'scheduler/_lesson_detail.html', context)

@login_required
@require_POST
def save_reason(request, pk, reason_code):
    record = get_object_or_404(AttendanceRecord, pk=pk)
    if reason_code in AttendanceRecord.AbsenceReason.values:
        record.reason_for_absence = reason_code
        record.save()
    lesson = record.lesson_session
    context = _prepare_lesson_context(lesson)
    return render(request, 'scheduler/_lesson_detail.html', context)

@login_required
def create_note_view(request, record_pk):
    record = get_object_or_404(AttendanceRecord, pk=record_pk)
    note, created = LessonNote.objects.get_or_create(attendance_record=record)
    lesson = record.lesson_session
    
    # The view now creates the form and passes it in the context
    form = LessonNoteForm(instance=note)
    
    context = _prepare_lesson_context(lesson, editing_note_id=note.id)
    context['note_form'] = form # Add the form to the context
    
    return render(request, 'scheduler/_lesson_detail.html', context)

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
    # This view doesn't need to pass the form, so it's fine as is,
    # but the logic is now consistent with edit_lesson_note
    context = _prepare_lesson_context(note.attendance_record.lesson_session)
    return render(request, 'scheduler/_lesson_detail.html', context)

@login_required
def edit_lesson_note(request, pk):
    note = get_object_or_404(LessonNote, pk=pk)
    lesson = note.attendance_record.lesson_session
    
    if request.method == 'POST':
        form = LessonNoteForm(request.POST, instance=note)
        if form.is_valid():
            form.save()
            # After saving, we just show the view state, no form needed
            context = _prepare_lesson_context(lesson)
            return render(request, 'scheduler/_lesson_detail.html', context)
    else:
        # For a GET request, create the form here
        form = LessonNoteForm(instance=note)

    context = _prepare_lesson_context(lesson, editing_note_id=note.id)
    context['note_form'] = form # Add the form to the context
    
    return render(request, 'scheduler/_lesson_detail.html', context)
