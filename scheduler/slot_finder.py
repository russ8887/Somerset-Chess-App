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
        """
        self._clear_cache_if_needed()
        
        cache_key = f"available_slots_{student.id}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        available_slots = []
        time_slots = TimeSlot.objects.all()
        
        for day in range(5):  # Monday to Friday
            for time_slot in time_slots:
                conflict_info = student.has_scheduling_conflict(day, time_slot)
                if not conflict_info['has_conflict']:
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
        """Calculate group size preference score"""
        if student.preferred_group_size == group.group_type:
            return 50
        elif student.preferred_group_size == 'GROUP' and group.group_type in ['PAIR', 'GROUP']:
            return 30  # Flexible preference
        else:
            return 10  # Not preferred but acceptable
    
    def _calculate_coach_score(self, student: Student, coach: Coach) -> int:
        """Calculate coach specialization score"""
        if coach.specializes_in_skill_level(student.skill_level):
            return 50
        else:
            return 20  # Coach can still teach, but not specialized
    
    def _calculate_lesson_balance_score(self, student: Student) -> int:
        """Calculate lesson balance priority score"""
        # Get current term enrollment
        current_term = Term.get_active_term()
        if not current_term:
            return 20
        
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


class SlotFinderEngine:
    """Core engine for finding optimal lesson slots"""
    
    def __init__(self):
        self.availability_checker = AvailabilityChecker()
        self.compatibility_scorer = CompatibilityScorer()
        self._optimization_cache = {}
    
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
        """Find groups with available space that student can join directly"""
        recommendations = []
        current_term = Term.get_active_term()
        
        if not current_term:
            return recommendations
        
        # Get student's available time slots
        available_slots = self.availability_checker.get_available_slots(student)
        
        # Find groups with space at those times
        for day, time_slot in available_slots:
            groups_at_time = ScheduledGroup.objects.filter(
                term=current_term,
                day_of_week=day,
                time_slot=time_slot
            ).select_related('coach').prefetch_related('members')
            
            for group in groups_at_time:
                if group.has_space() and group.is_compatible_with_student(student):
                    # Calculate compatibility score
                    score_info = self.compatibility_scorer.calculate_compatibility_score(
                        student, group, group.coach
                    )
                    
                    recommendation = SlotRecommendation(
                        group=group,
                        score=score_info['total_score'],
                        placement_type='direct',
                        benefits={
                            'score_breakdown': score_info['breakdown'],
                            'percentage': score_info['percentage'],
                            'available_spaces': group.get_available_spaces(),
                            'current_size': group.get_current_size()
                        }
                    )
                    recommendations.append(recommendation)
        
        return recommendations
    
    def _find_single_swaps(self, student: Student) -> List[SlotRecommendation]:
        """Find single swap opportunities"""
        recommendations = []
        current_term = Term.get_active_term()
        
        if not current_term:
            return recommendations
        
        # Get student's available time slots
        available_slots = self.availability_checker.get_available_slots(student)
        
        # Look for groups where we could swap with existing students
        for day, time_slot in available_slots:
            groups_at_time = ScheduledGroup.objects.filter(
                term=current_term,
                day_of_week=day,
                time_slot=time_slot
            ).select_related('coach').prefetch_related('members__student')
            
            for group in groups_at_time:
                # Check each student in the group for swap potential
                for member in group.members.all():
                    existing_student = member.student
                    
                    # Skip if it's the same student
                    if existing_student.id == student.id:
                        continue
                    
                    # Check if swapping would benefit both students
                    swap_benefit = self._evaluate_swap_benefit(
                        student, existing_student, group
                    )
                    
                    if swap_benefit['beneficial']:
                        recommendation = SlotRecommendation(
                            group=group,
                            score=swap_benefit['total_score'],
                            placement_type='swap',
                            swap_chain=[{
                                'student_in': student,
                                'student_out': existing_student,
                                'group': group,
                                'benefit_score': swap_benefit['benefit_score']
                            }],
                            benefits=swap_benefit
                        )
                        recommendations.append(recommendation)
        
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
        
        # Only beneficial if new student fits better AND displaced student has good alternatives
        beneficial = (
            benefit_score > 20 and  # Significant improvement
            best_alternative.score >= current_score['total_score'] - 10  # Displaced student not worse off
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
    
    def _rank_recommendations(self, recommendations: List[SlotRecommendation]) -> List[SlotRecommendation]:
        """Rank recommendations by score and other factors"""
        return sorted(
            recommendations, 
            key=lambda x: (x.score, x.placement_type == 'direct'), 
            reverse=True
        )


# Utility functions for quick access
def find_better_slot(student_id: int, max_results: int = 5) -> List[SlotRecommendation]:
    """Quick function to find better slots for a student"""
    try:
        student = Student.objects.get(id=student_id)
        engine = SlotFinderEngine()
        return engine.find_optimal_slots(student, max_results=max_results)
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
