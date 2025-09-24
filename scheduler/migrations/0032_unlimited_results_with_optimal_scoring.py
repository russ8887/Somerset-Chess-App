from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0031_fix_enrollment_type_compatibility'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Drop the previous function and create the unlimited, optimally-scored slot finder
            DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);
            
            CREATE FUNCTION find_optimal_slots_advanced(
                target_student_id INTEGER,
                student_term_id INTEGER,
                include_displacements BOOLEAN DEFAULT TRUE,
                max_results INTEGER DEFAULT 999  -- Effectively unlimited
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
                student_current_group_id BIGINT;
                result_count INTEGER := 0;
            BEGIN
                -- Get student details and current group
                SELECT s.skill_level, e.enrollment_type, sgm.scheduledgroup_id
                INTO student_skill_level, student_enrollment_type, student_current_group_id
                FROM scheduler_student s
                JOIN scheduler_enrollment e ON s.id = e.student_id
                LEFT JOIN scheduler_scheduledgroup_members sgm ON e.id = sgm.enrollment_id
                LEFT JOIN scheduler_scheduledgroup sg ON sgm.scheduledgroup_id = sg.id AND sg.term_id = student_term_id
                WHERE s.id = target_student_id AND e.term_id = student_term_id;
                
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
                
                -- PART 1: DIRECT PLACEMENTS (ALL compatible groups with space, optimally scored)
                RETURN QUERY
                WITH 
                student_availability AS (
                    SELECT DISTINCT ts.id as time_slot_id, day_num as day_of_week
                    FROM scheduler_timeslot ts
                    CROSS JOIN generate_series(0, 4) as day_num
                    WHERE NOT EXISTS (
                        SELECT 1 FROM scheduler_scheduledunavailability su
                        JOIN scheduler_scheduledunavailability_students sus ON su.id = sus.scheduledunavailability_id
                        WHERE sus.student_id = target_student_id
                        AND su.day_of_week = day_num AND su.time_slot_id = ts.id
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM scheduler_scheduledunavailability su
                        JOIN scheduler_scheduledunavailability_school_classes susc ON su.id = susc.scheduledunavailability_id
                        JOIN scheduler_student s ON s.school_class_id = susc.schoolclass_id
                        WHERE s.id = target_student_id
                        AND su.day_of_week = day_num AND su.time_slot_id = ts.id
                    )
                ),
                compatible_groups AS (
                    SELECT 
                        sg.id as group_id, sg.name as group_name, sg.day_of_week, sg.time_slot_id,
                        sg.max_capacity, sg.target_skill_level, sg.group_type,
                        (u.first_name || ' ' || u.last_name)::VARCHAR(202) as coach_name,
                        COUNT(sgm.enrollment_id)::INTEGER as current_size,
                        (ts.start_time || ' - ' || ts.end_time)::VARCHAR(50) as time_display,
                        CASE sg.day_of_week
                            WHEN 0 THEN 'Monday'::VARCHAR(10)
                            WHEN 1 THEN 'Tuesday'::VARCHAR(10)
                            WHEN 2 THEN 'Wednesday'::VARCHAR(10)
                            WHEN 3 THEN 'Thursday'::VARCHAR(10)
                            WHEN 4 THEN 'Friday'::VARCHAR(10)
                            ELSE 'Unknown'::VARCHAR(10)
                        END as day_name,
                        -- Check enrollment type compatibility of existing members
                        CASE 
                            WHEN COUNT(sgm.enrollment_id) = 0 THEN TRUE  -- Empty group is compatible
                            WHEN student_enrollment_type = 'INDIVIDUAL' THEN FALSE  -- Individual students need dedicated slots
                            ELSE 
                                -- Check if all existing members have compatible enrollment type
                                NOT EXISTS (
                                    SELECT 1 FROM scheduler_scheduledgroup_members sgm2
                                    JOIN scheduler_enrollment e2 ON sgm2.enrollment_id = e2.id
                                    WHERE sgm2.scheduledgroup_id = sg.id
                                    AND e2.enrollment_type != student_enrollment_type
                                )
                        END as enrollment_compatible,
                        -- OPTIMAL SCORING ALGORITHM - prioritizes logical choices
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
                                WHEN sg.id != COALESCE(student_current_group_id, -1) THEN 30
                                ELSE 0
                            END +
                            -- Enrollment type perfect match bonus
                            CASE 
                                WHEN student_enrollment_type = 'INDIVIDUAL' AND COUNT(sgm.enrollment_id) = 0 THEN 100
                                WHEN student_enrollment_type != 'INDIVIDUAL' AND COUNT(sgm.enrollment_id) > 0 THEN 50
                                ELSE 0
                            END +
                            -- Coach diversity bonus (spread across different coaches)
                            CASE 
                                WHEN ROW_NUMBER() OVER (PARTITION BY c.id ORDER BY sg.id) = 1 THEN 25  -- First group per coach gets bonus
                                ELSE 0
                            END
                        )::INTEGER as compatibility_score
                    FROM scheduler_scheduledgroup sg
                    JOIN scheduler_coach c ON sg.coach_id = c.id
                    JOIN auth_user u ON c.user_id = u.id
                    JOIN scheduler_timeslot ts ON sg.time_slot_id = ts.id
                    LEFT JOIN scheduler_scheduledgroup_members sgm ON sg.id = sgm.scheduledgroup_id
                    JOIN student_availability sa ON sa.time_slot_id = sg.time_slot_id 
                                                 AND sa.day_of_week = sg.day_of_week
                    WHERE sg.term_id = student_term_id
                    AND sg.id != COALESCE(student_current_group_id, -1)  -- Exclude current group
                    GROUP BY sg.id, sg.name, sg.day_of_week, sg.time_slot_id, sg.max_capacity, 
                             sg.target_skill_level, sg.group_type, u.first_name, u.last_name, ts.start_time, ts.end_time, c.id
                    HAVING COUNT(sgm.enrollment_id) < sg.max_capacity  -- Only groups with space
                )
                SELECT 
                    cg.time_slot_id::BIGINT as slot_id, cg.group_id::BIGINT,
                    cg.group_name::VARCHAR(100), cg.coach_name::VARCHAR(202),
                    cg.day_name::VARCHAR(10), cg.time_display::VARCHAR(50) as time_slot,
                    cg.compatibility_score, 'direct'::VARCHAR(20) as placement_type,
                    cg.current_size, cg.max_capacity,
                    json_build_object(
                        'type', 'direct',
                        'current_members', cg.current_size,
                        'available_space', cg.max_capacity - cg.current_size,
                        'enrollment_type_match', student_enrollment_type,
                        'group_type', cg.group_type,
                        'priority_reason', CASE 
                            WHEN cg.current_size = 1 THEN 'PERFECT: Group with 1 student of same type'
                            WHEN cg.current_size = 2 THEN 'EXCELLENT: Group with 2 students of same type'
                            WHEN cg.current_size = 3 THEN 'GOOD: Group with 3 students of same type'
                            WHEN cg.current_size = 0 THEN 'ACCEPTABLE: Empty group'
                            ELSE 'AVAILABLE: Group with space'
                        END,
                        'skill_match', CASE 
                            WHEN student_skill_level = cg.target_skill_level THEN 'perfect'
                            WHEN ABS(ASCII(student_skill_level) - ASCII(cg.target_skill_level)) = 1 THEN 'good'
                            ELSE 'acceptable'
                        END,
                        'enrollment_compatible', cg.enrollment_compatible
                    ) as displacement_info,
                    ('Direct placement - ' || cg.current_size || '/' || cg.max_capacity || ' students, ' ||
                     'enrollment type: ' || student_enrollment_type || ', ' ||
                     CASE 
                        WHEN cg.current_size = 1 THEN 'IDEAL: Join 1 student of same type'
                        WHEN cg.current_size = 2 THEN 'GREAT: Join 2 students of same type'
                        WHEN cg.current_size = 0 THEN 'Empty group available'
                        ELSE 'Group has ' || cg.current_size || ' students'
                     END || ', ' ||
                     CASE 
                        WHEN student_skill_level = cg.target_skill_level THEN 'perfect skill match'
                        ELSE 'skill level ' || cg.target_skill_level || ' group'
                     END)::VARCHAR(500) as explanation,
                    cg.compatibility_score as feasibility_score
                FROM compatible_groups cg
                WHERE cg.enrollment_compatible = TRUE  -- CRITICAL: Only show compatible enrollment types
                ORDER BY cg.compatibility_score DESC, cg.current_size ASC, cg.coach_name  -- Best matches first, then by group size, then coach diversity
                LIMIT max_results;  -- Now effectively unlimited (999)
                
                GET DIAGNOSTICS result_count = ROW_COUNT;
                
                -- PART 2: DISPLACEMENT PLACEMENTS (if requested and we want more options)
                IF include_displacements AND result_count < max_results THEN
                    RETURN QUERY
                    WITH 
                    student_availability AS (
                        SELECT DISTINCT ts.id as time_slot_id, day_num as day_of_week
                        FROM scheduler_timeslot ts
                        CROSS JOIN generate_series(0, 4) as day_num
                        WHERE NOT EXISTS (
                            SELECT 1 FROM scheduler_scheduledunavailability su
                            JOIN scheduler_scheduledunavailability_students sus ON su.id = sus.scheduledunavailability_id
                            WHERE sus.student_id = target_student_id
                            AND su.day_of_week = day_num AND su.time_slot_id = ts.id
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM scheduler_scheduledunavailability su
                            JOIN scheduler_scheduledunavailability_school_classes susc ON su.id = susc.scheduledunavailability_id
                            JOIN scheduler_student s ON s.school_class_id = susc.schoolclass_id
                            WHERE s.id = target_student_id
                            AND su.day_of_week = day_num AND su.time_slot_id = ts.id
                        )
                    ),
                    full_compatible_groups AS (
                        SELECT 
                            sg.id as group_id, sg.name as group_name, sg.day_of_week, sg.time_slot_id,
                            sg.max_capacity, sg.target_skill_level, sg.group_type,
                            (u.first_name || ' ' || u.last_name)::VARCHAR(202) as coach_name,
                            COUNT(sgm.enrollment_id)::INTEGER as current_size,
                            (ts.start_time || ' - ' || ts.end_time)::VARCHAR(50) as time_display,
                            CASE sg.day_of_week
                                WHEN 0 THEN 'Monday'::VARCHAR(10)
                                WHEN 1 THEN 'Tuesday'::VARCHAR(10)
                                WHEN 2 THEN 'Wednesday'::VARCHAR(10)
                                WHEN 3 THEN 'Thursday'::VARCHAR(10)
                                WHEN 4 THEN 'Friday'::VARCHAR(10)
                                ELSE 'Unknown'::VARCHAR(10)
                            END as day_name,
                            -- Check if group has compatible enrollment types
                            CASE 
                                WHEN student_enrollment_type = 'INDIVIDUAL' THEN FALSE  -- Individual can't displace
                                ELSE 
                                    -- Check if all existing members have compatible enrollment type
                                    NOT EXISTS (
                                        SELECT 1 FROM scheduler_scheduledgroup_members sgm2
                                        JOIN scheduler_enrollment e2 ON sgm2.enrollment_id = e2.id
                                        WHERE sgm2.scheduledgroup_id = sg.id
                                        AND e2.enrollment_type != student_enrollment_type
                                    )
                            END as enrollment_compatible,
                            -- Find the least compatible student for displacement (same enrollment type only)
                            (
                                SELECT json_build_object(
                                    'student_id', s2.id,
                                    'student_name', s2.first_name || ' ' || s2.last_name,
                                    'skill_level', s2.skill_level,
                                    'enrollment_type', e2.enrollment_type,
                                    'compatibility_score', 
                                    CASE 
                                        WHEN s2.skill_level = sg.target_skill_level THEN 100
                                        WHEN ABS(ASCII(s2.skill_level) - ASCII(sg.target_skill_level)) = 1 THEN 70
                                        ELSE 40
                                    END
                                )
                                FROM scheduler_scheduledgroup_members sgm2
                                JOIN scheduler_enrollment e2 ON sgm2.enrollment_id = e2.id
                                JOIN scheduler_student s2 ON e2.student_id = s2.id
                                WHERE sgm2.scheduledgroup_id = sg.id
                                AND e2.enrollment_type = student_enrollment_type  -- CRITICAL: Same enrollment type only
                                ORDER BY 
                                    CASE 
                                        WHEN s2.skill_level = sg.target_skill_level THEN 100
                                        WHEN ABS(ASCII(s2.skill_level) - ASCII(sg.target_skill_level)) = 1 THEN 70
                                        ELSE 40
                                    END ASC
                                LIMIT 1
                            ) as displacement_candidate,
                            -- Target student compatibility with this group
                            (
                                CASE 
                                    WHEN student_skill_level = sg.target_skill_level THEN 100
                                    WHEN ABS(ASCII(student_skill_level) - ASCII(sg.target_skill_level)) = 1 THEN 70
                                    WHEN ABS(ASCII(student_skill_level) - ASCII(sg.target_skill_level)) = 2 THEN 40
                                    ELSE 10
                                END +
                                CASE 
                                    WHEN sg.id != COALESCE(student_current_group_id, -1) THEN 30  -- Different group bonus
                                    ELSE 0
                                END +
                                20  -- Displacement bonus for better fit
                            )::INTEGER as target_compatibility_score
                        FROM scheduler_scheduledgroup sg
                        JOIN scheduler_coach c ON sg.coach_id = c.id
                        JOIN auth_user u ON c.user_id = u.id
                        JOIN scheduler_timeslot ts ON sg.time_slot_id = ts.id
                        LEFT JOIN scheduler_scheduledgroup_members sgm ON sg.id = sgm.scheduledgroup_id
                        JOIN student_availability sa ON sa.time_slot_id = sg.time_slot_id 
                                                     AND sa.day_of_week = sg.day_of_week
                        WHERE sg.term_id = student_term_id
                        AND sg.id != COALESCE(student_current_group_id, -1)  -- Exclude current group
                        GROUP BY sg.id, sg.name, sg.day_of_week, sg.time_slot_id, sg.max_capacity, 
                                 sg.target_skill_level, sg.group_type, u.first_name, u.last_name, ts.start_time, ts.end_time
                        HAVING COUNT(sgm.enrollment_id) = sg.max_capacity  -- Only full groups
                    )
                    SELECT 
                        fg.time_slot_id::BIGINT as slot_id, fg.group_id::BIGINT,
                        fg.group_name::VARCHAR(100), fg.coach_name::VARCHAR(202),
                        fg.day_name::VARCHAR(10), fg.time_display::VARCHAR(50) as time_slot,
                        fg.target_compatibility_score as compatibility_score,
                        'displacement'::VARCHAR(20) as placement_type,
                        fg.current_size, fg.max_capacity,
                        json_build_object(
                            'type', 'displacement',
                            'displaced_student', fg.displacement_candidate,
                            'complexity', 1,
                            'enrollment_type_match', student_enrollment_type,
                            'group_type', fg.group_type,
                            'skill_improvement', 
                            CASE 
                                WHEN student_skill_level = fg.target_skill_level THEN 'perfect_match'
                                WHEN ABS(ASCII(student_skill_level) - ASCII(fg.target_skill_level)) = 1 THEN 'good_match'
                                ELSE 'acceptable_match'
                            END,
                            'enrollment_compatible', fg.enrollment_compatible
                        ) as displacement_info,
                        ('Displacement - move ' || (fg.displacement_candidate->>'student_name') || 
                         ' (' || (fg.displacement_candidate->>'enrollment_type') || ') to make room, ' ||
                         'enrollment type: ' || student_enrollment_type || ', skill match: ' ||
                         CASE 
                            WHEN student_skill_level = fg.target_skill_level THEN 'perfect'
                            ELSE 'level ' || fg.target_skill_level
                         END)::VARCHAR(500) as explanation,
                        -- Feasibility score considers both target fit and displacement impact
                        (fg.target_compatibility_score - 
                         COALESCE((fg.displacement_candidate->>'compatibility_score')::INTEGER, 0) / 3)::INTEGER as feasibility_score
                    FROM full_compatible_groups fg
                    WHERE fg.displacement_candidate IS NOT NULL
                    AND fg.enrollment_compatible = TRUE  -- CRITICAL: Only compatible enrollment types
                    -- LOWERED threshold for displacement (was +10, now +5)
                    AND fg.target_compatibility_score > COALESCE((fg.displacement_candidate->>'compatibility_score')::INTEGER, 0) + 5
                    ORDER BY feasibility_score DESC, fg.coach_name  -- Add coach diversity
                    LIMIT GREATEST(max_results - result_count, 1);
                END IF;
                
            END;
            $$;
            """,
            reverse_sql="DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);"
        ),
    ]
