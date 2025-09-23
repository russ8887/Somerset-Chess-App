from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0016_fix_group_capacity_values'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Advanced Slot Finder PostgreSQL Function
            -- This function provides comprehensive slot optimization with displacement scenarios
            
            CREATE OR REPLACE FUNCTION find_optimal_slots_advanced(
                target_student_id INTEGER,
                max_results INTEGER DEFAULT 10,
                include_displacements BOOLEAN DEFAULT TRUE
            ) RETURNS TABLE (
                slot_id INTEGER,
                group_id INTEGER,
                group_name TEXT,
                coach_name TEXT,
                day_name TEXT,
                time_slot TEXT,
                compatibility_score INTEGER,
                placement_type TEXT,
                current_size INTEGER,
                max_capacity INTEGER,
                displacement_info JSONB,
                explanation TEXT,
                feasibility_score INTEGER
            ) AS $$
            DECLARE
                target_enrollment_type TEXT;
                target_term_id INTEGER;
                target_skill_level CHAR(1);
                target_year_level INTEGER;
            BEGIN
                -- Get target student information
                SELECT e.enrollment_type, e.term_id, s.skill_level, s.year_level
                INTO target_enrollment_type, target_term_id, target_skill_level, target_year_level
                FROM scheduler_enrollment e
                JOIN scheduler_student s ON e.student_id = s.id
                WHERE e.student_id = target_student_id
                AND e.term_id = (SELECT id FROM scheduler_term WHERE is_active = TRUE LIMIT 1);
                
                IF target_enrollment_type IS NULL THEN
                    RAISE EXCEPTION 'Student % not found or not enrolled in active term', target_student_id;
                END IF;
                
                -- Return comprehensive slot analysis
                RETURN QUERY
                WITH 
                -- Student availability analysis
                student_availability AS (
                    SELECT DISTINCT
                        ts.id as time_slot_id,
                        EXTRACT(DOW FROM CURRENT_DATE + (day_num || ' days')::INTERVAL) as day_of_week,
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
                        u.first_name || ' ' || u.last_name as coach_name,
                        COUNT(sgm.enrollment_id) as current_size,
                        
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
                    WHERE sg.term_id = target_term_id
                    GROUP BY sg.id, sg.name, sg.day_of_week, sg.time_slot_id, sg.max_capacity, 
                             sg.target_skill_level, c.user_id, u.first_name, u.last_name
                ),
                
                -- Compatibility scoring
                compatibility_scores AS (
                    SELECT 
                        ga.*,
                        ts.start_time || ' - ' || ts.end_time as time_display,
                        CASE ga.day_of_week
                            WHEN 0 THEN 'Monday'
                            WHEN 1 THEN 'Tuesday' 
                            WHEN 2 THEN 'Wednesday'
                            WHEN 3 THEN 'Thursday'
                            WHEN 4 THEN 'Friday'
                            ELSE 'Unknown'
                        END as day_name,
                        
                        -- Comprehensive compatibility scoring (0-370 points)
                        (
                            -- Skill level compatibility (0-100)
                            CASE 
                                WHEN target_skill_level = ga.target_skill_level THEN 100
                                WHEN ABS(ASCII(target_skill_level) - ASCII(ga.target_skill_level)) = 1 THEN 60
                                ELSE 0
                            END +
                            
                            -- Group type compatibility (0-80)
                            CASE 
                                WHEN ga.effective_group_type = 'EMPTY' THEN 80
                                WHEN ga.effective_group_type = target_enrollment_type THEN 80
                                WHEN target_enrollment_type = 'GROUP' AND ga.effective_group_type = 'PAIR' THEN 40
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
                            CASE target_enrollment_type
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
                        ) as compatibility_score
                        
                    FROM group_analysis ga
                    JOIN scheduler_timeslot ts ON ga.time_slot_id = ts.id
                    JOIN student_availability sa ON sa.time_slot_id = ga.time_slot_id 
                                                 AND sa.day_num = ga.day_of_week
                ),
                
                -- Direct placement opportunities
                direct_placements AS (
                    SELECT 
                        cs.*,
                        'direct' as placement_type,
                        json_build_object(
                            'type', 'direct',
                            'explanation', 'Direct placement in available slot',
                            'complexity', 1
                        ) as displacement_info,
                        cs.compatibility_score as feasibility_score,
                        'Direct placement - ' || 
                        CASE 
                            WHEN cs.current_size = 0 THEN 'empty slot'
                            WHEN cs.effective_group_type = target_enrollment_type THEN 
                                'join ' || cs.current_size || ' compatible student(s)'
                            ELSE 'available space'
                        END as explanation
                    FROM compatibility_scores cs
                    WHERE cs.current_size < cs.max_capacity
                    AND (
                        cs.effective_group_type = 'EMPTY' OR
                        cs.effective_group_type = target_enrollment_type OR
                        (target_enrollment_type = 'GROUP' AND cs.effective_group_type = 'PAIR')
                    )
                ),
                
                -- Displacement opportunities (if enabled)
                displacement_opportunities AS (
                    SELECT 
                        cs.*,
                        'displacement' as placement_type,
                        json_build_object(
                            'type', 'displacement',
                            'displaced_students', cs.current_members,
                            'explanation', 'Displace ' || cs.current_size || ' student(s) to create optimal placement',
                            'complexity', cs.current_size + 1
                        ) as displacement_info,
                        -- Reduce feasibility score for displacements
                        GREATEST(cs.compatibility_score - (cs.current_size * 20), 0) as feasibility_score,
                        'Displacement - move ' || cs.current_size || ' student(s) to optimize placement' as explanation
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
                    ao.time_slot_id as slot_id,
                    ao.group_id,
                    ao.group_name,
                    ao.coach_name,
                    ao.day_name,
                    ao.time_display as time_slot,
                    ao.compatibility_score,
                    ao.placement_type,
                    ao.current_size,
                    ao.max_capacity,
                    ao.displacement_info,
                    ao.explanation,
                    ao.feasibility_score
                FROM all_opportunities ao
                ORDER BY 
                    -- Prioritize direct placements, then by feasibility score
                    CASE WHEN ao.placement_type = 'direct' THEN 0 ELSE 1 END,
                    ao.feasibility_score DESC,
                    ao.compatibility_score DESC
                LIMIT max_results;
                
            END;
            $$ LANGUAGE plpgsql;
            
            -- Create index for performance
            CREATE INDEX IF NOT EXISTS idx_scheduledgroup_term_day_time 
            ON scheduler_scheduledgroup(term_id, day_of_week, time_slot_id);
            
            CREATE INDEX IF NOT EXISTS idx_enrollment_student_term 
            ON scheduler_enrollment(student_id, term_id);
            """,
            reverse_sql="DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN);"
        ),
    ]
