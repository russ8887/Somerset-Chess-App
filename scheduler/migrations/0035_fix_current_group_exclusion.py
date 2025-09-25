from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0034_fix_pair_student_group_size_constraints'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Drop the previous function and create the fixed version with proper current group exclusion
            DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);
            
            CREATE FUNCTION find_optimal_slots_advanced(
                target_student_id INTEGER,
                student_term_id INTEGER,
                include_displacements BOOLEAN DEFAULT TRUE,
                max_results INTEGER DEFAULT 999
            )
            RETURNS TABLE(
                slot_id BIGINT,
                group_id BIGINT,
                group_name VARCHAR(100),
                coach_name VARCHAR(202),
                day_name VARCHAR(10),
                time_slot VARCHAR(50),
                compatibility_score INTEGER,
                placement_type VARCHAR(20),
                current_size INTEGER,
                max_capacity INTEGER,
                displacement_info JSON,
                explanation VARCHAR(500),
                feasibility_score INTEGER
            )
            LANGUAGE plpgsql
            AS $$
            DECLARE
                student_skill_level VARCHAR(1);
                student_enrollment_type VARCHAR(10);
                student_current_group_ids INTEGER[];
                result_count INTEGER := 0;
            BEGIN
                -- FIXED: Get student details and ALL current groups properly
                SELECT s.skill_level, e.enrollment_type, 
                       ARRAY_AGG(DISTINCT sgm.scheduledgroup_id) FILTER (WHERE sgm.scheduledgroup_id IS NOT NULL)
                INTO student_skill_level, student_enrollment_type, student_current_group_ids
                FROM scheduler_student s
                JOIN scheduler_enrollment e ON s.id = e.student_id
                LEFT JOIN scheduler_scheduledgroup_members sgm ON e.id = sgm.enrollment_id
                LEFT JOIN scheduler_scheduledgroup sg ON sgm.scheduledgroup_id = sg.id AND sg.term_id = student_term_id
                WHERE s.id = target_student_id AND e.term_id = student_term_id
                GROUP BY s.skill_level, e.enrollment_type;
                
                -- Return error if student not found
                IF student_skill_level IS NULL THEN
                    RETURN QUERY
                    SELECT 
                        -1::BIGINT as slot_id, -1::BIGINT as group_id,
                        'ERROR: Student not found'::VARCHAR(100) as group_name,
                        ('Student ID: ' || target_student_id || ', Term ID: ' || student_term_id)::VARCHAR(202) as coach_name,
                        'ERROR'::VARCHAR(10) as day_name, 'No student data'::VARCHAR(50) as time_slot,
                        0 as compatibility_score, 'error'::VARCHAR(20) as placement_type,
                        0 as current_size, 0 as max_capacity,
                        json_build_object('error', 'student_not_found') as displacement_info,
                        'Student or enrollment not found in database'::VARCHAR(500) as explanation,
                        0 as feasibility_score;
                    RETURN;
                END IF;
                
                -- PART 1: DIRECT PLACEMENTS - EXCLUDE current groups properly
                RETURN QUERY
                SELECT 
                    sg.time_slot_id::BIGINT as slot_id, 
                    sg.id::BIGINT as group_id,
                    sg.name::VARCHAR(100) as group_name, 
                    (u.first_name || ' ' || u.last_name)::VARCHAR(202) as coach_name,
                    CASE sg.day_of_week
                        WHEN 0 THEN 'Monday'::VARCHAR(10)
                        WHEN 1 THEN 'Tuesday'::VARCHAR(10)
                        WHEN 2 THEN 'Wednesday'::VARCHAR(10)
                        WHEN 3 THEN 'Thursday'::VARCHAR(10)
                        WHEN 4 THEN 'Friday'::VARCHAR(10)
                        ELSE 'Unknown'::VARCHAR(10)
                    END as day_name,
                    (ts.start_time || ' - ' || ts.end_time)::VARCHAR(50) as time_slot,
                    (
                        -- Base skill level compatibility (0-100 points)
                        CASE 
                            WHEN student_skill_level = sg.target_skill_level THEN 100
                            WHEN ABS(ASCII(student_skill_level) - ASCII(sg.target_skill_level)) = 1 THEN 70
                            WHEN ABS(ASCII(student_skill_level) - ASCII(sg.target_skill_level)) = 2 THEN 40
                            ELSE 10
                        END +
                        -- Group utilization bonus (PRIORITIZES GROUPS WITH 1 STUDENT!)
                        CASE 
                            WHEN COUNT(sgm.enrollment_id) = 1 THEN 200  -- HIGHEST PRIORITY: 1 student groups
                            WHEN COUNT(sgm.enrollment_id) = 2 THEN 150  -- HIGH: 2 student groups
                            WHEN COUNT(sgm.enrollment_id) = 3 THEN 100  -- GOOD: 3 student groups
                            WHEN COUNT(sgm.enrollment_id) = 0 THEN 50   -- LOWER: Empty groups
                            ELSE 20  -- LOWEST: Nearly full groups
                        END +
                        -- Different group bonus
                        CASE 
                            WHEN NOT (sg.id = ANY(COALESCE(student_current_group_ids, ARRAY[]::INTEGER[]))) THEN 30
                            ELSE 0
                        END +
                        -- Enrollment type perfect match bonus
                        CASE 
                            WHEN student_enrollment_type = 'INDIVIDUAL' AND COUNT(sgm.enrollment_id) = 0 THEN 100
                            WHEN student_enrollment_type != 'INDIVIDUAL' AND COUNT(sgm.enrollment_id) > 0 THEN 50
                            ELSE 0
                        END
                    )::INTEGER as compatibility_score,
                    'direct'::VARCHAR(20) as placement_type,
                    COUNT(sgm.enrollment_id)::INTEGER as current_size,
                    sg.max_capacity,
                    json_build_object(
                        'type', 'direct',
                        'current_members', COUNT(sgm.enrollment_id),
                        'available_space', sg.max_capacity - COUNT(sgm.enrollment_id),
                        'enrollment_type_match', student_enrollment_type,
                        'group_type', sg.group_type,
                        'excluded_current_groups', COALESCE(array_length(student_current_group_ids, 1), 0)
                    ) as displacement_info,
                    ('Direct placement - ' || COUNT(sgm.enrollment_id) || '/' || sg.max_capacity || ' students, ' ||
                     'enrollment type: ' || student_enrollment_type || ', ' ||
                     CASE 
                        WHEN COUNT(sgm.enrollment_id) = 1 THEN 'IDEAL: Join 1 student of same type'
                        WHEN COUNT(sgm.enrollment_id) = 2 THEN 'GREAT: Join 2 students of same type'
                        WHEN COUNT(sgm.enrollment_id) = 0 THEN 'Empty group available'
                        ELSE 'Group has ' || COUNT(sgm.enrollment_id) || ' students'
                     END || ', ' ||
                     CASE 
                        WHEN student_skill_level = sg.target_skill_level THEN 'perfect skill match'
                        ELSE 'skill level ' || sg.target_skill_level || ' group'
                     END)::VARCHAR(500) as explanation,
                    (
                        CASE 
                            WHEN student_skill_level = sg.target_skill_level THEN 100
                            WHEN ABS(ASCII(student_skill_level) - ASCII(sg.target_skill_level)) = 1 THEN 70
                            ELSE 40
                        END +
                        CASE 
                            WHEN COUNT(sgm.enrollment_id) = 1 THEN 200
                            WHEN COUNT(sgm.enrollment_id) = 2 THEN 150
                            ELSE 50
                        END
                    )::INTEGER as feasibility_score
                FROM scheduler_scheduledgroup sg
                JOIN scheduler_coach c ON sg.coach_id = c.id
                JOIN auth_user u ON c.user_id = u.id
                JOIN scheduler_timeslot ts ON sg.time_slot_id = ts.id
                LEFT JOIN scheduler_scheduledgroup_members sgm ON sg.id = sgm.scheduledgroup_id
                WHERE sg.term_id = student_term_id
                -- FIXED: Properly exclude ALL current groups
                AND NOT (sg.id = ANY(COALESCE(student_current_group_ids, ARRAY[]::INTEGER[])))
                -- Only available time slots
                AND NOT EXISTS (
                    SELECT 1 FROM scheduler_scheduledunavailability su
                    JOIN scheduler_scheduledunavailability_students sus ON su.id = sus.scheduledunavailability_id
                    WHERE sus.student_id = target_student_id
                    AND su.day_of_week = sg.day_of_week AND su.time_slot_id = sg.time_slot_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM scheduler_scheduledunavailability su
                    JOIN scheduler_scheduledunavailability_school_classes susc ON su.id = susc.scheduledunavailability_id
                    JOIN scheduler_student s ON s.school_class_id = susc.schoolclass_id
                    WHERE s.id = target_student_id
                    AND su.day_of_week = sg.day_of_week AND su.time_slot_id = sg.time_slot_id
                )
                GROUP BY sg.id, sg.name, sg.day_of_week, sg.time_slot_id, sg.max_capacity, 
                         sg.target_skill_level, sg.group_type, u.first_name, u.last_name, ts.start_time, ts.end_time, c.id
                HAVING COUNT(sgm.enrollment_id) < sg.max_capacity  -- Only groups with space
                ORDER BY compatibility_score DESC, current_size ASC, coach_name
                LIMIT max_results;
                
            END;
            $$;
            """,
            reverse_sql="DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);"
        ),
    ]
