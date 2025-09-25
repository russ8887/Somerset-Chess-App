from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0033_enhanced_group_member_details'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Drop the previous function and create the corrected version with proper PAIR constraints
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
                
                -- PART 1: DIRECT PLACEMENTS with STRICT PAIR CONSTRAINTS
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
                        -- Get current group members with details
                        (
                            SELECT COALESCE(json_agg(
                                json_build_object(
                                    'student_id', s2.id,
                                    'student_name', s2.first_name || ' ' || s2.last_name,
                                    'skill_level', s2.skill_level,
                                    'enrollment_type', e2.enrollment_type,
                                    'school_class', COALESCE(sc.name, 'Unknown')
                                )
                            ), '[]'::json)
                            FROM scheduler_scheduledgroup_members sgm2
                            JOIN scheduler_enrollment e2 ON sgm2.enrollment_id = e2.id
                            JOIN scheduler_student s2 ON e2.student_id = s2.id
                            LEFT JOIN scheduler_schoolclass sc ON s2.school_class_id = sc.id
                            WHERE sgm2.scheduledgroup_id = sg.id
                        ) as current_members,
                        -- CRITICAL: Check enrollment type compatibility with STRICT PAIR constraints
                        CASE 
                            WHEN student_enrollment_type = 'PAIR' THEN
                                -- PAIR students can ONLY join groups with exactly 1 other PAIR student
                                CASE 
                                    WHEN COUNT(sgm.enrollment_id) = 1 THEN
                                        -- Check if the 1 existing student is also PAIR type
                                        (SELECT COUNT(*) = 1 FROM scheduler_scheduledgroup_members sgm3
                                         JOIN scheduler_enrollment e3 ON sgm3.enrollment_id = e3.id
                                         WHERE sgm3.scheduledgroup_id = sg.id AND e3.enrollment_type = 'PAIR')
                                    ELSE FALSE  -- PAIR students cannot join empty groups or groups with 2+ students
                                END
                            WHEN student_enrollment_type = 'GROUP' THEN
                                -- GROUP students can join PAIR or GROUP groups with space
                                CASE 
                                    WHEN COUNT(sgm.enrollment_id) = 0 THEN TRUE  -- Empty group is fine
                                    WHEN COUNT(sgm.enrollment_id) < sg.max_capacity THEN
                                        -- Check if existing students are compatible (PAIR or GROUP)
                                        NOT EXISTS (
                                            SELECT 1 FROM scheduler_scheduledgroup_members sgm2
                                            JOIN scheduler_enrollment e2 ON sgm2.enrollment_id = e2.id
                                            WHERE sgm2.scheduledgroup_id = sg.id
                                            AND e2.enrollment_type NOT IN ('PAIR', 'GROUP')
                                        )
                                    ELSE FALSE
                                END
                            WHEN student_enrollment_type = 'SOLO' THEN
                                -- SOLO students need dedicated empty groups
                                COUNT(sgm.enrollment_id) = 0
                            ELSE FALSE
                        END as enrollment_compatible,
                        -- OPTIMAL SCORING ALGORITHM with PAIR-specific logic
                        (
                            -- Base skill level compatibility (0-100 points)
                            CASE 
                                WHEN student_skill_level = sg.target_skill_level THEN 100
                                WHEN ABS(ASCII(student_skill_level) - ASCII(sg.target_skill_level)) = 1 THEN 70
                                WHEN ABS(ASCII(student_skill_level) - ASCII(sg.target_skill_level)) = 2 THEN 40
                                ELSE 10
                            END +
                            -- Group utilization bonus with PAIR-specific scoring
                            CASE 
                                WHEN student_enrollment_type = 'PAIR' THEN
                                    CASE 
                                        WHEN COUNT(sgm.enrollment_id) = 1 THEN 200  -- PERFECT: 1 PAIR student waiting
                                        ELSE 0  -- PAIR students get 0 points for any other group size
                                    END
                                WHEN student_enrollment_type = 'GROUP' THEN
                                    CASE 
                                        WHEN COUNT(sgm.enrollment_id) = 1 THEN 150  -- Good: 1 student groups
                                        WHEN COUNT(sgm.enrollment_id) = 2 THEN 100  -- OK: 2 student groups
                                        WHEN COUNT(sgm.enrollment_id) = 0 THEN 50   -- Lower: Empty groups
                                        ELSE 20  -- Lowest: Nearly full groups
                                    END
                                WHEN student_enrollment_type = 'SOLO' THEN
                                    CASE 
                                        WHEN COUNT(sgm.enrollment_id) = 0 THEN 200  -- PERFECT: Empty group for SOLO
                                        ELSE 0  -- SOLO students get 0 points for non-empty groups
                                    END
                                ELSE 0
                            END +
                            -- Different group bonus
                            CASE 
                                WHEN sg.id != COALESCE(student_current_group_id, -1) THEN 30
                                ELSE 0
                            END +
                            -- Coach diversity bonus
                            CASE 
                                WHEN ROW_NUMBER() OVER (PARTITION BY c.id ORDER BY sg.id) = 1 THEN 25
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
                    AND sg.id != COALESCE(student_current_group_id, -1)
                    GROUP BY sg.id, sg.name, sg.day_of_week, sg.time_slot_id, sg.max_capacity, 
                             sg.target_skill_level, sg.group_type, u.first_name, u.last_name, ts.start_time, ts.end_time, c.id
                    -- CRITICAL: Only show groups that meet the enrollment compatibility requirements
                    HAVING 
                        CASE 
                            WHEN student_enrollment_type = 'PAIR' THEN
                                COUNT(sgm.enrollment_id) = 1 AND
                                (SELECT COUNT(*) FROM scheduler_scheduledgroup_members sgm3
                                 JOIN scheduler_enrollment e3 ON sgm3.enrollment_id = e3.id
                                 WHERE sgm3.scheduledgroup_id = sg.id AND e3.enrollment_type = 'PAIR') = 1
                            WHEN student_enrollment_type = 'GROUP' THEN
                                COUNT(sgm.enrollment_id) < sg.max_capacity
                            WHEN student_enrollment_type = 'SOLO' THEN
                                COUNT(sgm.enrollment_id) = 0
                            ELSE FALSE
                        END
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
                        'current_students', cg.current_members,
                        'pairing_info', CASE 
                            WHEN student_enrollment_type = 'PAIR' AND cg.current_size = 1 THEN 
                                'You will join 1 student: ' || (cg.current_members->0->>'student_name') || ' (' || (cg.current_members->0->>'skill_level') || ', ' || (cg.current_members->0->>'enrollment_type') || ') - Perfect PAIR match!'
                            WHEN student_enrollment_type = 'SOLO' AND cg.current_size = 0 THEN 
                                'You will have this group to yourself - Perfect SOLO match!'
                            WHEN student_enrollment_type = 'GROUP' THEN
                                CASE 
                                    WHEN cg.current_size = 0 THEN 'You will be the first student in this group'
                                    WHEN cg.current_size = 1 THEN 'You will join 1 student: ' || (cg.current_members->0->>'student_name') || ' (' || (cg.current_members->0->>'skill_level') || ', ' || (cg.current_members->0->>'enrollment_type') || ')'
                                    WHEN cg.current_size = 2 THEN 'You will join 2 students: ' || (cg.current_members->0->>'student_name') || ' and ' || (cg.current_members->1->>'student_name')
                                    ELSE 'You will join ' || cg.current_size || ' students in this group'
                                END
                            ELSE 'Group placement available'
                        END,
                        'priority_reason', CASE 
                            WHEN student_enrollment_type = 'PAIR' AND cg.current_size = 1 THEN 'PERFECT: Exactly 1 PAIR student waiting for partner'
                            WHEN student_enrollment_type = 'SOLO' AND cg.current_size = 0 THEN 'PERFECT: Empty group for SOLO student'
                            WHEN student_enrollment_type = 'GROUP' AND cg.current_size = 1 THEN 'EXCELLENT: Group with 1 student'
                            WHEN student_enrollment_type = 'GROUP' AND cg.current_size = 2 THEN 'GOOD: Group with 2 students'
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
                        WHEN student_enrollment_type = 'PAIR' AND cg.current_size = 1 THEN 
                            'PERFECT PAIR: Join ' || (cg.current_members->0->>'student_name') || ' (' || (cg.current_members->0->>'skill_level') || ', ' || (cg.current_members->0->>'enrollment_type') || ')'
                        WHEN student_enrollment_type = 'SOLO' AND cg.current_size = 0 THEN 
                            'PERFECT SOLO: Dedicated individual group'
                        WHEN student_enrollment_type = 'GROUP' AND cg.current_size = 1 THEN 
                            'GREAT: Join 1 student of compatible type'
                        WHEN student_enrollment_type = 'GROUP' AND cg.current_size = 2 THEN 
                            'GOOD: Join 2 students of compatible type'
                        WHEN cg.current_size = 0 THEN 'Empty group available'
                        ELSE 'Group has ' || cg.current_size || ' students'
                     END || ', ' ||
                     CASE 
                        WHEN student_skill_level = cg.target_skill_level THEN 'perfect skill match'
                        ELSE 'skill level ' || cg.target_skill_level || ' group'
                     END)::VARCHAR(500) as explanation,
                    cg.compatibility_score as feasibility_score
                FROM compatible_groups cg
                WHERE cg.enrollment_compatible = TRUE
                ORDER BY cg.compatibility_score DESC, cg.current_size ASC, cg.coach_name
                LIMIT max_results;
                
            END;
            $$;
            """,
            reverse_sql="DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);"
        ),
    ]
