from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.db.models import Exists, OuterRef, Count, Q, F, Avg, Max, Min, Case, When, Value, FloatField
from django.db import transaction
from datetime import date, timedelta
from functools import wraps

def head_coach_required(view_func):
    """Decorator to restrict access to head coaches and staff only"""
    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        if not (hasattr(request.user, 'coach') and request.user.coach.is_head_coach) and not request.user.is_staff:
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped_view

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
    
    # Always provide editing_note_id (even if None) to prevent template errors
    context = {
        'lesson': lesson, 
        'editing_note_id': editing_note_id
    }
    
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

                # Mark affected students as absent in affected lessons (ONLY if no manual override exists)
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
                                # Map event types to appropriate absence reasons
                                reason_mapping = {
                                    'PUBLIC_HOLIDAY': 'CLASS_EVENT',
                                    'PUPIL_FREE_DAY': 'CLASS_EVENT', 
                                    'CAMP': 'CLASS_EVENT',
                                    'EXCURSION': 'CLASS_EVENT',
                                    'INDIVIDUAL': 'OTHER',
                                    'COACH_AWAY': None,  # Will be handled dynamically below
                                    'CUSTOM': 'CLASS_EVENT'
                                }
                                
                                # Handle Coach Away events dynamically based on the specific reason
                                if event.event_type == 'COACH_AWAY':
                                    # Extract the specific coach absence reason from the event reason field
                                    if 'Coach Sick' in event.reason:
                                        absence_reason = 'COACH_SICK'
                                    elif 'Coach at Tournament' in event.reason:
                                        absence_reason = 'COACH_TOURNAMENT'
                                    else:
                                        # Fallback for other coach-related reasons
                                        absence_reason = 'COACH_SICK'  # Default to COACH_SICK
                                else:
                                    absence_reason = reason_mapping.get(event.event_type, 'CLASS_EVENT')
                                
                                # FIXED: Only create absence record if no record exists (respect manual overrides)
                                existing_record = AttendanceRecord.objects.filter(
                                    lesson_session=lesson,
                                    enrollment=enrollment
                                ).first()
                                
                                if not existing_record:
                                    # No record exists - create new absent record due to event
                                    attendance_record = AttendanceRecord.objects.create(
                                        lesson_session=lesson,
                                        enrollment=enrollment,
                                        status='ABSENT',
                                        reason_for_absence=absence_reason
                                    )
                                    
                                    # Create a lesson note with the specific event reason for better tracking
                                    LessonNote.objects.create(
                                        attendance_record=attendance_record,
                                        coach_comments=f"Absent due to: {event.reason} ({event.name})"
                                    )
                                elif existing_record.status == 'PENDING':
                                    # Record exists but is still PENDING - update it to absent due to event
                                    existing_record.status = 'ABSENT'
                                    existing_record.reason_for_absence = absence_reason
                                    existing_record.save()
                                    
                                    # Add note if none exists
                                    if not hasattr(existing_record, 'lessonnote'):
                                        LessonNote.objects.create(
                                            attendance_record=existing_record,
                                            coach_comments=f"Absent due to: {event.reason} ({event.name})"
                                        )
                                # If record exists with PRESENT, ABSENT, etc., leave it unchanged (manual override)
                                
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
        
        # Add time slots for the "Add Extra Lesson" form
        context['time_slots'] = TimeSlot.objects.all().order_by('start_time')
        
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
        # Enhanced POST logic to handle both FILL_IN and PENDING record removal
        if 'add_enrollment_pk' in request.POST:
            enrollment_to_add = get_object_or_404(Enrollment, pk=request.POST.get('add_enrollment_pk'))
            AttendanceRecord.objects.update_or_create(
                lesson_session=lesson,
                enrollment=enrollment_to_add,
                defaults={'status': 'FILL_IN'}
            )
        elif 'remove_record_pk' in request.POST:
            record_to_remove = get_object_or_404(AttendanceRecord, pk=request.POST.get('remove_record_pk'))
            # Allow removal of both FILL_IN and PENDING records
            if record_to_remove.status in ['FILL_IN', 'PENDING']:
                record_to_remove.delete()
        elif 'remove_pending_pk' in request.POST:
            # Specific handler for removing PENDING fill-in records
            pending_record = get_object_or_404(AttendanceRecord, pk=request.POST.get('remove_pending_pk'))
            if pending_record.status == 'PENDING':
                pending_record.delete()
        return redirect('manage-lesson', lesson_pk=lesson.pk)

    # --- Prepare a SINGLE master list of candidates ---
    
    # 1. Get students already in this specific lesson
    present_student_ids = lesson.attendancerecord_set.all().values_list('enrollment__student_id', flat=True)

    # 2. Get all enrollments for the term, excluding those already in the lesson
    all_candidates = Enrollment.objects.filter(term=term, is_active=True) \
        .exclude(student_id__in=present_student_ids) \
        .select_related('student__school_class') \
        .annotate(
            actual_lessons=Count('attendancerecord', filter=Q(attendancerecord__status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']))
        )

    # 3. NEW: Find coach-absent students (high priority candidates)
    coach_absent_reasons = ['COACH_TOURNAMENT', 'COACH_SICK', 'SPECIALIST_CLASS']
    coach_absent_records = AttendanceRecord.objects.filter(
        lesson_session__lesson_date=lesson.lesson_date,
        status='ABSENT',
        reason_for_absence__in=coach_absent_reasons
    ).select_related(
        'enrollment__student__school_class',
        'lesson_session__scheduled_group__time_slot',
        'lesson_session__scheduled_group__coach__user'
    ).exclude(enrollment__student_id__in=present_student_ids)

    # Build a map of coach-absent students with their context
    coach_absent_map = {}
    for record in coach_absent_records:
        student_id = record.enrollment.student.id
        
        # Check if this coach-absent student can attend THIS lesson (no conflicts)
        conflict_info = record.enrollment.student.has_scheduling_conflict(
            lesson_day := lesson.lesson_date.weekday(),
            lesson_timeslot := lesson.scheduled_group.time_slot
        )
        
        # Only include if they don't have a scheduling conflict with THIS lesson
        if not conflict_info['has_conflict']:
            coach_absent_map[student_id] = {
                'reason': record.get_reason_for_absence_display(),
                'original_lesson_info': f"{record.lesson_session.scheduled_group.name}",
                'original_coach': str(record.lesson_session.scheduled_group.coach) if record.lesson_session.scheduled_group.coach else 'No Coach',
                'original_time_slot': str(record.lesson_session.scheduled_group.time_slot),
                'is_coach_absent': True
            }

    # 4. Determine regular availability (students with attendance records on this date)
    busy_student_ids = AttendanceRecord.objects.filter(lesson_session__lesson_date=lesson.lesson_date).values_list('enrollment__student_id', flat=True)
    
    # FIXED: Also exclude students who are members of groups with lessons on this date
    # This prevents double-booking when using extra lessons and "Find Slots" feature
    group_member_student_ids = Student.objects.filter(
        enrollment__scheduledgroup__lessonsession__lesson_date=lesson.lesson_date,
        enrollment__term=term
    ).values_list('id', flat=True)
    
    # Combine both types of busy students
    all_busy_student_ids = set(list(busy_student_ids) + list(group_member_student_ids))
    
    lesson_day = lesson.lesson_date.weekday()
    lesson_timeslot = lesson.scheduled_group.time_slot
    unavailable_student_ids = ScheduledUnavailability.objects.filter(day_of_week=lesson_day, time_slot=lesson_timeslot).values_list('students__id', flat=True)
    unavailable_class_student_ids = Student.objects.filter(school_class__scheduledunavailability__day_of_week=lesson_day, school_class__scheduledunavailability__time_slot=lesson_timeslot).values_list('id', flat=True)
    
    # FIXED: Also exclude students blocked by OneOffEvents on this specific date
    event_blocked_student_ids = []
    one_off_events = OneOffEvent.objects.filter(event_date=lesson.lesson_date)
    
    for event in one_off_events:
        # Check if event affects this time slot (or all time slots if none specified)
        event_affects_timeslot = False
        if event.time_slots.exists():
            event_affects_timeslot = event.time_slots.filter(id=lesson_timeslot.id).exists()
        else:
            event_affects_timeslot = True  # Event affects all time slots
        
        if event_affects_timeslot:
            # Get students directly affected by this event
            event_blocked_student_ids.extend(event.students.values_list('id', flat=True))
            
            # Get students affected by class-based events
            for school_class in event.school_classes.all():
                event_blocked_student_ids.extend(school_class.student_set.values_list('id', flat=True))
    
    non_suggested_ids = set(list(all_busy_student_ids) + list(unavailable_student_ids) + list(unavailable_class_student_ids) + list(event_blocked_student_ids))

    # 5. Process each candidate and add calculated fields
    candidates_list = []
    for enrollment in all_candidates:
        # Check if this is a coach-absent student (high priority)
        student_id = enrollment.student.id
        is_coach_absent = student_id in coach_absent_map
        
        if is_coach_absent:
            # High priority coach-absent candidate
            enrollment.is_suggested = True  # Always suggested
            enrollment.is_coach_absent = True
            enrollment.coach_absent_reason = coach_absent_map[student_id]['reason']
            enrollment.original_lesson_info = coach_absent_map[student_id]['original_lesson_info']
            enrollment.original_coach = coach_absent_map[student_id]['original_coach']
            enrollment.original_time_slot = coach_absent_map[student_id]['original_time_slot']
            enrollment.priority_score = 1000  # Very high priority
        else:
            # Regular availability check
            enrollment.is_suggested = enrollment.student_id not in non_suggested_ids
            enrollment.is_coach_absent = False
            enrollment.coach_absent_reason = None
            enrollment.original_lesson_info = None
            enrollment.original_coach = None
            enrollment.original_time_slot = None
            enrollment.priority_score = 0
        
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

    # 6. Sort by priority (coach-absent first), then by deficit, then by fewest actual lessons
    candidates_list.sort(key=lambda e: (-e.priority_score, -e.effective_deficit, e.actual_lessons))

    # Get the list of students currently in the lesson for display
    # Separate PENDING records (problematic fill-ins) from active records
    all_records = lesson.attendancerecord_set.select_related(
        'enrollment__student__school_class'
    ).order_by('enrollment__student__last_name')
    
    current_records = []
    pending_fill_ins = []
    
    for record in all_records:
        if record.status == 'PENDING':
            # These are problematic fill-in records that need to be handled
            pending_fill_ins.append(record)
        else:
            # These are normal active records (FILL_IN, PRESENT, ABSENT, etc.)
            current_records.append(record)

    context = {
        'lesson': lesson,
        'current_records': current_records,
        'pending_fill_ins': pending_fill_ins,
        'all_candidates': candidates_list,
    }
    return render(request, 'scheduler/manage_lesson.html', context)
# --- HTMX Helper Views ---

@login_required
@require_POST
def mark_attendance(request, pk, status):
    record = get_object_or_404(AttendanceRecord, pk=pk)
    new_status = status.upper()
    
    # Special handling for FILL_IN students: when "undoing" FILL_IN, mark as ABSENT
    # This preserves the record and any notes while removing them from active roster
    if record.status == 'FILL_IN' and new_status == 'FILL_IN':
        # Fill-in student being "undone" - mark as ABSENT to preserve history
        record.status = 'ABSENT'
        # Set a specific reason to indicate this was a fill-in attempt
        record.reason_for_absence = 'OTHER'  # Could be expanded to have a specific fill-in reason
    elif record.status == new_status:
        # Standard toggle behavior: if clicking the same status, go back to PENDING
        record.status = 'PENDING'
    else:
        # Standard status change
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
@require_POST
def mark_fill_in_absent(request, pk):
    """Mark a fill-in student as absent (preserving the record for analytics)"""
    record = get_object_or_404(AttendanceRecord, pk=pk)
    
    # Only allow this action for FILL_IN students
    if record.status == 'FILL_IN':
        record.status = 'FILL_IN_ABSENT'
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
    """Enhanced API for head coaches to get all available slots across all days/coaches"""
    if not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request'})
    
    # HEAD COACH ONLY - Regular coaches don't have access to this feature
    if not (hasattr(request.user, 'coach') and request.user.coach.is_head_coach):
        return JsonResponse({'success': False, 'error': 'Access denied. Head coach privileges required.'})
    
    try:
        student = get_object_or_404(Student, pk=student_id)
        current_term = Term.get_active_term()
        
        if not current_term:
            return JsonResponse({'success': False, 'error': 'No active term found'})
        
        # ENHANCED FOR HEAD COACHES: Get all available slots across ALL coaches and days
        available_slots = []
        
        # Check all days of the week
        for day in range(5):  # Monday to Friday
            day_name = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'][day]
            
            # Get all time slots
            time_slots = TimeSlot.objects.all().order_by('start_time')
            
            for time_slot in time_slots:
                # Check if student is available at this specific day/time
                conflict_info = student.has_scheduling_conflict(day, time_slot)
                if conflict_info['has_conflict']:
                    continue  # Skip if student has a conflict
                
                # HEAD COACH ENHANCEMENT: Find ALL groups across ALL coaches at this day/time
                groups_at_time = ScheduledGroup.objects.filter(
                    term=current_term,
                    day_of_week=day,
                    time_slot=time_slot
                ).select_related('coach').prefetch_related('members').order_by('coach__user__first_name', 'name')
                
                # Add ALL groups at this time - head coach can move students anywhere
                for group in groups_at_time:
                    available_slots.append({
                        'group_id': group.id,
                        'group_name': group.name,
                        'day': day,
                        'day_name': day_name,
                        'time_slot': str(time_slot),
                        'coach_name': str(group.coach) if group.coach else 'No Coach',
                        'coach_id': group.coach.id if group.coach else None,
                        'current_size': group.get_current_size(),
                        'max_capacity': group.get_type_based_max_capacity(),
                        'has_space': group.has_space(),
                        'group_type': group.group_type,
                        'is_current_coach': group.coach == request.user.coach if group.coach else False
                    })
        
        # Get all coaches for the dropdown
        all_coaches = Coach.objects.select_related('user').filter(
            scheduledgroup__term=current_term
        ).distinct().order_by('user__first_name')
        
        coaches_list = []
        for coach in all_coaches:
            coaches_list.append({
                'id': coach.id,
                'name': str(coach),
                'is_current': coach.id == request.user.coach.id
            })
        
        return JsonResponse({
            'success': True,
            'student_name': f"{student.first_name} {student.last_name}",
            'available_slots': available_slots,
            'total_slots': len(available_slots),
            'is_head_coach': True,
            'current_coach_id': request.user.coach.id,
            'available_coaches': coaches_list
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def move_student_to_slot_api(request, student_id):
    """API to move a student to a specific slot"""
    import logging
    logger = logging.getLogger(__name__)
    
    # Debug logging
    logger.info(f"🔍 move_student_to_slot_api called for student {student_id}")
    logger.info(f"🔍 Request method: {request.method}")
    logger.info(f"🔍 User: {request.user}")
    logger.info(f"🔍 User authenticated: {request.user.is_authenticated}")
    logger.info(f"🔍 Has coach attr: {hasattr(request.user, 'coach')}")
    
    if hasattr(request.user, 'coach'):
        logger.info(f"🔍 Coach: {request.user.coach}")
        logger.info(f"🔍 Is head coach: {request.user.coach.is_head_coach}")
    
    if request.method != 'POST':
        logger.warning(f"❌ Wrong method: {request.method}")
        return JsonResponse({'success': False, 'error': 'POST request required'})
    
    if not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        logger.warning(f"❌ Not AJAX request")
        return JsonResponse({'success': False, 'error': 'Invalid request'})
    
    # HEAD COACH ONLY - Regular coaches don't have access to this feature
    if not (hasattr(request.user, 'coach') and request.user.coach.is_head_coach):
        logger.warning(f"❌ Permission denied for user {request.user}")
        return JsonResponse({'success': False, 'error': 'Access denied. Head coach privileges required.'})
    
    logger.info(f"✅ Permission checks passed")
    
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


def _can_student_join_group(student, group, student_enrollment_type, exclude_current_group=True):
    """Check if student can join a specific group"""
    # Check if group has space or if we allow overfilling
    if not group.has_space():
        # For now, allow overfilling - we'll show visual indicators
        pass
    
    # Check basic compatibility
    if not group.is_compatible_with_student(student, student_enrollment_type):
        return False
    
    # Check if student is already in this group (only exclude if requested)
    if exclude_current_group:
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
    """Advanced analytics dashboard with comprehensive filtering and reporting"""
    
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
        
        # Get filter parameters - including new ones
        filters = {
            'status_filter': request.GET.getlist('status_filter'),
            'absence_reason_filter': request.GET.getlist('absence_reason_filter'),
            'lesson_balance_filter': request.GET.get('lesson_balance_filter'),
            'student_status_filter': request.GET.get('student_status_filter'),
            'coach_filter': request.GET.get('coach_filter'),
            'year_level_filter': request.GET.getlist('year_level_filter'),
            'school_class_filter': request.GET.getlist('school_class_filter'),
            'skill_level_filter': request.GET.getlist('skill_level_filter'),
            'enrollment_type_filter': request.GET.getlist('enrollment_type_filter'),
            'day_filter': request.GET.getlist('day_filter'),
            'time_slot_filter': request.GET.getlist('time_slot_filter'),
            'attendance_rate_filter': request.GET.get('attendance_rate_filter'),
            'days_since_last_filter': request.GET.get('days_since_last_filter'),
        }
        
        # Handle export request
        if request.GET.get('export') == 'csv':
            return _export_analytics_csv(current_term, start_date, end_date, filters)
        
        # Get filtered student list for "Present Sick" and other filters
        filtered_students = None
        filtered_student_count = 0
        
        if any(filters.values()):
            filtered_students = _get_filtered_students(current_term, start_date, end_date, filters)
            filtered_student_count = len(filtered_students)
        
        # Student Progress Analytics (with filtering)
        try:
            student_analytics = _get_student_analytics(current_term, start_date, end_date, filters)
        except Exception as e:
            student_analytics = {
                'total_students': 0, 'students_behind': 0, 'students_on_track': 0,
                'students_ahead': 0, 'lesson_balances': [], 'skill_distribution': [],
                'avg_attendance_rate': 0, 'error': f'Student analytics error: {str(e)}'
            }
        
        # Coach Performance Metrics
        try:
            coach_analytics = _get_coach_analytics(current_term, start_date, end_date, filters)
        except Exception as e:
            coach_analytics = []
        
        # System Utilization Insights
        try:
            utilization_analytics = _get_utilization_analytics(current_term, start_date, end_date, filters)
        except Exception as e:
            utilization_analytics = {
                'time_slot_utilization': [], 'day_utilization': [],
                'group_type_distribution': [], 'capacity_stats': {
                    'underutilized': 0, 'optimal': 0, 'full': 0, 'total': 0
                }
            }
        
        # Attendance Pattern Analysis
        try:
            attendance_analytics = _get_attendance_analytics(current_term, start_date, end_date, filters)
        except Exception as e:
            attendance_analytics = {
                'total_records': 0, 'attendance_breakdown': [], 'absence_reasons': [],
                'weekly_trends': [], 'overall_attendance_rate': 0
            }
        
        # Get available coaches for filter dropdown
        available_coaches = Coach.objects.select_related('user').filter(
            scheduledgroup__term=current_term
        ).distinct().order_by('user__first_name')
        
        # Get available options for new filters
        available_year_levels = Student.objects.filter(
            enrollment__term=current_term
        ).values_list('year_level', flat=True).distinct().order_by('year_level')
        
        available_school_classes = SchoolClass.objects.filter(
            student__enrollment__term=current_term
        ).distinct().order_by('name')
        
        available_time_slots = TimeSlot.objects.all().order_by('start_time')
        
        # Get all students overview with sorting
        sort_by = request.GET.get('sort', 'lessons_owed')  # Default sort by lessons owed
        sort_order = request.GET.get('order', 'desc')  # desc = most owed first
        
        try:
            all_students_overview = _get_all_students_overview(current_term, sort_by, sort_order)
        except Exception as e:
            all_students_overview = []
        
        context = {
            'current_term': current_term,
            'start_date': start_date,
            'end_date': end_date,
            'student_analytics': student_analytics,
            'coach_analytics': coach_analytics,
            'utilization_analytics': utilization_analytics,
            'attendance_analytics': attendance_analytics,
            'available_coaches': available_coaches,
            'available_year_levels': available_year_levels,
            'available_school_classes': available_school_classes,
            'available_time_slots': available_time_slots,
            'filtered_students': filtered_students,
            'filtered_student_count': filtered_student_count,
            'active_filters': filters,
            'all_students_overview': all_students_overview,
            'sort_by': sort_by,
            'sort_order': sort_order,
        }
        
        return render(request, 'scheduler/analytics_dashboard.html', context)
        
    except Exception as e:
        # Catch-all error handler
        return render(request, 'scheduler/analytics_dashboard.html', {
            'error': f'Analytics dashboard error: {str(e)}. Please contact an administrator.'
        })


def _get_filtered_students(term, start_date, end_date, filters):
    """Get filtered list of students based on filter criteria - Enhanced with new filters"""
    
    # Start with base query
    base_query = AttendanceRecord.objects.filter(
        enrollment__term=term,
        lesson_session__lesson_date__range=[start_date, end_date]
    ).select_related('enrollment__student__school_class', 'lesson_session__scheduled_group__coach__user', 'lesson_session__scheduled_group__time_slot')
    
    # Apply status filters
    if filters.get('status_filter'):
        base_query = base_query.filter(status__in=filters['status_filter'])
    
    # Apply absence reason filters
    if filters.get('absence_reason_filter'):
        base_query = base_query.filter(reason_for_absence__in=filters['absence_reason_filter'])
    
    # Apply coach filter
    if filters.get('coach_filter'):
        base_query = base_query.filter(lesson_session__scheduled_group__coach_id=filters['coach_filter'])
    
    # Apply student status filter
    if filters.get('student_status_filter'):
        if filters['student_status_filter'] == 'active':
            base_query = base_query.filter(enrollment__is_active=True)
        elif filters['student_status_filter'] == 'inactive':
            base_query = base_query.filter(enrollment__is_active=False)
    
    # Apply NEW demographic filters
    if filters.get('year_level_filter'):
        year_levels = [int(y) for y in filters['year_level_filter'] if y.isdigit()]
        if year_levels:
            base_query = base_query.filter(enrollment__student__year_level__in=year_levels)
    
    if filters.get('school_class_filter'):
        class_ids = [int(c) for c in filters['school_class_filter'] if c.isdigit()]
        if class_ids:
            base_query = base_query.filter(enrollment__student__school_class_id__in=class_ids)
    
    if filters.get('skill_level_filter'):
        base_query = base_query.filter(enrollment__student__skill_level__in=filters['skill_level_filter'])
    
    if filters.get('enrollment_type_filter'):
        base_query = base_query.filter(enrollment__enrollment_type__in=filters['enrollment_type_filter'])
    
    # Apply NEW scheduling filters
    if filters.get('day_filter'):
        days = [int(d) for d in filters['day_filter'] if d.isdigit()]
        if days:
            base_query = base_query.filter(lesson_session__scheduled_group__day_of_week__in=days)
    
    if filters.get('time_slot_filter'):
        time_slot_ids = [int(t) for t in filters['time_slot_filter'] if t.isdigit()]
        if time_slot_ids:
            base_query = base_query.filter(lesson_session__scheduled_group__time_slot_id__in=time_slot_ids)
    
    # Get unique students from filtered records
    student_records = []
    seen_students = set()
    
    for record in base_query:
        student_key = (record.enrollment.student.id, record.enrollment.student.first_name, record.enrollment.student.last_name)
        if student_key not in seen_students:
            seen_students.add(student_key)
            
            # Apply lesson balance filter if specified
            if filters.get('lesson_balance_filter'):
                balance = record.enrollment.get_lesson_balance()
                
                if filters['lesson_balance_filter'] == 'behind' and balance <= 0:
                    continue
                elif filters['lesson_balance_filter'] == 'very_behind' and balance < 3:
                    continue
                elif filters['lesson_balance_filter'] == 'on_track' and (balance < -1 or balance > 2):
                    continue
                elif filters['lesson_balance_filter'] == 'ahead' and balance >= 0:
                    continue
            
            # Apply attendance rate filter if specified
            if filters.get('attendance_rate_filter'):
                # Calculate attendance rate
                total_lessons = record.enrollment.attendancerecord_set.count()
                attended_lessons = record.enrollment.attendancerecord_set.filter(
                    status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
                ).count()
                
                attendance_rate = (attended_lessons / total_lessons * 100) if total_lessons > 0 else 0
                
                if filters['attendance_rate_filter'] == 'high' and attendance_rate < 90:
                    continue
                elif filters['attendance_rate_filter'] == 'good' and not (75 <= attendance_rate < 90):
                    continue
                elif filters['attendance_rate_filter'] == 'average' and not (50 <= attendance_rate < 75):
                    continue
                elif filters['attendance_rate_filter'] == 'low' and attendance_rate >= 50:
                    continue
            
            # Apply days since last lesson filter if specified
            if filters.get('days_since_last_filter'):
                # Get most recent attendance record for this enrollment
                latest_record = AttendanceRecord.objects.filter(
                    enrollment=record.enrollment
                ).select_related('lesson_session').order_by('-lesson_session__lesson_date').first()
                
                if latest_record:
                    days_since_last = (date.today() - latest_record.lesson_session.lesson_date).days
                    
                    if filters['days_since_last_filter'] == 'recent' and days_since_last > 7:
                        continue
                    elif filters['days_since_last_filter'] == 'moderate' and not (8 <= days_since_last <= 14):
                        continue
                    elif filters['days_since_last_filter'] == 'overdue' and days_since_last < 15:
                        continue
                    elif filters['days_since_last_filter'] == 'never' and latest_record:
                        continue
                else:
                    # No lessons yet - only include if filter is 'never'
                    if filters['days_since_last_filter'] != 'never':
                        continue
            
            student_records.append({
                'student': record.enrollment.student,
                'enrollment': record.enrollment,
                'record': record,
                'lesson_balance': record.enrollment.get_lesson_balance()
            })
    
    return student_records


def _export_analytics_csv(term, start_date, end_date, filters):
    """Export filtered analytics data as CSV"""
    import csv
    from django.http import HttpResponse
    
    # Get filtered students
    filtered_students = _get_filtered_students(term, start_date, end_date, filters)
    
    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="analytics_export_{start_date}_{end_date}.csv"'
    
    writer = csv.writer(response)
    
    # Write header
    writer.writerow([
        'Student Name', 'Skill Level', 'Year Level', 'School Class',
        'Enrollment Type', 'Status', 'Lesson Balance', 'Target Lessons',
        'Attended Lessons', 'Coach', 'Last Attendance Status', 'Last Lesson Date'
    ])
    
    # Write data
    for student_data in filtered_students:
        student = student_data['student']
        enrollment = student_data['enrollment']
        record = student_data['record']
        
        writer.writerow([
            f"{student.first_name} {student.last_name}",
            student.get_skill_level_display(),
            student.year_level,
            student.school_class.name if student.school_class else 'N/A',
            enrollment.get_enrollment_type_display(),
            'Active' if enrollment.is_active else 'Inactive',
            enrollment.get_lesson_balance(),
            enrollment.adjusted_target,
            enrollment.attendancerecord_set.filter(
                status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
            ).count(),
            record.lesson_session.scheduled_group.coach if record.lesson_session.scheduled_group.coach else 'N/A',
            record.get_status_display(),
            record.lesson_session.lesson_date
        ])
    
    return response


def _get_student_analytics(term, start_date, end_date, filters=None):
    """Calculate student progress analytics with optional filtering"""
    
    if filters is None:
        filters = {}
    
    # Get all enrollments for the term
    enrollments = Enrollment.objects.filter(term=term, is_active=True).select_related('student').annotate(
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


def _get_coach_analytics(term, start_date, end_date, filters=None):
    """Calculate coach performance metrics with optional filtering"""
    
    if filters is None:
        filters = {}
    
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


def _get_utilization_analytics(term, start_date, end_date, filters=None):
    """Calculate system utilization insights with optional filtering"""
    
    if filters is None:
        filters = {}
    
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


def _get_attendance_analytics(term, start_date, end_date, filters=None):
    """Calculate attendance pattern analysis"""
    
    if filters is None:
        filters = {}
    
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

def _get_all_students_overview(term, sort_by='lessons_owed', sort_order='desc'):
    """Get comprehensive overview of all students with sorting options"""
    
    # Get all enrollments for the term with related data
    enrollments = Enrollment.objects.filter(
        term=term, 
        is_active=True
    ).select_related(
        'student__school_class'
    ).annotate(
        attended_lessons=Count('attendancerecord', filter=Q(
            attendancerecord__status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
        )),
        total_lessons=Count('attendancerecord'),
        absent_lessons=Count('attendancerecord', filter=Q(
            attendancerecord__status='ABSENT'
        )),
        sick_present_lessons=Count('attendancerecord', filter=Q(
            attendancerecord__status='SICK_PRESENT'
        ))
    )
    
    # Process each enrollment and add calculated fields
    students_overview = []
    
    for enrollment in enrollments:
        student = enrollment.student
        lesson_balance = enrollment.get_lesson_balance()
        
        # Get current coach and group info
        current_groups = enrollment.scheduledgroup_set.filter(term=term)
        coach_names = []
        group_types = set()
        group_names = []
        
        for group in current_groups:
            if group.coach:
                coach_name = f"{group.coach.user.first_name} {group.coach.user.last_name}"
                if coach_name not in coach_names:
                    coach_names.append(coach_name)
            group_types.add(group.get_group_type_display())
            group_names.append(group.name)
        
        # Get most recent attendance record for last status
        latest_record = AttendanceRecord.objects.filter(
            enrollment=enrollment
        ).select_related('lesson_session').order_by('-lesson_session__lesson_date').first()
        
        # Calculate days since last lesson
        days_since_last = None
        last_lesson_date = None
        last_status = 'No lessons yet'
        
        if latest_record:
            last_lesson_date = latest_record.lesson_session.lesson_date
            days_since_last = (date.today() - last_lesson_date).days
            last_status = latest_record.get_status_display()
        
        # Determine balance category and color
        if lesson_balance >= 3:
            balance_category = 'Very Behind'
            balance_color = 'danger'
        elif lesson_balance > 0:
            balance_category = 'Behind'
            balance_color = 'warning'
        elif lesson_balance < -2:
            balance_category = 'Ahead'
            balance_color = 'info'
        else:
            balance_category = 'On Track'
            balance_color = 'success'
        
        student_data = {
            'student': student,
            'enrollment': enrollment,
            'student_name': f"{student.first_name} {student.last_name}",
            'lessons_owed': lesson_balance,
            'balance_category': balance_category,
            'balance_color': balance_color,
            'attended_lessons': enrollment.attended_lessons,
            'target_lessons': enrollment.adjusted_target,
            'total_lessons': enrollment.total_lessons,
            'absent_lessons': enrollment.absent_lessons,
            'sick_present_lessons': enrollment.sick_present_lessons,
            'skill_level': student.get_skill_level_display(),
            'year_level': student.year_level,
            'school_class': student.school_class.name if student.school_class else 'N/A',
            'enrollment_type': enrollment.get_enrollment_type_display(),
            'coaches': ', '.join(coach_names) if coach_names else 'No Coach',
            'group_types': ', '.join(group_types) if group_types else 'No Groups',
            'group_names': ', '.join(group_names) if group_names else 'No Groups',
            'last_lesson_date': last_lesson_date,
            'days_since_last': days_since_last,
            'last_status': last_status,
            'attendance_rate': round((enrollment.attended_lessons / enrollment.total_lessons * 100) if enrollment.total_lessons > 0 else 0, 1)
        }
        
        students_overview.append(student_data)
    
    # Apply sorting
    reverse_sort = sort_order == 'desc'
    
    if sort_by == 'lessons_owed':
        students_overview.sort(key=lambda x: x['lessons_owed'], reverse=reverse_sort)
    elif sort_by == 'student_name':
        students_overview.sort(key=lambda x: x['student_name'], reverse=reverse_sort)
    elif sort_by == 'coach':
        students_overview.sort(key=lambda x: x['coaches'], reverse=reverse_sort)
    elif sort_by == 'group_type':
        students_overview.sort(key=lambda x: x['group_types'], reverse=reverse_sort)
    elif sort_by == 'skill_level':
        students_overview.sort(key=lambda x: x['skill_level'], reverse=reverse_sort)
    elif sort_by == 'school_class':
        students_overview.sort(key=lambda x: x['school_class'], reverse=reverse_sort)
    elif sort_by == 'attendance_rate':
        students_overview.sort(key=lambda x: x['attendance_rate'], reverse=reverse_sort)
    elif sort_by == 'days_since_last':
        students_overview.sort(key=lambda x: x['days_since_last'] or 9999, reverse=reverse_sort)
    elif sort_by == 'last_status':
        students_overview.sort(key=lambda x: x['last_status'], reverse=reverse_sort)
    else:
        # Default: sort by lessons owed (most behind first)
        students_overview.sort(key=lambda x: x['lessons_owed'], reverse=True)
    
    return students_overview


@login_required
@require_POST
def change_attendance_status_analytics(request):
    """AJAX endpoint to change attendance status from analytics dashboard"""
    
    # Ensure this is only accessible to head coaches
    if not (hasattr(request.user, 'coach') and request.user.coach.is_head_coach) and not request.user.is_staff:
        return JsonResponse({'success': False, 'error': 'Permission denied'})
    
    try:
        import json
        data = json.loads(request.body)
        record_id = data.get('record_id')
        new_status = data.get('new_status')
        
        if not record_id or not new_status:
            return JsonResponse({'success': False, 'error': 'Missing required parameters'})
        
        # Get the attendance record
        record = get_object_or_404(AttendanceRecord, id=record_id)
        
        # Validate the new status
        valid_statuses = [choice[0] for choice in AttendanceRecord.StatusChoices.choices]
        if new_status not in valid_statuses:
            return JsonResponse({'success': False, 'error': 'Invalid status'})
        
        # Update the record
        old_status = record.status
        record.status = new_status
        
        # Clear absence reason if not absent
        if new_status != 'ABSENT':
            record.reason_for_absence = None
        
        record.save()
        
        # Get student info for response
        student = record.enrollment.student
        student_name = f"{student.first_name} {student.last_name}"
        
        return JsonResponse({
            'success': True,
            'message': f'Successfully changed {student_name} from {old_status} to {new_status}',
            'student_name': student_name,
            'old_status': old_status,
            'new_status': new_status,
            'new_status_display': record.get_status_display()
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def add_extra_lesson(request):
    """Simple view to add an extra lesson to any coach's schedule"""
    if request.method == 'POST':
        coach_id = request.POST.get('coach')
        time_slot_id = request.POST.get('time_slot')
        lesson_date = request.POST.get('date')
        
        try:
            coach = get_object_or_404(Coach, pk=coach_id)
            time_slot = get_object_or_404(TimeSlot, pk=time_slot_id)
            lesson_date = date.fromisoformat(lesson_date)
            
            # Create a simple scheduled group for this extra lesson
            # We'll use a naming convention to identify these as extra lessons
            # Include day name to make each day's extra lesson unique
            day_name = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'][lesson_date.weekday()]
            group_name = f"Extra Lesson - {coach} - {day_name} {time_slot}"
            
            # Get or create a scheduled group for this coach/time/day combination
            current_term = Term.get_active_term()
            if not current_term:
                messages.error(request, "No active term found.")
                return redirect('dashboard')
            
            # FIXED: Include day_of_week in get_or_create to ensure each day gets its own group
            scheduled_group, created = ScheduledGroup.objects.get_or_create(
                name=group_name,
                coach=coach,
                term=current_term,
                time_slot=time_slot,
                day_of_week=lesson_date.weekday(),  # This is the key fix!
                defaults={
                    'group_type': 'GROUP',
                    'target_skill_level': 'B',
                    'max_capacity': 4
                }
            )
            
            # Create the lesson session
            lesson_session, lesson_created = LessonSession.objects.get_or_create(
                scheduled_group=scheduled_group,
                lesson_date=lesson_date,
                defaults={'status': 'SCHEDULED'}
            )
            
            if lesson_created:
                messages.success(request, f"Extra lesson added for {coach} on {lesson_date.strftime('%B %d, %Y')} at {time_slot}. You can now add students using the fill-in system.")
            else:
                messages.info(request, f"Lesson already exists for {coach} on {lesson_date.strftime('%B %d, %Y')} at {time_slot}.")
            
            # Redirect back to the dashboard for that date
            return redirect(f"/?date={lesson_date.isoformat()}")
            
        except Exception as e:
            messages.error(request, f"Error creating lesson: {str(e)}")
            return redirect('dashboard')
    
    # For GET requests, redirect to dashboard
    return redirect('dashboard')


# =============================================================================
# CHESS TRAINING SYSTEM VIEWS
# =============================================================================

@login_required
def student_training_view(request, record_pk):
    """Display and manage chess training for a specific student in a lesson"""
    from datetime import date
    from .models import CurriculumLevel, CurriculumTopic, StudentProgress, RecapSchedule
    
    # Get the attendance record (links student to specific lesson)
    record = get_object_or_404(
        AttendanceRecord.objects.select_related(
            'enrollment__student__school_class',
            'enrollment__term',
            'lesson_session__scheduled_group__coach__user',
            'lesson_session__scheduled_group__time_slot'
        ),
        pk=record_pk
    )
    
    student = record.enrollment.student
    lesson = record.lesson_session
    
    # Initialize student progress if this is their first time in training
    _initialize_student_progress(student)
    
    # Get current curriculum level based on completed topics
    current_level, current_elo = _calculate_student_level_and_elo(student)
    
    # Get current topic (next topic to work on)
    current_topic = _get_current_topic_for_student(student, current_level)
    
    # Get topics due for recap
    recap_topics = _get_topics_due_for_recap(student, lesson)
    
    # Get recently mastered topics for display
    recent_progress = StudentProgress.objects.filter(
        student=student,
        status=StudentProgress.Status.MASTERED
    ).select_related('topic__level').order_by('-mastery_date')[:5]
    
    # Get progress summary
    progress_summary = _get_student_progress_summary(student)
    
    context = {
        'record': record,
        'student': student,
        'lesson': lesson,
        'current_level': current_level,
        'current_elo': current_elo,
        'current_topic': current_topic,
        'recap_topics': recap_topics,
        'recent_progress': recent_progress,
        'progress_summary': progress_summary,
        # Navigation context
        'lesson_date': lesson.lesson_date.isoformat(),
        'lesson_id': lesson.pk,
    }
    
    return render(request, 'scheduler/student_training.html', context)


@login_required
@require_POST
def mark_training_progress(request, record_pk):
    """Mark progress on a training topic and handle ELO updates - ENHANCED with debugging"""
    import logging
    from django.contrib import messages
    from datetime import date
    from .models import StudentProgress, RecapSchedule, CurriculumTopic
    
    logger = logging.getLogger(__name__)
    logger.info(f"🎯 TRAINING DEBUG: mark_training_progress called for record {record_pk}")
    
    record = get_object_or_404(AttendanceRecord, pk=record_pk)
    student = record.enrollment.student
    lesson = record.lesson_session
    
    logger.info(f"🎯 Student: {student.first_name} {student.last_name} (ID: {student.id})")
    
    topic_id = request.POST.get('topic_id')
    result = request.POST.get('result')  # 'pass', 'review', 'not_ready'
    notes = request.POST.get('notes', '').strip()
    is_recap = request.POST.get('is_recap') == 'true'
    
    logger.info(f"🎯 Form data: topic_id={topic_id}, result={result}, is_recap={is_recap}")
    logger.info(f"🎯 Notes: {notes[:100]}..." if len(notes) > 100 else f"🎯 Notes: {notes}")
    
    if not topic_id or not result:
        logger.error(f"❌ Missing required info: topic_id={topic_id}, result={result}")
        messages.error(request, "Missing required information.")
        return redirect('student_training', record_pk=record_pk)
    
    try:
        # Get the topic
        try:
            topic = CurriculumTopic.objects.get(pk=topic_id)
            logger.info(f"✅ Found topic: {topic.name} (Level: {topic.level.name})")
        except CurriculumTopic.DoesNotExist:
            logger.error(f"❌ Topic not found: {topic_id}")
            messages.error(request, f"Topic not found: {topic_id}")
            return redirect('student_training', record_pk=record_pk)
        
        # Get or create progress record
        progress, created = StudentProgress.objects.get_or_create(
            student=student,
            topic=topic,
            defaults={
                'status': StudentProgress.Status.NOT_STARTED,
                'attempts': 0,
                'coach_notes': ''
            }
        )
        
        logger.info(f"📊 Progress record: {'created new' if created else 'found existing'}")
        logger.info(f"📊 Previous status: {progress.status}, attempts: {progress.attempts}")
        
        # Update progress based on result
        progress.attempts += 1
        progress.last_attempted_date = date.today()
        progress.last_lesson_session = lesson
        
        # Add coach notes
        if notes:
            if progress.coach_notes:
                progress.coach_notes += f"\n[{date.today()}] {notes}"
            else:
                progress.coach_notes = f"[{date.today()}] {notes}"
            logger.info(f"📝 Added coach notes")
        
        # Handle different results
        if result == 'pass':
            progress.status = StudentProgress.Status.MASTERED
            progress.mastery_date = date.today()
            progress.pass_percentage = 100
            logger.info(f"🎉 Topic MASTERED: {topic.name} (+{topic.elo_points} ELO)")
            
            # Create recap schedule for spaced repetition (only for regular topics, not recaps)
            if not is_recap:
                try:
                    recap_schedule = RecapSchedule.create_for_progress(progress)
                    logger.info(f"📅 Created recap schedule for topic {topic.name}")
                except Exception as recap_error:
                    logger.error(f"❌ Error creating recap schedule: {recap_error}")
                    # Don't fail the whole operation for recap schedule issues
            
            messages.success(request, f"🎉 {student.first_name} has mastered '{topic.name}'! (+{topic.elo_points} ELO)")
            
        elif result == 'review':
            progress.status = StudentProgress.Status.NEEDS_REVIEW
            progress.pass_percentage = 75
            logger.info(f"📝 Topic needs review: {topic.name}")
            messages.info(request, f"📝 {topic.name} marked for review. {student.first_name} is making progress!")
            
        elif result == 'not_ready':
            progress.status = StudentProgress.Status.IN_PROGRESS
            progress.pass_percentage = 25
            logger.info(f"📚 Topic in progress: {topic.name}")
            messages.info(request, f"📚 {student.first_name} needs more practice with '{topic.name}'.")
        
        else:
            logger.error(f"❌ Invalid result value: {result}")
            messages.error(request, f"Invalid result value: {result}")
            return redirect('student_training', record_pk=record_pk)
        
        # Save the progress
        try:
            progress.save()
            logger.info(f"✅ Progress saved successfully")
            logger.info(f"📊 New status: {progress.status}, attempts: {progress.attempts}")
        except Exception as save_error:
            logger.error(f"❌ Error saving progress: {save_error}")
            messages.error(request, f"Error saving progress: {save_error}")
            return redirect('student_training', record_pk=record_pk)
        
        # Handle recap topic marking
        if is_recap:
            recap_result = 'PASS' if result == 'pass' else 'FAIL'
            logger.info(f"📅 Processing recap: {recap_result}")
            
            try:
                recap_schedule = RecapSchedule.objects.get(
                    progress__student=student,
                    progress__topic=topic
                )
                recap_schedule.mark_recap_completed(recap_result)
                logger.info(f"📅 Recap schedule updated")
                
                if recap_result == 'PASS':
                    messages.success(request, f"✅ Recap passed! Next recap in {recap_schedule.current_interval} lessons.")
                else:
                    messages.info(request, f"📖 Recap needs work. Schedule reset to help reinforce learning.")
                    
            except RecapSchedule.DoesNotExist:
                logger.warning(f"⚠️ No recap schedule found for topic {topic.name}")
                pass  # No recap schedule found
        
        # Debug: Check what topics are available now
        logger.info(f"🔍 Checking available topics after progress update...")
        _debug_student_topics(student)
        
    except Exception as e:
        logger.error(f"❌ Unexpected error in mark_training_progress: {str(e)}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        messages.error(request, f"Error updating progress: {str(e)}")
    
    logger.info(f"🔄 Redirecting back to student training page")
    return redirect('student_training', record_pk=record_pk)


def _debug_student_topics(student):
    """Debug helper to log student's current topic status"""
    import logging
    logger = logging.getLogger(__name__)
    
    from .models import StudentProgress, CurriculumLevel
    
    logger.info(f"📊 DEBUG - Student {student.first_name} topic status:")
    
    # Check all progress records
    all_progress = StudentProgress.objects.filter(student=student).select_related('topic__level')
    logger.info(f"📊 Total progress records: {all_progress.count()}")
    
    for progress in all_progress:
        logger.info(f"   - {progress.topic.name}: {progress.status} (Level: {progress.topic.level.name})")
    
    # Check available topics in foundation level
    try:
        foundation = CurriculumLevel.objects.get(name='FOUNDATION')
        foundation_topics = foundation.topics.filter(is_active=True).order_by('sort_order')
        logger.info(f"📊 Total foundation topics available: {foundation_topics.count()}")
        
        for topic in foundation_topics:
            try:
                progress = StudentProgress.objects.get(student=student, topic=topic)
                status = progress.status
            except StudentProgress.DoesNotExist:
                status = "NOT_STARTED (no record)"
            logger.info(f"   - {topic.name} (order: {topic.sort_order}): {status}")
            
    except CurriculumLevel.DoesNotExist:
        logger.error(f"❌ Foundation level not found!")


def _initialize_student_progress(student):
    """Initialize progress records for a student starting training"""
    from .models import CurriculumLevel, StudentProgress
    
    # Check if student already has progress records
    if StudentProgress.objects.filter(student=student).exists():
        return  # Already initialized
    
    # Get foundation level topics to initialize
    try:
        foundation_level = CurriculumLevel.objects.get(name='FOUNDATION')
        foundation_topics = foundation_level.topics.filter(is_active=True).order_by('sort_order')
        
        # Create NOT_STARTED progress records for foundation topics
        progress_records = []
        for topic in foundation_topics:
            progress_records.append(
                StudentProgress(
                    student=student,
                    topic=topic,
                    status=StudentProgress.Status.NOT_STARTED,
                    attempts=0,
                    coach_notes=f'Initialized for {student.first_name} on first training session'
                )
            )
        
        if progress_records:
            StudentProgress.objects.bulk_create(progress_records, ignore_conflicts=True)
            
    except CurriculumLevel.DoesNotExist:
        pass  # No foundation level found


def _calculate_student_level_and_elo(student):
    """Calculate student's current curriculum level and ELO based on mastered topics"""
    from .models import CurriculumLevel, StudentProgress
    
    # Get all mastered topics
    mastered_progress = StudentProgress.objects.filter(
        student=student,
        status=StudentProgress.Status.MASTERED
    ).select_related('topic__level')
    
    # Calculate total ELO points earned
    total_elo = sum(progress.topic.elo_points for progress in mastered_progress)
    current_elo = 400 + total_elo  # Base ELO of 400
    
    # Determine current level based on ELO
    try:
        current_level = CurriculumLevel.objects.filter(
            min_elo__lte=current_elo,
            max_elo__gte=current_elo
        ).first()
        
        if not current_level:
            # If ELO is above all levels, use highest level
            current_level = CurriculumLevel.objects.order_by('-max_elo').first()
        
        return current_level, current_elo
        
    except CurriculumLevel.DoesNotExist:
        # Fallback to foundation level
        foundation_level = CurriculumLevel.objects.filter(name='FOUNDATION').first()
        return foundation_level, current_elo


def _get_current_topic_for_student(student, current_level):
    """Get the next topic the student should work on - ENHANCED with debugging"""
    import logging
    from .models import StudentProgress, TopicPrerequisite, CurriculumLevel
    
    logger = logging.getLogger(__name__)
    logger.info(f"🔍 TOPIC FINDER: Finding current topic for {student.first_name}")
    
    if not current_level:
        logger.warning(f"⚠️ No current level provided")
        # Try to get the foundation level as fallback
        try:
            current_level = CurriculumLevel.objects.get(name='FOUNDATION')
            logger.info(f"📚 Using foundation level as fallback")
        except CurriculumLevel.DoesNotExist:
            logger.error(f"❌ No foundation level found")
            return None
    
    logger.info(f"📚 Current level: {current_level.name}")
    
    # Get all topics for current level, ordered by sort_order
    level_topics = current_level.topics.filter(is_active=True).order_by('sort_order')
    logger.info(f"📖 Found {level_topics.count()} topics in {current_level.name} level")
    
    for topic in level_topics:
        logger.info(f"   🔍 Checking topic: {topic.name} (order: {topic.sort_order})")
        
        # Check if student has already mastered this topic
        try:
            progress = StudentProgress.objects.get(student=student, topic=topic)
            logger.info(f"      📊 Found progress: {progress.status}")
            if progress.status == StudentProgress.Status.MASTERED:
                logger.info(f"      ✅ Topic already mastered, skipping")
                continue  # Skip mastered topics
            elif progress.status in [StudentProgress.Status.IN_PROGRESS, StudentProgress.Status.NEEDS_REVIEW]:
                logger.info(f"      🎯 Topic in progress/needs review - returning this topic")
                return topic  # Continue with in-progress topics
        except StudentProgress.DoesNotExist:
            logger.info(f"      📋 No progress record found - new topic")
        
        # Check if prerequisites are met
        prerequisites = TopicPrerequisite.objects.filter(required_for=topic)
        logger.info(f"      🔗 Found {prerequisites.count()} prerequisites")
        
        prerequisites_met = True
        
        for prereq in prerequisites:
            logger.info(f"         🔗 Checking prerequisite: {prereq.prerequisite.name}")
            try:
                prereq_progress = StudentProgress.objects.get(
                    student=student, 
                    topic=prereq.prerequisite
                )
                if prereq.is_strict and prereq_progress.status != StudentProgress.Status.MASTERED:
                    logger.info(f"         ❌ Strict prerequisite not mastered: {prereq.prerequisite.name} ({prereq_progress.status})")
                    prerequisites_met = False
                    break
                else:
                    logger.info(f"         ✅ Prerequisite met: {prereq.prerequisite.name}")
            except StudentProgress.DoesNotExist:
                if prereq.is_strict:
                    logger.info(f"         ❌ Strict prerequisite has no progress record: {prereq.prerequisite.name}")
                    prerequisites_met = False
                    break
                else:
                    logger.info(f"         ⚠️ Optional prerequisite has no progress record: {prereq.prerequisite.name}")
        
        if prerequisites_met:
            logger.info(f"      🎯 All prerequisites met - returning topic: {topic.name}")
            return topic
        else:
            logger.info(f"      ❌ Prerequisites not met for topic: {topic.name}")
    
    # If all topics in current level are mastered, get first topic from next level
    logger.info(f"📚 All topics in {current_level.name} completed/blocked, checking next level...")
    
    next_level = CurriculumLevel.objects.filter(
        sort_order__gt=current_level.sort_order
    ).order_by('sort_order').first()
    
    if next_level:
        logger.info(f"📚 Found next level: {next_level.name}")
        next_level_topic = next_level.topics.filter(is_active=True).order_by('sort_order').first()
        if next_level_topic:
            logger.info(f"🎯 Found first topic in next level: {next_level_topic.name}")
            return next_level_topic
        else:
            logger.warning(f"⚠️ No topics found in next level: {next_level.name}")
    else:
        logger.info(f"🏆 No next level found - student has completed all available levels!")
    
    logger.warning(f"❌ No current topic found for student {student.first_name}")
    return None


def _get_topics_due_for_recap(student, lesson):
    """Get topics that are due for spaced repetition recap"""
    from .models import RecapSchedule
    
    # Get recap schedules where it's time for the next recap
    recap_schedules = RecapSchedule.objects.filter(
        progress__student=student,
        next_recap_lesson__lte=_get_student_lesson_count(student)
    ).select_related('progress__topic').order_by('next_recap_lesson')
    
    return [schedule.progress.topic for schedule in recap_schedules[:3]]  # Limit to 3 recaps per lesson


def _get_student_lesson_count(student):
    """Get total number of lessons student has attended"""
    from .models import AttendanceRecord
    
    return AttendanceRecord.objects.filter(
        enrollment__student=student,
        status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT']
    ).count()


def _get_student_progress_summary(student):
    """Get summary of student's overall progress"""
    from .models import StudentProgress, CurriculumLevel
    
    # Get progress counts by status
    progress_counts = StudentProgress.objects.filter(student=student).values('status').annotate(
        count=Count('id')
    )
    
    status_summary = {status[0]: 0 for status in StudentProgress.Status.choices}
    for item in progress_counts:
        status_summary[item['status']] = item['count']
    
    # Get progress by level
    level_progress = {}
    for level in CurriculumLevel.objects.all().order_by('sort_order'):
        mastered_count = StudentProgress.objects.filter(
            student=student,
            topic__level=level,
            status=StudentProgress.Status.MASTERED
        ).count()
        
        total_count = level.topics.filter(is_active=True).count()
        
        if total_count > 0:
            level_progress[level.name] = {
                'mastered': mastered_count,
                'total': total_count,
                'percentage': round((mastered_count / total_count) * 100, 1)
            }
    
    return {
        'status_counts': status_summary,
        'level_progress': level_progress,
        'total_topics_available': sum(lp['total'] for lp in level_progress.values()),
        'total_mastered': status_summary.get(StudentProgress.Status.MASTERED, 0)
    }


@login_required
@require_POST
def bulk_advance_student(request, record_pk):
    """Bulk advance student through curriculum levels - Quick Level Advancement"""
    import logging
    from django.contrib import messages
    from datetime import date
    from .models import StudentProgress, CurriculumLevel, CurriculumTopic, RecapSchedule
    
    logger = logging.getLogger(__name__)
    logger.info(f"🚀 BULK ADVANCE: Starting bulk advancement for record {record_pk}")
    
    record = get_object_or_404(AttendanceRecord, pk=record_pk)
    student = record.enrollment.student
    lesson = record.lesson_session
    
    advancement_type = request.POST.get('advancement_type')
    reason = request.POST.get('reason', 'Bulk advancement by coach').strip()
    
    logger.info(f"🚀 Student: {student.first_name} {student.last_name}")
    logger.info(f"🚀 Advancement type: {advancement_type}")
    logger.info(f"🚀 Reason: {reason}")
    
    if not advancement_type:
        messages.error(request, "Please select an advancement type.")
        return redirect('student_training', record_pk=record_pk)
    
    try:
        with transaction.atomic():
            topics_to_advance = []
            levels_advanced = []
            total_elo_awarded = 0
            
            # Parse advancement type and get topics
            if advancement_type == 'foundation':
                # Complete Foundation Level (400-600 ELO)
                foundation_level = CurriculumLevel.objects.get(name='FOUNDATION')
                topics_to_advance = foundation_level.topics.filter(is_active=True)
                levels_advanced = ['Foundation']
                
            elif advancement_type == 'tactical':
                # Complete Tactical Level (600-800 ELO)
                tactical_level = CurriculumLevel.objects.get(name='TACTICAL')
                topics_to_advance = tactical_level.topics.filter(is_active=True)
                levels_advanced = ['Tactical Awareness']
                
            elif advancement_type == 'to_strategic':
                # Skip to Strategic Level (complete Foundation + Tactical)
                foundation = CurriculumLevel.objects.get(name='FOUNDATION')
                tactical = CurriculumLevel.objects.get(name='TACTICAL')
                foundation_topics = foundation.topics.filter(is_active=True)
                tactical_topics = tactical.topics.filter(is_active=True)
                topics_to_advance = list(foundation_topics) + list(tactical_topics)
                levels_advanced = ['Foundation', 'Tactical Awareness']
                
            elif advancement_type == 'to_advanced':
                # Skip to Advanced Level (complete Foundation + Tactical + Strategic)
                foundation = CurriculumLevel.objects.get(name='FOUNDATION')
                tactical = CurriculumLevel.objects.get(name='TACTICAL')
                strategic = CurriculumLevel.objects.get(name='STRATEGIC')
                topics_to_advance = (
                    list(foundation.topics.filter(is_active=True)) +
                    list(tactical.topics.filter(is_active=True)) +
                    list(strategic.topics.filter(is_active=True))
                )
                levels_advanced = ['Foundation', 'Tactical Awareness', 'Strategic Thinking']
                
            elif advancement_type.startswith('custom_elo_'):
                # Custom ELO advancement (e.g., custom_elo_800)
                target_elo = int(advancement_type.split('_')[-1])
                current_elo = _calculate_student_level_and_elo(student)[1]
                
                if target_elo <= current_elo:
                    messages.warning(request, f"Student already has {current_elo} ELO (target: {target_elo})")
                    return redirect('student_training', record_pk=record_pk)
                
                # Find all topics up to the target ELO
                topics_to_advance = []
                for level in CurriculumLevel.objects.filter(min_elo__lt=target_elo).order_by('sort_order'):
                    topics_to_advance.extend(level.topics.filter(is_active=True))
                    levels_advanced.append(level.get_name_display())
                
            else:
                messages.error(request, f"Invalid advancement type: {advancement_type}")
                return redirect('student_training', record_pk=record_pk)
            
            # Bulk create/update progress records
            logger.info(f"📚 Processing {len(topics_to_advance)} topics for advancement")
            
            topics_completed = 0
            topics_skipped = 0
            
            for topic in topics_to_advance:
                # Check if student already has progress on this topic
                progress, created = StudentProgress.objects.get_or_create(
                    student=student,
                    topic=topic,
                    defaults={
                        'status': StudentProgress.Status.MASTERED,
                        'mastery_date': date.today(),
                        'last_lesson_session': lesson,
                        'attempts': 1,
                        'pass_percentage': 100,
                        'coach_notes': f'[{date.today()}] Bulk advanced: {reason}'
                    }
                )
                
                if created or progress.status != StudentProgress.Status.MASTERED:
                    # Mark as mastered (skip if already mastered)
                    if not created:
                        progress.status = StudentProgress.Status.MASTERED
                        progress.mastery_date = date.today()
                        progress.last_lesson_session = lesson
                        progress.pass_percentage = 100
                        if progress.coach_notes:
                            progress.coach_notes += f"\n[{date.today()}] Bulk advanced: {reason}"
                        else:
                            progress.coach_notes = f"[{date.today()}] Bulk advanced: {reason}"
                        progress.save()
                    
                    # Award ELO points
                    total_elo_awarded += topic.elo_points
                    topics_completed += 1
                    
                    # Create recap schedule for spaced repetition
                    try:
                        RecapSchedule.create_for_progress(progress)
                        logger.info(f"📅 Created recap schedule for {topic.name}")
                    except Exception as recap_error:
                        logger.warning(f"⚠️ Could not create recap schedule for {topic.name}: {recap_error}")
                        
                else:
                    topics_skipped += 1
                    logger.info(f"⏭️ Skipping already mastered topic: {topic.name}")
            
            # Calculate new ELO
            _, new_elo = _calculate_student_level_and_elo(student)
            
            logger.info(f"✅ BULK ADVANCE COMPLETE:")
            logger.info(f"   - Topics completed: {topics_completed}")
            logger.info(f"   - Topics skipped: {topics_skipped}")
            logger.info(f"   - ELO awarded: {total_elo_awarded}")
            logger.info(f"   - New ELO: {new_elo}")
            
            # Success message
            levels_text = " & ".join(levels_advanced)
            messages.success(request, 
                f"🚀 Successfully advanced {student.first_name} through {levels_text}! "
                f"Completed {topics_completed} topics (+{total_elo_awarded} ELO). "
                f"New ELO: {new_elo}"
            )
            
            if topics_skipped > 0:
                messages.info(request, f"ℹ️ Skipped {topics_skipped} topics that were already mastered.")
            
    except CurriculumLevel.DoesNotExist as e:
        logger.error(f"❌ Curriculum level not found: {e}")
        messages.error(request, f"Curriculum level not found: {e}")
    except Exception as e:
        logger.error(f"❌ Error in bulk advancement: {str(e)}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        messages.error(request, f"Error during bulk advancement: {str(e)}")
    
    return redirect('student_training', record_pk=record_pk)
