from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0026_fix_availability_day_logic'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Drop ALL existing versions of the function to prevent conflicts
            DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);
            
            -- Create diagnostic version with detailed logging
            CREATE FUNCTION find_optimal_slots_advanced(
                target_student_id INTEGER,
                student_term_id INTEGER,
                include_displacements BOOLEAN DEFAULT FALSE,
                max_results INTEGER DEFAULT 10
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
                debug_msg TEXT;
                available_slots_count INTEGER;
                scheduled_groups_count INTEGER;
                compatible_scores_count INTEGER;
                direct_placements_count INTEGER;
            BEGIN
                -- DIAGNOSTIC: Log function start
                RAISE NOTICE 'DIAGNOSTIC: Starting slot finder for student % in term %', target_student_id, student_term_id;
                
                -- Get student details with diagnostics
                SELECT s.skill_level, e.enrollment_type 
                INTO student_skill_level, student_enrollment_type
                FROM scheduler_student s
                JOIN scheduler_enrollment e ON s.id = e.student_id
                WHERE s.id = target_student_id AND e.term_id = student_term_id;
                
                -- DIAGNOSTIC: Log student details
                IF student_skill_level IS NULL OR student_enrollment_type IS NULL THEN
                    RAISE NOTICE 'DIAGNOSTIC: ERROR - Student data not found! skill_level=%, enrollment_type=%', student_skill_level, student_enrollment_type;
                    RETURN;
                ELSE
                    RAISE NOTICE 'DIAGNOSTIC: Found student - skill_level=%, enrollment_type=%', student_skill_level, student_enrollment_type;
                END IF;
                
                -- DIAGNOSTIC: Check available time slots
                SELECT COUNT(*) INTO available_slots_count
                FROM (
                    SELECT DISTINCT ts.id as time_slot_id, day_num as day_of_week, day_num
                    FROM scheduler_timeslot ts
                    CROSS JOIN generate_series(0, 4) as day_num
                    WHERE NOT EXISTS (
                        SELECT 1 FROM scheduler_scheduledunavailability su
                        JOIN scheduler_scheduledunavailability_students sus ON su.id = sus.scheduledunavailability_id
                        WHERE sus.student_id = target_student_id
                        AND su.day_of_week = day_num
                        AND su.time_slot_id = ts.id
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM scheduler_scheduledunavailability su
                        JOIN scheduler_scheduledunavailability_school_classes susc ON su.id = susc.scheduledunavailability_id
                        JOIN scheduler_student s ON s.school_class_id = susc.schoolclass_id
                        WHERE s.id = target_student_id
                        AND su.day_of_week = day_num
                        AND su.time_slot_id = ts.id
                    )
                ) available_slots;
                
                RAISE NOTICE 'DIAGNOSTIC: Available time slots count: %', available_slots_count;
                
                -- DIAGNOSTIC: Check scheduled groups
                SELECT COUNT(*) INTO scheduled_groups_count
                FROM scheduler_scheduledgroup sg
                WHERE sg.term_id = student_term_id;
                
                RAISE NOTICE 'DIAGNOSTIC: Scheduled groups in term: %', scheduled_groups_count;
                
                -- Return optimized slot recommendations
                RETURN QUERY
                WITH 
                -- Student availability analysis
                student_availability AS (
                    SELECT DISTINCT
                        ts.id as time_slot_id,
                        day_num as day_of_week,
                        day_num
                    FROM scheduler_timeslot ts
                    CROSS JOIN generate_series(0, 4) as day_num
                    WHERE NOT EXISTS (
                        -- Check individual unavailability
                        SELECT 1 FROM scheduler_scheduledunavailability su
                        JOIN scheduler_scheduledunavailability_students sus ON su.id = sus.scheduledunavailability_id
                        WHERE sus.student_id = target_student_id
                        AND su.day_of_week = day_num
                        AND su.time_slot_id = ts.id
                    )
                    AND NOT EXISTS (
                        -- Check class unavailability
                        SELECT 1 FROM scheduler_scheduledunavailability su
                        JOIN scheduler_scheduledunavailability_school_classes susc ON su.id = susc.scheduledunavailability_id
                        JOIN scheduler_student s ON s.school_class_id = susc.schoolclass_id
                        WHERE s.id = target_student_id
                        AND su.day_of_week = day_num
                        AND su.time_slot_id = ts.id
                    )
                ),
                
                -- Dynamic group analysis with current composition
                group_analysis AS (
                    SELECT 
                        sg.id as group_id,
                        sg.name as group_name,
                        sg.day_of_week,
                        sg.time_slot_id,
                        sg.max_capacity,
                        sg.target_skill_level,
                        c.user_id,
                        -- Cast concatenated coach name to VARCHAR(202)
                        (u.first_name || ' ' || u.last_name)::VARCHAR(202) as coach_name,
                        -- Cast COUNT to INTEGER to match return type
                        COUNT(sgm.enrollment_id)::INTEGER as current_size,
                        
                        -- Dynamic group type detection based on current members
                        CASE 
                            WHEN COUNT(sgm.enrollment_id) = 0 THEN 'EMPTY'
                            WHEN COUNT(sgm.enrollment_id) = 1 THEN 
                                (SELECT e.enrollment_type FROM scheduler_enrollment e 
                                 WHERE e.id = MIN(sgm.enrollment_id))
                            ELSE
                                CASE 
                                    WHEN COUNT(DISTINCT e.enrollment_type) = 1 THEN
                                        (SELECT DISTINCT e.enrollment_type FROM scheduler_enrollment e 
                                         JOIN scheduler_scheduledgroup_members sgm2 ON e.id = sgm2.enrollment_id
                                         WHERE sgm2.scheduledgroup_id = sg.id)
                                    ELSE 'MIXED'
                                END
                        END as effective_group_type,
                        
                        -- Current members info for displacement analysis
                        COALESCE(
                            json_agg(
                                json_build_object(
                                    'student_id', s.id,
                                    'student_name', s.first_name || ' ' || s.last_name,
                                    'enrollment_type', e.enrollment_type,
                                    'skill_level', s.skill_level,
                                    'year_level', s.year_level
                                ) ORDER BY s.last_name
                            ) FILTER (WHERE s.id IS NOT NULL),
                            '[]'::json
                        ) as current_members
                        
                    FROM scheduler_scheduledgroup sg
                    JOIN scheduler_coach c ON sg.coach_id = c.id
                    JOIN auth_user u ON c.user_id = u.id
                    LEFT JOIN scheduler_scheduledgroup_members sgm ON sg.id = sgm.scheduledgroup_id
                    LEFT JOIN scheduler_enrollment e ON sgm.enrollment_id = e.id
                    LEFT JOIN scheduler_student s ON e.student_id = s.id
                    WHERE sg.term_id = student_term_id
                    GROUP BY sg.id, sg.name, sg.day_of_week, sg.time_slot_id, sg.max_capacity, 
                             sg.target_skill_level, c.user_id, u.first_name, u.last_name
                ),
                
                -- Compatibility scoring
                compatibility_scores AS (
                    SELECT 
                        ga.*,
                        (ts.start_time || ' - ' || ts.end_time)::VARCHAR(50) as time_display,
                        CASE ga.day_of_week
                            WHEN 0 THEN 'Monday'::VARCHAR(10)
                            WHEN 1 THEN 'Tuesday'::VARCHAR(10)
                            WHEN 2 THEN 'Wednesday'::VARCHAR(10)
                            WHEN 3 THEN 'Thursday'::VARCHAR(10)
                            WHEN 4 THEN 'Friday'::VARCHAR(10)
                            ELSE 'Unknown'::VARCHAR(10)
                        END as day_name,
                        
                        -- Comprehensive compatibility scoring (0-370 points) - CAST TO INTEGER
                        (
                            -- Skill level compatibility (0-100)
                            CASE 
                                WHEN student_skill_level = ga.target_skill_level THEN 100
                                WHEN ABS(ASCII(student_skill_level) - ASCII(ga.target_skill_level)) = 1 THEN 60
                                ELSE 0
                            END +
                            
                            -- Group type compatibility (0-80)
                            CASE 
                                WHEN ga.effective_group_type = 'EMPTY' THEN 80
                                WHEN ga.effective_group_type = student_enrollment_type THEN 80
                                WHEN student_enrollment_type = 'GROUP' AND ga.effective_group_type = 'PAIR' THEN 40
                                WHEN ga.effective_group_type = 'MIXED' THEN 20
                                ELSE 0
                            END +
                            
                            -- Capacity optimization (0-50)
                            CASE 
                                WHEN ga.current_size < ga.max_capacity THEN 
                                    50 - (ga.current_size * 10)
                                ELSE 0
                            END +
                            
                            -- Group size preference (0-50)
                            CASE student_enrollment_type
                                WHEN 'SOLO' THEN 
                                    CASE WHEN ga.current_size = 0 THEN 50 ELSE 0 END
                                WHEN 'PAIR' THEN
                                    CASE 
                                        WHEN ga.current_size = 0 THEN 40
                                        WHEN ga.current_size = 1 AND ga.effective_group_type = 'PAIR' THEN 50
                                        ELSE 0
                                    END
                                WHEN 'GROUP' THEN
                                    CASE 
                                        WHEN ga.current_size BETWEEN 1 AND 2 THEN 50
                                        WHEN ga.current_size = 0 THEN 30
                                        ELSE 20
                                    END
                                ELSE 0
                            END +
                            
                            -- Lesson balance priority (0-40) - placeholder for now
                            30 +
                            
                            -- Coach specialization (0-50) - placeholder for now  
                            40 +
                            
                            -- Time preference (0-20) - placeholder for now
                            15
                        )::INTEGER as compatibility_score
                        
                    FROM group_analysis ga
                    JOIN scheduler_timeslot ts ON ga.time_slot_id = ts.id
                    JOIN student_availability sa ON sa.time_slot_id = ga.time_slot_id 
                                                 AND sa.day_num = ga.day_of_week
                ),
                
                -- Direct placement opportunities
                direct_placements AS (
                    SELECT 
                        cs.*,
                        'direct'::VARCHAR(20) as placement_type,
                        json_build_object(
                            'type', 'direct',
                            'explanation', 'Direct placement in available slot',
                            'complexity', 1
                        ) as displacement_info,
                        cs.compatibility_score::INTEGER as feasibility_score,
                        ('Direct placement - ' || 
                        CASE 
                            WHEN cs.current_size = 0 THEN 'empty slot'
                            WHEN cs.effective_group_type = student_enrollment_type THEN 
                                'join ' || cs.current_size || ' compatible student(s)'
                            ELSE 'available space'
                        END)::VARCHAR(500) as explanation
                    FROM compatibility_scores cs
                    WHERE cs.current_size < cs.max_capacity
                    AND (
                        cs.effective_group_type = 'EMPTY' OR
                        cs.effective_group_type = student_enrollment_type OR
                        (student_enrollment_type = 'GROUP' AND cs.effective_group_type = 'PAIR')
                    )
                ),
                
                -- Displacement opportunities (if enabled)
                displacement_opportunities AS (
                    SELECT 
                        cs.*,
                        'displacement'::VARCHAR(20) as placement_type,
                        json_build_object(
                            'type', 'displacement',
                            'displaced_students', cs.current_members,
                            'explanation', 'Displace ' || cs.current_size || ' student(s) to create optimal placement',
                            'complexity', cs.current_size + 1
                        ) as displacement_info,
                        -- Reduce feasibility score for displacements - CAST TO INTEGER
                        GREATEST(cs.compatibility_score - (cs.current_size * 20), 0)::INTEGER as feasibility_score,
                        ('Displacement - move ' || cs.current_size || ' student(s) to optimize placement')::VARCHAR(500) as explanation
                    FROM compatibility_scores cs
                    WHERE include_displacements = TRUE
                    AND cs.current_size > 0
                    AND cs.current_size <= 3  -- Limit displacement complexity
                    AND cs.compatibility_score > 200  -- Only suggest high-compatibility displacements
                ),
                
                -- Combined results
                all_opportunities AS (
                    SELECT * FROM direct_placements
                    UNION ALL
                    SELECT * FROM displacement_opportunities
                )
                
                -- Final selection and ranking
                SELECT 
                    ao.time_slot_id::BIGINT as slot_id,
                    ao.group_id::BIGINT,
                    ao.group_name::VARCHAR(100),
                    ao.coach_name::VARCHAR(202),
                    ao.day_name::VARCHAR(10),
                    ao.time_display::VARCHAR(50) as time_slot,
                    ao.compatibility_score,
                    ao.placement_type::VARCHAR(20),
                    ao.current_size,
                    ao.max_capacity,
                    ao.displacement_info,
                    ao.explanation::VARCHAR(500),
                    ao.feasibility_score
                FROM all_opportunities ao
                ORDER BY 
                    -- Prioritize direct placements, then by feasibility score
                    CASE WHEN ao.placement_type = 'direct' THEN 0 ELSE 1 END,
                    ao.feasibility_score DESC,
                    ao.compatibility_score DESC
                LIMIT max_results;
                
                -- DIAGNOSTIC: Log final counts after query execution
                GET DIAGNOSTICS compatible_scores_count = ROW_COUNT;
                RAISE NOTICE 'DIAGNOSTIC: Final results returned: %', compatible_scores_count;
                
            END;
            $$;
            """,
            reverse_sql="DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);"
        ),
    ]
