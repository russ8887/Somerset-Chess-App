"""
Intelligent Slot Finder System for Somerset Chess Scheduler

This module contains the core algorithms for finding optimal lesson slots,
managing swap chains, and providing intelligent scheduling recommendations.
"""

from django.db.models import Q, Count, Prefetch
from django.db import transaction
from datetime import date, timedelta
import time
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from functools import lru_cache

from .models import (
    Student, Coach, ScheduledGroup, TimeSlot, Term, Enrollment,
    AttendanceRecord, ScheduledUnavailability, LessonSession
)


@dataclass
class SlotRecommendation:
    """Data class for slot recommendations"""
    group: ScheduledGroup
    score: int
    placement_type: str  # 'direct', 'swap', 'chain'
    swap_chain: Optional[List[Dict]] = None
    benefits: Dict[str, Any] = None
    conflicts: List[str] = None
    
    def __post_init__(self):
        if self.benefits is None:
            self.benefits = {}
        if self.conflicts is None:
            self.conflicts = []


class AvailabilityChecker:
    """Fast availability checking with caching"""
    
    def __init__(self):
        self._cache = {}
        self._cache_timeout = 300  # 5 minutes
        self._last_cache_clear = time.time()
    
    def _clear_cache_if_needed(self):
        """Clear cache if it's too old"""
        if time.time() - self._last_cache_clear > self._cache_timeout:
            self._cache.clear()
            self._last_cache_clear = time.time()
    
    def get_available_slots(self, student: Student) -> List[Tuple[int, TimeSlot]]:
        """
        Get all available time slots for a student.
        Returns list of (day_of_week, time_slot) tuples.
        Optimized with bulk queries to reduce database hits.
        """
        self._clear_cache_if_needed()
        
        cache_key = f"available_slots_{student.id}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        available_slots = []
        
        # Bulk fetch all time slots once
        time_slots = list(TimeSlot.objects.all())
        
        # Bulk fetch all unavailabilities for this student to reduce queries
        individual_unavailabilities = set(
            ScheduledUnavailability.objects.filter(students=student)
            .values_list('day_of_week', 'time_slot_id')
        )
        
        class_unavailabilities = set()
        if student.school_class:
            class_unavailabilities = set(
                ScheduledUnavailability.objects.filter(school_classes=student.school_class)
                .values_list('day_of_week', 'time_slot_id')
            )
        
        # Check availability for each day/time combination
        for day in range(5):  # Monday to Friday
            for time_slot in time_slots:
                # Quick check against pre-fetched unavailabilities
                has_individual_conflict = (day, time_slot.id) in individual_unavailabilities
                has_class_conflict = (day, time_slot.id) in class_unavailabilities
                
                if not has_individual_conflict and not has_class_conflict:
                    available_slots.append((day, time_slot))
        
        self._cache[cache_key] = available_slots
        return available_slots
    
    def is_student_available(self, student: Student, day: int, time_slot: TimeSlot) -> bool:
        """Quick check if student is available at specific time"""
        conflict_info = student.has_scheduling_conflict(day, time_slot)
        return not conflict_info['has_conflict']
    
    def get_busy_students(self, lesson_date: date) -> set:
        """Get set of student IDs who are busy on a specific date"""
        cache_key = f"busy_students_{lesson_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        busy_student_ids = set(
            AttendanceRecord.objects.filter(
                lesson_session__lesson_date=lesson_date
            ).values_list('enrollment__student_id', flat=True)
        )
        
        self._cache[cache_key] = busy_student_ids
        return busy_student_ids


class CompatibilityScorer:
    """Multi-criteria scoring algorithm for student-group compatibility"""
    
    # Scoring weights (can be made configurable later)
    WEIGHTS = {
        'skill_level': 100,
        'year_level': 80,
        'group_size_preference': 50,
        'coach_specialization': 50,
        'lesson_balance': 40,
        'group_capacity': 30,
        'time_preference': 20,
    }
    
    def __init__(self):
        self._group_cache = {}
        self._lesson_balance_cache = {}
        self._enrollment_cache = {}
        self._cache_timeout = 300  # 5 minutes
        self._last_cache_clear = time.time()
    
    def _clear_cache_if_needed(self):
        """Clear cache if it's too old"""
        if time.time() - self._last_cache_clear > self._cache_timeout:
            self._lesson_balance_cache.clear()
            self._enrollment_cache.clear()
            self._group_cache.clear()
            self._last_cache_clear = time.time()
    
    def _bulk_prefetch_lesson_balances(self, students: List[Student], current_term: Term):
        """Bulk prefetch lesson balances for multiple students to avoid N+1 queries"""
        self._clear_cache_if_needed()
        
        # Get student IDs that aren't already cached
        student_ids_to_fetch = [
            s.id for s in students 
            if f"{s.id}_{current_term.id}" not in self._lesson_balance_cache
        ]
        
        if not student_ids_to_fetch:
            return  # All already cached
        
        # Bulk fetch enrollments with attendance counts
        enrollments_with_counts = Enrollment.objects.filter(
            student_id__in=student_ids_to_fetch,
            term=current_term
        ).annotate(
            actual_lessons_count=Count(
                'attendancerecord',
                filter=Q(attendancerecord__status__in=['PRESENT', 'FILL_IN', 'SICK_PRESENT', 'REFUSES_PRESENT'])
            )
        ).select_related('student')
        
        # Cache the results
        for enrollment in enrollments_with_counts:
            cache_key = f"{enrollment.student.id}_{current_term.id}"
            # Calculate balance: target + carried forward - actual lessons
            balance = enrollment.adjusted_target - enrollment.actual_lessons_count
            self._lesson_balance_cache[cache_key] = balance
            self._enrollment_cache[cache_key] = enrollment
    
    def _get_cached_lesson_balance(self, student: Student, current_term: Term) -> int:
        """Get cached lesson balance for a student"""
        cache_key = f"{student.id}_{current_term.id}"
        return self._lesson_balance_cache.get(cache_key, 0)
    
    def _get_cached_enrollment(self, student: Student, current_term: Term):
        """Get cached enrollment for a student"""
        cache_key = f"{student.id}_{current_term.id}"
        return self._enrollment_cache.get(cache_key)
    
    def calculate_compatibility_score(
        self, 
        student: Student, 
        group: ScheduledGroup, 
        coach: Coach = None
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive compatibility score between student and group.
        Returns dict with total score and breakdown.
        """
        if coach is None:
            coach = group.coach
        
        score_breakdown = {}
        total_score = 0
        
        # 1. Skill level compatibility (0-100 points)
        skill_score = self._calculate_skill_score(student, group)
        score_breakdown['skill_level'] = skill_score
        total_score += skill_score * (self.WEIGHTS['skill_level'] / 100)
        
        # 2. Year level compatibility (0-80 points)
        year_score = self._calculate_year_level_score(student, group)
        score_breakdown['year_level'] = year_score
        total_score += year_score * (self.WEIGHTS['year_level'] / 100)
        
        # 3. Group size preference (0-50 points)
        size_score = self._calculate_group_size_score(student, group)
        score_breakdown['group_size_preference'] = size_score
        total_score += size_score * (self.WEIGHTS['group_size_preference'] / 100)
        
        # 4. Coach specialization (0-50 points)
        if coach:
            coach_score = self._calculate_coach_score(student, coach)
            score_breakdown['coach_specialization'] = coach_score
            total_score += coach_score * (self.WEIGHTS['coach_specialization'] / 100)
        
        # 5. Lesson balance consideration (0-40 points)
        balance_score = self._calculate_lesson_balance_score(student)
        score_breakdown['lesson_balance'] = balance_score
        total_score += balance_score * (self.WEIGHTS['lesson_balance'] / 100)
        
        # 6. Group capacity optimization (0-30 points)
        capacity_score = self._calculate_capacity_score(group)
        score_breakdown['group_capacity'] = capacity_score
        total_score += capacity_score * (self.WEIGHTS['group_capacity'] / 100)
        
        return {
            'total_score': int(total_score),
            'breakdown': score_breakdown,
            'max_possible': sum(self.WEIGHTS.values()),
            'percentage': int((total_score / sum(self.WEIGHTS.values())) * 100)
        }
    
    def _calculate_skill_score(self, student: Student, group: ScheduledGroup) -> int:
        """Calculate skill level compatibility score"""
        student_skill = student.skill_level
        target_skill = group.target_skill_level
        
        if student_skill == target_skill:
            return 100
        elif abs(ord(student_skill) - ord(target_skill)) == 1:
            return 60  # Adjacent skill levels
        else:
            return 0  # Too far apart
    
    def _calculate_year_level_score(self, student: Student, group: ScheduledGroup) -> int:
        """Calculate year level compatibility score"""
        avg_year = group.get_average_year_level()
        if avg_year == 0:  # Empty group
            return 80  # Neutral score
        
        year_diff = abs(student.year_level - avg_year)
        if year_diff == 0:
            return 80
        elif year_diff <= 1:
            return 60
        elif year_diff <= 2:
            return 30
        else:
            return 0
    
    def _calculate_group_size_score(self, student: Student, group: ScheduledGroup) -> int:
        """Calculate group size preference score based on student's enrollment type - STRICT MATCHING"""
        # Get the student's current enrollment type from active term
        current_term = Term.get_active_term()
        if not current_term:
            return 0  # No score if no active term
        
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            student_enrollment_type = enrollment.enrollment_type
            
            # Strict matching: SOLO only SOLO, PAIR only PAIR, GROUP can do PAIR or GROUP
            if student_enrollment_type == group.group_type:
                return 50  # Perfect match
            elif student_enrollment_type == 'GROUP' and group.group_type == 'PAIR':
                return 25  # Acceptable - group student in pair slot
            else:
                return 0  # No match - different enrollment types
        except Enrollment.DoesNotExist:
            return 0  # No score if no enrollment
    
    def _calculate_coach_score(self, student: Student, coach: Coach) -> int:
        """Calculate coach specialization score"""
        if coach.specializes_in_skill_level(student.skill_level):
            return 50
        else:
            return 20  # Coach can still teach, but not specialized
    
    def _calculate_lesson_balance_score(self, student: Student) -> int:
        """Calculate lesson balance priority score using cached values"""
        # Get current term enrollment
        current_term = Term.get_active_term()
        if not current_term:
            return 20
        
        # Try to get cached balance first
        balance = self._get_cached_lesson_balance(student, current_term)
        if balance is not None:
            if balance > 3:
                return 40  # High priority - student owes many lessons
            elif balance > 1:
                return 30  # Medium priority
            elif balance >= 0:
                return 20  # Normal priority
            else:
                return 10  # Low priority - student has credit
        
        # Fallback to direct query if not cached
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            balance = enrollment.get_lesson_balance()
            
            if balance > 3:
                return 40  # High priority - student owes many lessons
            elif balance > 1:
                return 30  # Medium priority
            elif balance >= 0:
                return 20  # Normal priority
            else:
                return 10  # Low priority - student has credit
        except Enrollment.DoesNotExist:
            return 20  # Neutral if no enrollment
    
    def _calculate_capacity_score(self, group: ScheduledGroup) -> int:
        """Calculate group capacity optimization score"""
        current_size = group.get_current_size()
        preferred_size = group.preferred_size
        max_capacity = group.max_capacity
        
        if current_size < preferred_size:
            return 30  # Group wants more students
        elif current_size == preferred_size:
            return 20  # Group is at ideal size
        elif current_size < max_capacity:
            return 10  # Group has space but not ideal
        else:
            return 0  # Group is full

    def _is_group_type_compatible(self, student_enrollment_type, group_type):
        """Check if student's enrollment type is compatible with group type"""
        if student_enrollment_type == 'SOLO':
            return group_type == 'SOLO'
        elif student_enrollment_type == 'PAIR':
            return group_type == 'PAIR'
        elif student_enrollment_type == 'GROUP':
            return group_type in ['PAIR', 'GROUP']
        return False

    def _is_enrollment_type_compatible(self, student_type, displaced_type):
        """Check if two enrollment types can swap"""
        if student_type == 'SOLO':
            return displaced_type == 'SOLO'
        elif student_type == 'PAIR':
            return displaced_type == 'PAIR'
        elif student_type == 'GROUP':
            return displaced_type in ['GROUP', 'PAIR']
        return False


class SlotFinderEngine:
    """Core engine for finding optimal lesson slots"""
    
    def __init__(self):
        self.availability_checker = AvailabilityChecker()
        self.compatibility_scorer = CompatibilityScorer()
        self._optimization_cache = {}
    
    def get_dynamic_group_type(self, group: ScheduledGroup, current_term: Term) -> str:
        """Determine the dynamic group type based on current students enrolled"""
        current_members = group.members.filter(term=current_term)
        member_count = current_members.count()
        
        if member_count == 0:
            return 'EMPTY'
        
        # Get enrollment types of current members
        enrollment_types = set(
            current_members.values_list('enrollment_type', flat=True)
        )
        
        # Determine effective type based on current composition
        if len(enrollment_types) == 1:
            # All students have same enrollment type
            single_type = list(enrollment_types)[0]
            if single_type == 'PAIR':
                if member_count == 1:
                    return 'PAIR_WAITING'  # 1 PAIR student waiting for partner
                elif member_count == 2:
                    return 'PAIR_FULL'     # 2 PAIR students, full
                else:
                    return 'PAIR_INVALID'  # More than 2 PAIR students (shouldn't happen)
            elif single_type == 'SOLO':
                return 'SOLO_OCCUPIED' if member_count == 1 else 'SOLO_INVALID'
            elif single_type == 'GROUP':
                return 'GROUP'
        else:
            # Mixed enrollment types
            return 'MIXED'
        
        return 'UNKNOWN'
    
    def find_optimal_slots(
        self, 
        student: Student, 
        max_results: int = 10,
        include_swaps: bool = True,
        max_time_seconds: int = 30
    ) -> List[SlotRecommendation]:
        """
        Find optimal lesson slots for a student.
        Returns ranked list of recommendations.
        """
        start_time = time.time()
        recommendations = []
        
        # Phase 1: Direct placements (quick wins)
        direct_placements = self._find_direct_placements(student)
        recommendations.extend(direct_placements)
        
        if time.time() - start_time > max_time_seconds:
            return self._rank_recommendations(recommendations)[:max_results]
        
        # Phase 2: Single swaps (if enabled)
        if include_swaps:
            swap_options = self._find_single_swaps(student)
            recommendations.extend(swap_options)
        
        if time.time() - start_time > max_time_seconds:
            return self._rank_recommendations(recommendations)[:max_results]
        
        # Return ranked results
        return self._rank_recommendations(recommendations)[:max_results]
    
    def _find_direct_placements(self, student: Student) -> List[SlotRecommendation]:
        """Find groups with available space that student can join directly - DYNAMIC TYPE MATCHING"""
        import logging
        logger = logging.getLogger(__name__)
        
        recommendations = []
        current_term = Term.get_active_term()
        
        if not current_term:
            logger.error(f"❌ No current term for direct placements")
            return recommendations
        
        # Get student's enrollment type
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            student_enrollment_type = enrollment.enrollment_type
            logger.info(f"🔍 Direct placement search for {student.first_name} (enrollment type: {student_enrollment_type})")
        except Enrollment.DoesNotExist:
            logger.error(f"❌ Student {student.first_name} not enrolled in current term")
            return recommendations  # Can't place if no enrollment
        
        # Get student's available time slots
        available_slots = self.availability_checker.get_available_slots(student)
        logger.info(f"📅 Student has {len(available_slots)} available time slots")
        
        total_groups_checked = 0
        compatible_groups_found = 0
        groups_with_space = 0
        
        # Find groups with space at those times
        for day, time_slot in available_slots:
            day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
            day_name = day_names[day] if day < len(day_names) else f'Day {day}'
            
            groups_at_time = ScheduledGroup.objects.filter(
                term=current_term,
                day_of_week=day,
                time_slot=time_slot
            ).select_related('coach').prefetch_related('members__student')
            
            groups_count = groups_at_time.count()
            total_groups_checked += groups_count
            logger.info(f"📍 {day_name} {time_slot}: Found {groups_count} groups")
            
            for group in groups_at_time:
                # DYNAMIC GROUP TYPE DETECTION - Look at actual students in group
                effective_group_type = self._get_effective_group_type(group, current_term)
                current_size = group.get_current_size()
                
                logger.info(f"   🔍 Checking group: {group.name} (effective type: {effective_group_type}, size: {current_size}/{group.max_capacity})")
                
                # Check compatibility based on EFFECTIVE type, not static type
                is_type_compatible = self._is_student_compatible_with_effective_group_type(
                    student_enrollment_type, effective_group_type, current_size
                )
                
                if not is_type_compatible:
                    logger.info(f"   ❌ Type incompatible: {student_enrollment_type} cannot join {effective_group_type} group with {current_size} students")
                    continue
                
                compatible_groups_found += 1
                logger.info(f"   ✅ Type compatible: {student_enrollment_type} can join {effective_group_type} group")
                
                has_space = group.has_space()
                is_compatible = group.is_compatible_with_student(student, student_enrollment_type)
                
                logger.info(f"   📊 Has space: {has_space}, Is compatible: {is_compatible}")
                
                if has_space and is_compatible:
                    groups_with_space += 1
                    # Calculate compatibility score
                    score_info = self.compatibility_scorer.calculate_compatibility_score(
                        student, group, group.coach
                    )
                    
                    logger.info(f"   🎯 MATCH FOUND! Score: {score_info['total_score']}/370 ({score_info['percentage']}%)")
                    
                    recommendation = SlotRecommendation(
                        group=group,
                        score=score_info['total_score'],
                        placement_type='direct',
                        benefits={
                            'score_breakdown': score_info['breakdown'],
                            'percentage': score_info['percentage'],
                            'available_spaces': group.get_available_spaces(),
                            'current_size': group.get_current_size(),
                            'enrollment_type': student_enrollment_type,
                            'effective_group_type': effective_group_type
                        }
                    )
                    recommendations.append(recommendation)
                else:
                    if not has_space:
                        logger.info(f"   ❌ No space available")
                    if not is_compatible:
                        logger.info(f"   ❌ Student not compatible with group")
        
        logger.info(f"📊 DIRECT PLACEMENT SUMMARY:")
        logger.info(f"   Total groups checked: {total_groups_checked}")
        logger.info(f"   Compatible type groups: {compatible_groups_found}")
        logger.info(f"   Groups with space: {groups_with_space}")
        logger.info(f"   Recommendations found: {len(recommendations)}")
        
        return recommendations
    
    def _find_single_swaps(self, student: Student) -> List[SlotRecommendation]:
        """Find single swap opportunities with ENHANCED displacement from full groups"""
        import logging
        logger = logging.getLogger(__name__)
        
        recommendations = []
        current_term = Term.get_active_term()
        
        if not current_term:
            return recommendations
        
        # Get student's enrollment type
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            student_enrollment_type = enrollment.enrollment_type
        except Enrollment.DoesNotExist:
            return recommendations  # Can't swap if no enrollment
        
        logger.info(f"🔄 ENHANCED DISPLACEMENT SEARCH for {student.first_name} ({student_enrollment_type})")
        
        # Get student's available time slots
        available_slots = self.availability_checker.get_available_slots(student)
        
        # Look for groups where we could displace existing students
        for day, time_slot in available_slots:
            groups_at_time = ScheduledGroup.objects.filter(
                term=current_term,
                day_of_week=day,
                time_slot=time_slot
            ).select_related('coach').prefetch_related('members__student')
            
            for group in groups_at_time:
                effective_group_type = self._get_effective_group_type(group, current_term)
                current_size = group.get_current_size()
                
                logger.info(f"   🎯 Analyzing {group.name} ({effective_group_type}, {current_size} students)")
                
                # ENHANCED DISPLACEMENT LOGIC - Handle all group types
                displacement_opportunities = self._find_displacement_opportunities(
                    student, student_enrollment_type, group, effective_group_type, current_size
                )
                
                for opportunity in displacement_opportunities:
                    recommendations.append(opportunity)
                    logger.info(f"   ✅ DISPLACEMENT OPPORTUNITY: {opportunity.placement_type}")
                
                # Also check regular swaps for compatible groups
                if self._is_student_compatible_with_effective_group_type(
                    student_enrollment_type, effective_group_type, current_size
                ):
                    
                    for member in group.members.all():
                        existing_student = member.student
                        
                        # Skip if it's the same student
                        if existing_student.id == student.id:
                            continue
                        
                        # Get displaced student's enrollment type
                        try:
                            displaced_enrollment = existing_student.enrollment_set.get(term=current_term)
                            displaced_enrollment_type = displaced_enrollment.enrollment_type
                        except Enrollment.DoesNotExist:
                            continue  # Skip if no enrollment for displaced student
                        
                        # Only swap with compatible enrollment types
                        if not self.compatibility_scorer._is_enrollment_type_compatible(student_enrollment_type, displaced_enrollment_type):
                            continue
                        
                        # Check if swapping would benefit both students
                        swap_benefit = self._evaluate_swap_benefit(
                            student, existing_student, group
                        )
                        
                        if swap_benefit['beneficial']:
                            # Convert Student objects to JSON-serializable dictionaries
                            recommendation = SlotRecommendation(
                                group=group,
                                score=swap_benefit['total_score'],
                                placement_type='swap',
                                swap_chain=[{
                                    'student_in': {
                                        'id': student.id,
                                        'name': f"{student.first_name} {student.last_name}",
                                        'enrollment_type': student_enrollment_type
                                    },
                                    'student_out': {
                                        'id': existing_student.id,
                                        'name': f"{existing_student.first_name} {existing_student.last_name}",
                                        'enrollment_type': displaced_enrollment_type
                                    },
                                    'group': {
                                        'id': group.id,
                                        'name': group.name,
                                        'time_slot': str(group.time_slot),
                                        'day_of_week': group.day_of_week
                                    },
                                    'benefit_score': swap_benefit['benefit_score'],
                                    'enrollment_type': student_enrollment_type
                                }],
                                benefits=swap_benefit
                            )
                            recommendations.append(recommendation)
        
        logger.info(f"🔄 ENHANCED DISPLACEMENT SEARCH found {len(recommendations)} options")
        return recommendations
    
    def _evaluate_swap_benefit(
        self, 
        new_student: Student, 
        existing_student: Student, 
        group: ScheduledGroup
    ) -> Dict[str, Any]:
        """Evaluate if swapping two students would be beneficial"""
        
        # Calculate current compatibility of existing student
        current_score = self.compatibility_scorer.calculate_compatibility_score(
            existing_student, group, group.coach
        )
        
        # Calculate potential compatibility of new student
        potential_score = self.compatibility_scorer.calculate_compatibility_score(
            new_student, group, group.coach
        )
        
        # Check if existing student has alternative slots available
        existing_alternatives = self._find_direct_placements(existing_student)
        
        if not existing_alternatives:
            return {'beneficial': False, 'reason': 'No alternatives for displaced student'}
        
        # Calculate benefit
        benefit_score = potential_score['total_score'] - current_score['total_score']
        best_alternative = max(existing_alternatives, key=lambda x: x.score)
        
        # AGGRESSIVE DISPLACEMENT: Allow almost any swap that doesn't make things much worse
        beneficial = (
            benefit_score >= -50 and  # Allow major downgrades for displacement opportunities
            best_alternative.score >= current_score['total_score'] - 50  # Very flexible for displaced student
        )
        
        return {
            'beneficial': beneficial,
            'benefit_score': benefit_score,
            'total_score': potential_score['total_score'],
            'current_student_score': current_score['total_score'],
            'new_student_score': potential_score['total_score'],
            'displaced_student_alternatives': len(existing_alternatives),
            'best_alternative_score': best_alternative.score if existing_alternatives else 0
        }
    
    def _get_effective_group_type(self, group: ScheduledGroup, current_term: Term) -> str:
        """Determine the effective group type based on current students enrolled"""
        current_members = group.members.filter(term=current_term)
        member_count = current_members.count()
        
        if member_count == 0:
            return 'EMPTY'
        
        # Get enrollment types of current members
        enrollment_types = set(
            current_members.values_list('enrollment_type', flat=True)
        )
        
        # Determine effective type based on current composition
        if len(enrollment_types) == 1:
            # All students have same enrollment type
            single_type = list(enrollment_types)[0]
            if single_type == 'PAIR':
                if member_count == 1:
                    return 'PAIR_WAITING'  # 1 PAIR student waiting for partner
                elif member_count == 2:
                    return 'PAIR_FULL'     # 2 PAIR students, full
                else:
                    return 'PAIR_INVALID'  # More than 2 PAIR students (shouldn't happen)
            elif single_type == 'SOLO':
                return 'SOLO_OCCUPIED' if member_count == 1 else 'SOLO_INVALID'
            elif single_type == 'GROUP':
                return 'GROUP'
        else:
            # Mixed enrollment types
            return 'MIXED'
        
        return 'UNKNOWN'
    
    def _is_student_compatible_with_effective_group_type(
        self, 
        student_enrollment_type: str, 
        effective_group_type: str, 
        current_size: int
    ) -> bool:
        """Check if student can join group based on effective type and current size"""
        
        if student_enrollment_type == 'SOLO':
            # SOLO students can only join empty groups
            return effective_group_type == 'EMPTY'
        
        elif student_enrollment_type == 'PAIR':
            # PAIR students can only join:
            # 1. Empty groups (will become PAIR group)
            # 2. Groups with exactly 1 PAIR student waiting
            return effective_group_type in ['EMPTY', 'PAIR_WAITING']
        
        elif student_enrollment_type == 'GROUP':
            # GROUP students can join:
            # 1. Empty groups
            # 2. Other GROUP groups with space
            # 3. PAIR groups with 1 student (making it a mixed group)
            return effective_group_type in ['EMPTY', 'GROUP', 'PAIR_WAITING']
        
        return False
    
    def _find_displacement_opportunities(
        self, 
        student: Student, 
        student_enrollment_type: str, 
        group: ScheduledGroup, 
        effective_group_type: str, 
        current_size: int
    ) -> List[SlotRecommendation]:
        """Find displacement opportunities for all group types with enhanced logic"""
        import logging
        logger = logging.getLogger(__name__)
        
        opportunities = []
        current_term = Term.get_active_term()
        
        if not current_term:
            return opportunities
        
        logger.info(f"   🔍 DISPLACEMENT ANALYSIS: {student_enrollment_type} student targeting {effective_group_type} group")
        
        # ENHANCED DISPLACEMENT LOGIC FOR ALL GROUP TYPES
        
        # 1. PAIR students targeting PAIR_FULL groups (2 PAIR students)
        if (student_enrollment_type == 'PAIR' and 
            effective_group_type == 'PAIR_FULL' and 
            current_size == 2):
            
            logger.info(f"   💥 PAIR→PAIR_FULL displacement analysis")
            
            # Find the weaker fit of the 2 current students
            current_students = []
            for member in group.members.all():
                existing_student = member.student
                if existing_student.id != student.id:
                    score = self.compatibility_scorer.calculate_compatibility_score(
                        existing_student, group, group.coach
                    )
                    current_students.append((existing_student, score['total_score']))
            
            if len(current_students) >= 1:
                # Sort by score - displace the weakest fit
                current_students.sort(key=lambda x: x[1])
                weakest_student, weakest_score = current_students[0]
                
                # Calculate new student's score
                new_student_score = self.compatibility_scorer.calculate_compatibility_score(
                    student, group, group.coach
                )
                
                # ENHANCED: Allow displacement if new student is better OR if displaced student has good alternatives
                displaced_alternatives = self._find_direct_placements(weakest_student)
                
                # VERY AGGRESSIVE displacement criteria
                should_displace = (
                    new_student_score['total_score'] >= weakest_score - 50 or  # Allow major downgrades
                    len(displaced_alternatives) > 0  # Any alternative is acceptable
                )
                
                # AGGRESSIVE: Create displacement even with limited alternatives
                if should_displace and len(displaced_alternatives) > 0:
                    best_alternative = max(displaced_alternatives, key=lambda x: x.score)
                    
                    opportunity = SlotRecommendation(
                        group=group,
                        score=new_student_score['total_score'],
                        placement_type='pair_displacement',
                        swap_chain=[{
                            'student_in': student,
                            'student_out': weakest_student,
                            'group': group,
                            'benefit_score': new_student_score['total_score'] - weakest_score,
                            'enrollment_type': student_enrollment_type,
                            'displacement_reason': f'PAIR displacement from PAIR_FULL group',
                            'alternative_placement': best_alternative.group.name,
                            'alternative_score': best_alternative.score
                        }],
                        benefits={
                            'displacement_type': 'PAIR_FULL_DISPLACEMENT',
                            'new_student_score': new_student_score['total_score'],
                            'displaced_student_score': weakest_score,
                            'benefit_score': new_student_score['total_score'] - weakest_score,
                            'displaced_alternatives': len(displaced_alternatives),
                            'best_alternative_score': best_alternative.score
                        }
                    )
                    opportunities.append(opportunity)
                    logger.info(f"   ✅ PAIR_FULL displacement created!")
        
        # 2. GROUP students targeting PAIR_WAITING groups (1 PAIR student)
        elif (student_enrollment_type == 'GROUP' and 
              effective_group_type == 'PAIR_WAITING' and 
              current_size == 1):
            
            logger.info(f"   💥 GROUP→PAIR_WAITING displacement analysis")
            
            # Get the single PAIR student
            pair_student = None
            for member in group.members.all():
                if member.student.id != student.id:
                    pair_student = member.student
                    break
            
            if pair_student:
                # Find alternatives for the PAIR student
                pair_alternatives = self._find_direct_placements(pair_student)
                
                # Calculate scores
                pair_current_score = self.compatibility_scorer.calculate_compatibility_score(
                    pair_student, group, group.coach
                )
                group_new_score = self.compatibility_scorer.calculate_compatibility_score(
                    student, group, group.coach
                )
                
                # Allow displacement if GROUP student fits well and PAIR student has alternatives
                if (len(pair_alternatives) > 0 and 
                    group_new_score['total_score'] >= pair_current_score['total_score'] - 40):
                    
                    best_alternative = max(pair_alternatives, key=lambda x: x.score)
                    
                    opportunity = SlotRecommendation(
                        group=group,
                        score=group_new_score['total_score'],
                        placement_type='group_pair_displacement',
                        swap_chain=[{
                            'student_in': student,
                            'student_out': pair_student,
                            'group': group,
                            'benefit_score': group_new_score['total_score'] - pair_current_score['total_score'],
                            'enrollment_type': student_enrollment_type,
                            'displacement_reason': f'GROUP displacing PAIR_WAITING student',
                            'alternative_placement': best_alternative.group.name,
                            'alternative_score': best_alternative.score
                        }],
                        benefits={
                            'displacement_type': 'GROUP_PAIR_WAITING_DISPLACEMENT',
                            'new_student_score': group_new_score['total_score'],
                            'displaced_student_score': pair_current_score['total_score'],
                            'benefit_score': group_new_score['total_score'] - pair_current_score['total_score'],
                            'displaced_alternatives': len(pair_alternatives),
                            'best_alternative_score': best_alternative.score
                        }
                    )
                    opportunities.append(opportunity)
                    logger.info(f"   ✅ GROUP→PAIR_WAITING displacement created!")
        
        # 3. GROUP students targeting full GROUP groups
        elif (student_enrollment_type == 'GROUP' and 
              effective_group_type == 'GROUP' and 
              current_size >= group.max_capacity):
            
            logger.info(f"   💥 GROUP→GROUP_FULL displacement analysis")
            
            # Find the weakest fit in the full GROUP
            current_students = []
            for member in group.members.all():
                existing_student = member.student
                if existing_student.id != student.id:
                    score = self.compatibility_scorer.calculate_compatibility_score(
                        existing_student, group, group.coach
                    )
                    current_students.append((existing_student, score['total_score']))
            
            if len(current_students) >= 1:
                # Sort by score - displace the weakest fit
                current_students.sort(key=lambda x: x[1])
                weakest_student, weakest_score = current_students[0]
                
                # Calculate new student's score
                new_student_score = self.compatibility_scorer.calculate_compatibility_score(
                    student, group, group.coach
                )
                
                # Find alternatives for displaced student
                displaced_alternatives = self._find_direct_placements(weakest_student)
                
                # Allow displacement if new student is significantly better
                if (new_student_score['total_score'] > weakest_score + 20 and 
                    len(displaced_alternatives) > 0):
                    
                    best_alternative = max(displaced_alternatives, key=lambda x: x.score)
                    
                    opportunity = SlotRecommendation(
                        group=group,
                        score=new_student_score['total_score'],
                        placement_type='group_displacement',
                        swap_chain=[{
                            'student_in': student,
                            'student_out': weakest_student,
                            'group': group,
                            'benefit_score': new_student_score['total_score'] - weakest_score,
                            'enrollment_type': student_enrollment_type,
                            'displacement_reason': f'GROUP displacing weaker GROUP student',
                            'alternative_placement': best_alternative.group.name,
                            'alternative_score': best_alternative.score
                        }],
                        benefits={
                            'displacement_type': 'GROUP_FULL_DISPLACEMENT',
                            'new_student_score': new_student_score['total_score'],
                            'displaced_student_score': weakest_score,
                            'benefit_score': new_student_score['total_score'] - weakest_score,
                            'displaced_alternatives': len(displaced_alternatives),
                            'best_alternative_score': best_alternative.score
                        }
                    )
                    opportunities.append(opportunity)
                    logger.info(f"   ✅ GROUP_FULL displacement created!")
        
        # 4. SOLO students targeting SOLO_OCCUPIED groups
        elif (student_enrollment_type == 'SOLO' and 
              effective_group_type == 'SOLO_OCCUPIED' and 
              current_size == 1):
            
            logger.info(f"   💥 SOLO→SOLO_OCCUPIED displacement analysis")
            
            # Get the current SOLO student
            current_solo_student = None
            for member in group.members.all():
                if member.student.id != student.id:
                    current_solo_student = member.student
                    break
            
            if current_solo_student:
                # Calculate scores
                current_score = self.compatibility_scorer.calculate_compatibility_score(
                    current_solo_student, group, group.coach
                )
                new_score = self.compatibility_scorer.calculate_compatibility_score(
                    student, group, group.coach
                )
                
                # Find alternatives for current student
                displaced_alternatives = self._find_direct_placements(current_solo_student)
                
                # Allow displacement if new student is better and displaced has alternatives
                if (new_score['total_score'] > current_score['total_score'] + 10 and 
                    len(displaced_alternatives) > 0):
                    
                    best_alternative = max(displaced_alternatives, key=lambda x: x.score)
                    
                    opportunity = SlotRecommendation(
                        group=group,
                        score=new_score['total_score'],
                        placement_type='solo_displacement',
                        swap_chain=[{
                            'student_in': student,
                            'student_out': current_solo_student,
                            'group': group,
                            'benefit_score': new_score['total_score'] - current_score['total_score'],
                            'enrollment_type': student_enrollment_type,
                            'displacement_reason': f'SOLO displacing current SOLO student',
                            'alternative_placement': best_alternative.group.name,
                            'alternative_score': best_alternative.score
                        }],
                        benefits={
                            'displacement_type': 'SOLO_OCCUPIED_DISPLACEMENT',
                            'new_student_score': new_score['total_score'],
                            'displaced_student_score': current_score['total_score'],
                            'benefit_score': new_score['total_score'] - current_score['total_score'],
                            'displaced_alternatives': len(displaced_alternatives),
                            'best_alternative_score': best_alternative.score
                        }
                    )
                    opportunities.append(opportunity)
                    logger.info(f"   ✅ SOLO_OCCUPIED displacement created!")
        
        logger.info(f"   📊 Found {len(opportunities)} displacement opportunities")
        return opportunities
    
    def _rank_recommendations(self, recommendations: List[SlotRecommendation]) -> List[SlotRecommendation]:
        """Rank recommendations by score and other factors"""
        return sorted(
            recommendations, 
            key=lambda x: (x.score, x.placement_type == 'direct'), 
            reverse=True
        )


@dataclass
class SwapMove:
    """Represents a single move in a swap chain"""
    student: Student
    from_group: ScheduledGroup
    to_group: ScheduledGroup
    benefit_score: int
    displaced_student: Optional[Student] = None


class SwapChain:
    """Manages complex multi-student swap chains"""
    
    def __init__(self, initial_student: Student):
        self.initial_student = initial_student
        self.moves: List[SwapMove] = []
        self.total_benefit = 0
        self.is_complete = False
        self.validation_errors: List[str] = []
    
    def add_move(self, move: SwapMove):
        """Add a move to the chain"""
        self.moves.append(move)
        self.total_benefit += move.benefit_score
        
        # Check if chain is complete (final move doesn't displace anyone)
        if move.displaced_student is None:
            self.is_complete = True
    
    def get_chain_length(self) -> int:
        """Get the number of moves in the chain"""
        return len(self.moves)
    
    def get_affected_students(self) -> List[Student]:
        """Get all students affected by this chain"""
        students = [self.initial_student]
        for move in self.moves:
            if move.displaced_student and move.displaced_student not in students:
                students.append(move.displaced_student)
        return students
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert SwapChain to JSON-serializable dictionary"""
        return {
            'initial_student_id': self.initial_student.id,
            'initial_student_name': f"{self.initial_student.first_name} {self.initial_student.last_name}",
            'moves': [
                {
                    'student_id': move.student.id,
                    'student_name': f"{move.student.first_name} {move.student.last_name}",
                    'from_group_id': move.from_group.id if move.from_group else None,
                    'from_group_name': move.from_group.name if move.from_group else None,
                    'to_group_id': move.to_group.id,
                    'to_group_name': move.to_group.name,
                    'benefit_score': move.benefit_score,
                    'displaced_student_id': move.displaced_student.id if move.displaced_student else None,
                    'displaced_student_name': f"{move.displaced_student.first_name} {move.displaced_student.last_name}" if move.displaced_student else None
                }
                for move in self.moves
            ],
            'total_benefit': self.total_benefit,
            'chain_length': len(self.moves),
            'affected_students_count': len(self.get_affected_students()),
            'is_complete': self.is_complete,
            'validation_errors': self.validation_errors
        }
    
    def validate_chain(self) -> Tuple[bool, List[str]]:
        """Comprehensive validation of the swap chain"""
        errors = []
        
        # 1. Check chain completeness
        if not self.is_complete:
            errors.append("Chain is not complete - final move displaces another student")
        
        # 2. Check for circular dependencies
        if self._has_circular_dependency():
            errors.append("Circular dependency detected in swap chain")
        
        # 3. Validate each move
        for i, move in enumerate(self.moves):
            move_errors = self._validate_move(move, i)
            errors.extend(move_errors)
        
        # 4. Check all students are still available
        for student in self.get_affected_students():
            if not self._is_student_still_available(student):
                errors.append(f"Student {student} is no longer available for moves")
        
        self.validation_errors = errors
        return len(errors) == 0, errors
    
    def _has_circular_dependency(self) -> bool:
        """Check for circular dependencies in the chain"""
        student_moves = {}
        
        for move in self.moves:
            if move.student in student_moves:
                return True  # Student appears twice
            student_moves[move.student] = move.to_group
        
        return False
    
    def _validate_move(self, move: SwapMove, move_index: int) -> List[str]:
        """Validate a single move in the chain"""
        errors = []
        
        # Check if target group has space or will have space after previous moves
        if not self._will_group_have_space(move.to_group, move_index):
            errors.append(f"Group {move.to_group} will not have space for {move.student}")
        
        # Check compatibility
        if not move.to_group.is_compatible_with_student(move.student):
            errors.append(f"Student {move.student} is not compatible with group {move.to_group}")
        
        # Check availability
        if not self._is_student_available_for_group(move.student, move.to_group):
            errors.append(f"Student {move.student} is not available for {move.to_group} time slot")
        
        return errors
    
    def _will_group_have_space(self, group: ScheduledGroup, move_index: int) -> bool:
        """Check if group will have space after considering previous moves"""
        current_size = group.get_current_size()
        
        # Count moves that affect this group up to this point
        moves_in = sum(1 for i, move in enumerate(self.moves[:move_index + 1]) if move.to_group == group)
        moves_out = sum(1 for i, move in enumerate(self.moves[:move_index]) if move.from_group == group)
        
        projected_size = current_size + moves_in - moves_out
        return projected_size <= group.max_capacity
    
    def _is_student_still_available(self, student: Student) -> bool:
        """Check if student is still available (not moved by another process)"""
        # This would check against recent database changes
        # For now, assume students are available
        return True
    
    def _is_student_available_for_group(self, student: Student, group: ScheduledGroup) -> bool:
        """Check if student is available for the group's time slot"""
        conflict_info = student.has_scheduling_conflict(group.day_of_week, group.time_slot)
        return not conflict_info['has_conflict']
    
    def execute_chain(self) -> Tuple[bool, str]:
        """Execute the entire swap chain as an atomic transaction"""
        is_valid, errors = self.validate_chain()
        if not is_valid:
            return False, f"Validation failed: {'; '.join(errors)}"
        
        try:
            with transaction.atomic():
                # Create a savepoint for rollback
                savepoint = transaction.savepoint()
                
                try:
                    # Execute all moves in sequence
                    for move in self.moves:
                        self._execute_single_move(move)
                    
                    # Final validation of database state
                    if not self._validate_final_state():
                        transaction.savepoint_rollback(savepoint)
                        return False, "Final state validation failed"
                    
                    # Commit all changes
                    transaction.savepoint_commit(savepoint)
                    return True, f"Successfully executed {len(self.moves)}-move chain"
                    
                except Exception as e:
                    transaction.savepoint_rollback(savepoint)
                    return False, f"Execution failed: {str(e)}"
                    
        except Exception as e:
            return False, f"Transaction failed: {str(e)}"
    
    def _execute_single_move(self, move: SwapMove):
        """Execute a single move in the chain"""
        current_term = Term.get_active_term()
        if not current_term:
            raise Exception("No active term found")
        
        # Get the student's enrollment
        try:
            enrollment = move.student.enrollment_set.get(term=current_term)
        except Enrollment.DoesNotExist:
            raise Exception(f"No enrollment found for {move.student} in current term")
        
        # Remove from old group
        if move.from_group:
            move.from_group.members.remove(enrollment)
        
        # Add to new group
        move.to_group.members.add(enrollment)
    
    def _validate_final_state(self) -> bool:
        """Validate the final state after all moves"""
        # Check that all groups are within capacity
        affected_groups = set()
        for move in self.moves:
            affected_groups.add(move.from_group)
            affected_groups.add(move.to_group)
        
        for group in affected_groups:
            if group and group.get_current_size() > group.max_capacity:
                return False
        
        return True


class SwapChainBuilder:
    """Builds complex swap chains with intelligent pruning"""
    
    def __init__(self, slot_finder_engine: 'SlotFinderEngine'):
        self.engine = slot_finder_engine
        self.max_chain_depth = 20  # EXTENDED: Allow up to 20 moves in a chain
        self.max_chains_to_explore = 100  # More chains to explore
        self.min_benefit_threshold = -10  # AGGRESSIVE: Allow negative benefit chains
    
    def find_swap_chains(
        self, 
        student: Student, 
        max_depth: int = None,
        max_time_seconds: int = 60
    ) -> List[SwapChain]:
        """Find beneficial swap chains for a student"""
        if max_depth is None:
            max_depth = self.max_chain_depth
        
        start_time = time.time()
        completed_chains = []
        
        # Start with single swaps and build from there
        initial_swaps = self._find_initial_swap_opportunities(student)
        
        for initial_swap in initial_swaps:
            if time.time() - start_time > max_time_seconds:
                break
            
            # Build chain starting from this swap
            chain = SwapChain(student)
            
            # Try to build a complete chain
            if self._build_chain_recursively(
                chain, initial_swap, max_depth - 1, start_time, max_time_seconds
            ):
                completed_chains.append(chain)
        
        # Sort by total benefit
        return sorted(completed_chains, key=lambda x: x.total_benefit, reverse=True)
    
    def _find_initial_swap_opportunities(self, student: Student) -> List[Dict]:
        """Find initial swap opportunities for the student with enrollment type compatibility"""
        opportunities = []
        current_term = Term.get_active_term()
        
        if not current_term:
            return opportunities
        
        # Get student's enrollment type
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            student_enrollment_type = enrollment.enrollment_type
        except Enrollment.DoesNotExist:
            return opportunities  # Can't swap if no enrollment
        
        # Get student's available time slots
        available_slots = self.engine.availability_checker.get_available_slots(student)
        
        # Look for beneficial swaps
        for day, time_slot in available_slots:
            groups_at_time = ScheduledGroup.objects.filter(
                term=current_term,
                day_of_week=day,
                time_slot=time_slot
            ).select_related('coach').prefetch_related('members__student')
            
            for group in groups_at_time:
                # Only consider groups of compatible type
                if not self.engine.compatibility_scorer._is_group_type_compatible(student_enrollment_type, group.group_type):
                    continue
                
                for member in group.members.all():
                    existing_student = member.student
                    
                    if existing_student.id == student.id:
                        continue
                    
                    # Get displaced student's enrollment type
                    try:
                        displaced_enrollment = existing_student.enrollment_set.get(term=current_term)
                        displaced_enrollment_type = displaced_enrollment.enrollment_type
                    except Enrollment.DoesNotExist:
                        continue  # Skip if no enrollment for displaced student
                    
                    # Only swap with compatible enrollment types
                    if not self.engine.compatibility_scorer._is_enrollment_type_compatible(student_enrollment_type, displaced_enrollment_type):
                        continue
                    
                    # Evaluate swap benefit
                    swap_benefit = self.engine._evaluate_swap_benefit(
                        student, existing_student, group
                    )
                    
                    if swap_benefit['beneficial'] and swap_benefit['benefit_score'] >= self.min_benefit_threshold:
                        opportunities.append({
                            'target_student': student,
                            'displaced_student': existing_student,
                            'target_group': group,
                            'benefit_score': swap_benefit['benefit_score'],
                            'student_type': student_enrollment_type,
                            'displaced_type': displaced_enrollment_type
                        })
        
        return sorted(opportunities, key=lambda x: x['benefit_score'], reverse=True)
    
    def _build_chain_recursively(
        self, 
        chain: SwapChain, 
        swap_opportunity: Dict,
        remaining_depth: int,
        start_time: float,
        max_time_seconds: int
    ) -> bool:
        """Recursively build a swap chain with enrollment type compatibility"""
        
        if time.time() - start_time > max_time_seconds:
            return False
        
        # Get the student type from the opportunity
        student_enrollment_type = swap_opportunity.get('student_type')
        if not student_enrollment_type:
            # Fallback to getting from student if not provided
            current_term = Term.get_active_term()
            if current_term:
                try:
                    enrollment = swap_opportunity['target_student'].enrollment_set.get(term=current_term)
                    student_enrollment_type = enrollment.enrollment_type
                except Enrollment.DoesNotExist:
                    return False
        
        # Add the current swap to the chain
        move = SwapMove(
            student=swap_opportunity['target_student'],
            from_group=None,  # Will be determined by current enrollment
            to_group=swap_opportunity['target_group'],
            benefit_score=swap_opportunity['benefit_score'],
            displaced_student=swap_opportunity['displaced_student']
        )
        
        chain.add_move(move)
        
        # If no one is displaced, chain is complete
        if move.displaced_student is None:
            return True
        
        # If we've reached max depth, try to find a direct placement for displaced student
        if remaining_depth <= 0:
            direct_placements = self.engine._find_direct_placements(move.displaced_student)
            if direct_placements:
                # Add direct placement move
                best_placement = max(direct_placements, key=lambda x: x.score)
                final_move = SwapMove(
                    student=move.displaced_student,
                    from_group=swap_opportunity['target_group'],
                    to_group=best_placement.group,
                    benefit_score=best_placement.score,
                    displaced_student=None
                )
                chain.add_move(final_move)
                return True
            else:
                return False  # Can't complete chain
        
        # Try to find a placement for the displaced student with type compatibility
        displaced_student = move.displaced_student
        current_term = Term.get_active_term()
        if current_term:
            try:
                displaced_enrollment = displaced_student.enrollment_set.get(term=current_term)
                displaced_enrollment_type = displaced_enrollment.enrollment_type
            except Enrollment.DoesNotExist:
                return False
        
        displaced_opportunities = self._find_initial_swap_opportunities(move.displaced_student)
        
        for opportunity in displaced_opportunities[:5]:  # Limit exploration
            if time.time() - start_time > max_time_seconds:
                break
            
            # Check if this opportunity is compatible with the displaced student's type
            if not self.engine.compatibility_scorer._is_enrollment_type_compatible(
                displaced_enrollment_type, 
                opportunity['displaced_type']
            ):
                continue
            
            # Create a copy of the chain to explore this branch
            chain_copy = SwapChain(chain.initial_student)
            chain_copy.moves = chain.moves.copy()
            chain_copy.total_benefit = chain.total_benefit
            
            if self._build_chain_recursively(
                chain_copy, opportunity, remaining_depth - 1, start_time, max_time_seconds
            ):
                # Update original chain with successful branch
                chain.moves = chain_copy.moves
                chain.total_benefit = chain_copy.total_benefit
                chain.is_complete = chain_copy.is_complete
                return True
        
        return False


# Enhanced SlotFinderEngine with swap chains
class EnhancedSlotFinderEngine(SlotFinderEngine):
    """Enhanced engine with complex swap chain capabilities"""
    
    def __init__(self):
        super().__init__()
        self.chain_builder = SwapChainBuilder(self)
    
    def find_optimal_slots(
        self, 
        student: Student, 
        max_results: int = 10,
        include_swaps: bool = True,
        include_chains: bool = True,
        max_time_seconds: int = 60
    ) -> List[SlotRecommendation]:
        """
        Enhanced slot finding with complex swap chains and bulk optimization.
        Always returns some options when possible, including alternative placements.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        start_time = time.time()
        recommendations = []
        current_term = Term.get_active_term()
        
        logger.info(f"🔍 SLOT FINDER DEBUG: Starting analysis for student {student.id} ({student.first_name} {student.last_name})")
        
        if not current_term:
            logger.error(f"❌ No active term found")
            return recommendations
        
        logger.info(f"✅ Active term: {current_term.name}")
        
        # Check student enrollment
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            logger.info(f"✅ Student enrollment found: {enrollment.enrollment_type} type")
        except Enrollment.DoesNotExist:
            logger.error(f"❌ Student not enrolled in current term")
            return recommendations
        
        # Bulk prefetch lesson balances for performance optimization
        # Get all students that might be involved in analysis
        all_students_to_analyze = [student]
        
        # Get students from groups at available time slots for swap analysis
        if include_swaps or include_chains:
            available_slots = self.availability_checker.get_available_slots(student)
            for day, time_slot in available_slots:
                groups_at_time = ScheduledGroup.objects.filter(
                    term=current_term,
                    day_of_week=day,
                    time_slot=time_slot
                ).prefetch_related('members__student')
                
                for group in groups_at_time:
                    for member in group.members.all():
                        if member.student not in all_students_to_analyze:
                            all_students_to_analyze.append(member.student)
        
        # Bulk prefetch lesson balances for all students we'll analyze
        self.compatibility_scorer._bulk_prefetch_lesson_balances(all_students_to_analyze, current_term)
        
        # Phase 1: Direct placements (quick wins)
        direct_placements = self._find_direct_placements(student)
        recommendations.extend(direct_placements)
        
        if time.time() - start_time > max_time_seconds * 0.3:  # Use 30% of time for direct
            ranked = self._rank_recommendations(recommendations)[:max_results]
            if len(ranked) < 3:  # If we have fewer than 3 options, try to find more
                ranked.extend(self._find_alternative_placements(student, exclude_groups=[r.group for r in ranked]))
            return ranked[:max_results]
        
        # Phase 2: Single swaps
        if include_swaps:
            swap_options = self._find_single_swaps(student)
            recommendations.extend(swap_options)
        
        if time.time() - start_time > max_time_seconds * 0.6:  # Use 60% of time for swaps
            ranked = self._rank_recommendations(recommendations)[:max_results]
            if len(ranked) < 3:  # If we have fewer than 3 options, try to find more
                ranked.extend(self._find_alternative_placements(student, exclude_groups=[r.group for r in ranked]))
            return ranked[:max_results]
        
        # Phase 3: Complex swap chains
        if include_chains:
            remaining_time = max_time_seconds - (time.time() - start_time)
            swap_chains = self.chain_builder.find_swap_chains(
                student, 
                max_time_seconds=int(remaining_time)
            )
            
            # Convert chains to recommendations
            for chain in swap_chains[:5]:  # Limit to top 5 chains
                if chain.is_complete:
                    recommendation = SlotRecommendation(
                        group=chain.moves[0].to_group,
                        score=chain.total_benefit,
                        placement_type='chain',
                        swap_chain=chain,
                        benefits={
                            'chain_length': chain.get_chain_length(),
                            'total_benefit': chain.total_benefit,
                            'affected_students': len(chain.get_affected_students())
                        }
                    )
                    recommendations.append(recommendation)
        
        # Final ranking with alternative options if needed
        ranked = self._rank_recommendations(recommendations)[:max_results]
        if len(ranked) < 3:  # If we have fewer than 3 options, try to find more
            ranked.extend(self._find_alternative_placements(student, exclude_groups=[r.group for r in ranked]))
        
        return ranked[:max_results]
    
    def _find_alternative_placements(self, student: Student, exclude_groups: List[ScheduledGroup] = None) -> List[SlotRecommendation]:
        """Find alternative placement options even if they're not better than current placement"""
        if exclude_groups is None:
            exclude_groups = []
        
        recommendations = []
        current_term = Term.get_active_term()
        
        if not current_term:
            return recommendations
        
        # Get student's enrollment type
        try:
            enrollment = student.enrollment_set.get(term=current_term)
            student_enrollment_type = enrollment.enrollment_type
        except Enrollment.DoesNotExist:
            return recommendations
        
        # Get student's available time slots
        available_slots = self.availability_checker.get_available_slots(student)
        
        # Find ALL compatible groups, not just better ones
        for day, time_slot in available_slots:
            groups_at_time = ScheduledGroup.objects.filter(
                term=current_term,
                day_of_week=day,
                time_slot=time_slot
            ).select_related('coach').prefetch_related('members')
            
            for group in groups_at_time:
                # Skip groups we've already recommended
                if group in exclude_groups:
                    continue
                
                # Only consider groups of compatible type
                if not self.compatibility_scorer._is_group_type_compatible(student_enrollment_type, group.group_type):
                    continue
                
                # Include groups with space OR groups where student could swap
                if group.has_space() and group.is_compatible_with_student(student, student_enrollment_type):
                    # Calculate compatibility score
                    score_info = self.compatibility_scorer.calculate_compatibility_score(
                        student, group, group.coach
                    )
                    
                    recommendation = SlotRecommendation(
                        group=group,
                        score=score_info['total_score'],
                        placement_type='alternative',
                        benefits={
                            'score_breakdown': score_info['breakdown'],
                            'percentage': score_info['percentage'],
                            'available_spaces': group.get_available_spaces(),
                            'current_size': group.get_current_size(),
                            'enrollment_type': student_enrollment_type,
                            'is_alternative': True
                        }
                    )
                    recommendations.append(recommendation)
        
        # Return top alternatives
        return sorted(recommendations, key=lambda x: x.score, reverse=True)[:5]


# Utility functions for quick access
def find_better_slot(student_id: int, max_results: int = 5, include_chains: bool = True) -> List[SlotRecommendation]:
    """Quick function to find better slots for a student"""
    try:
        student = Student.objects.get(id=student_id)
        engine = EnhancedSlotFinderEngine()
        return engine.find_optimal_slots(
            student, 
            max_results=max_results,
            include_chains=include_chains
        )
    except Student.DoesNotExist:
        return []


def check_student_availability(student_id: int, day: int, time_slot_id: int) -> bool:
    """Quick function to check if student is available"""
    try:
        student = Student.objects.get(id=student_id)
        time_slot = TimeSlot.objects.get(id=time_slot_id)
        checker = AvailabilityChecker()
        return checker.is_student_available(student, day, time_slot)
    except (Student.DoesNotExist, TimeSlot.DoesNotExist):
        return False


def execute_swap_chain(chain: SwapChain) -> Tuple[bool, str]:
    """Execute a swap chain with full validation"""
    return chain.execute_chain()
