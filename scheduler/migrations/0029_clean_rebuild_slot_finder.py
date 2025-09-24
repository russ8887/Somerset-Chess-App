from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0028_test_diagnostic_logging'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- STEP 1: Drop ALL existing versions of the function to start clean
            DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);
            DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN);
            DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER);
            DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER);
            
            -- STEP 2: Create a simple diagnostic function that returns actual data instead of using RAISE NOTICE
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
                available_slots_count INTEGER;
                scheduled_groups_count INTEGER;
                debug_info TEXT;
            BEGIN
                -- Get student details
                SELECT s.skill_level, e.enrollment_type 
                INTO student_skill_level, student_enrollment_type
                FROM scheduler_student s
                JOIN scheduler_enrollment e ON s.id = e.student_id
                WHERE s.id = target_student_id AND e.term_id = student_term_id;
                
                -- If no student data found, return diagnostic info
                IF student_skill_level IS NULL OR student_enrollment_type IS NULL THEN
                    RETURN QUERY
                    SELECT 
                        -1::BIGINT as slot_id,
                        -1::BIGINT as group_id,
                        'DEBUG: Student data not found'::VARCHAR(100) as group_name,
                        ('Student ID: ' || target_student_id || ', Term ID: ' || student_term_id)::VARCHAR(202) as coach_name,
                        'ERROR'::VARCHAR(10) as day_name,
                        'No student data'::VARCHAR(50) as time_slot,
                        0 as compatibility_score,
                        'debug'::VARCHAR(20) as placement_type,
                        0 as current_size,
                        0 as max_capacity,
                        json_build_object('error', 'student_not_found') as displacement_info,
                        'Student or enrollment not found in database'::VARCHAR(500) as explanation,
                        0 as feasibility_score;
                    RETURN;
                END IF;
                
                -- Count available time slots
                SELECT COUNT(*) INTO available_slots_count
                FROM (
                    SELECT DISTINCT ts.id as time_slot_id, day_num as day_of_week
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
                
                -- Count scheduled groups in term
                SELECT COUNT(*) INTO scheduled_groups_count
                FROM scheduler_scheduledgroup sg
                WHERE sg.term_id = student_term_id;
                
                -- Return diagnostic info first
                RETURN QUERY
                SELECT 
                    -2::BIGINT as slot_id,
                    -2::BIGINT as group_id,
                    'DEBUG: Diagnostic Info'::VARCHAR(100) as group_name,
                    ('Skill: ' || COALESCE(student_skill_level, 'NULL') || ', Type: ' || COALESCE(student_enrollment_type, 'NULL'))::VARCHAR(202) as coach_name,
                    'DEBUG'::VARCHAR(10) as day_name,
                    ('Slots: ' || available_slots_count || ', Groups: ' || scheduled_groups_count)::VARCHAR(50) as time_slot,
                    available_slots_count as compatibility_score,
                    'debug'::VARCHAR(20) as placement_type,
                    scheduled_groups_count as current_size,
                    0 as max_capacity,
                    json_build_object(
                        'student_skill_level', student_skill_level,
                        'student_enrollment_type', student_enrollment_type,
                        'available_slots_count', available_slots_count,
                        'scheduled_groups_count', scheduled_groups_count
                    ) as displacement_info,
                    'Diagnostic information about student and available options'::VARCHAR(500) as explanation,
                    0 as feasibility_score;
                
                -- If no available slots or no groups, return early
                IF available_slots_count = 0 THEN
                    RETURN QUERY
                    SELECT 
                        -3::BIGINT as slot_id,
                        -3::BIGINT as group_id,
                        'DEBUG: No Available Slots'::VARCHAR(100) as group_name,
                        'Student has no available time slots'::VARCHAR(202) as coach_name,
                        'ERROR'::VARCHAR(10) as day_name,
                        'Check availability'::VARCHAR(50) as time_slot,
                        0 as compatibility_score,
                        'debug'::VARCHAR(20) as placement_type,
                        0 as current_size,
                        0 as max_capacity,
                        json_build_object('error', 'no_available_slots') as displacement_info,
                        'Student has no available time slots - check unavailability settings'::VARCHAR(500) as explanation,
                        0 as feasibility_score;
                    RETURN;
                END IF;
                
                IF scheduled_groups_count = 0 THEN
                    RETURN QUERY
                    SELECT 
                        -4::BIGINT as slot_id,
                        -4::BIGINT as group_id,
                        'DEBUG: No Scheduled Groups'::VARCHAR(100) as group_name,
                        'No groups exist in this term'::VARCHAR(202) as coach_name,
                        'ERROR'::VARCHAR(10) as day_name,
                        'Create groups first'::VARCHAR(50) as time_slot,
                        0 as compatibility_score,
                        'debug'::VARCHAR(20) as placement_type,
                        0 as current_size,
                        0 as max_capacity,
                        json_build_object('error', 'no_scheduled_groups') as displacement_info,
                        'No scheduled groups exist in this term - create groups first'::VARCHAR(500) as explanation,
                        0 as feasibility_score;
                    RETURN;
                END IF;
                
                -- Now try to find actual slot recommendations
                RETURN QUERY
                WITH 
                -- Student availability analysis
                student_availability AS (
                    SELECT DISTINCT
                        ts.id as time_slot_id,
                        day_num as day_of_week
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
                ),
                
                -- Group analysis with current composition
                group_analysis AS (
                    SELECT 
                        sg.id as group_id,
                        sg.name as group_name,
                        sg.day_of_week,
                        sg.time_slot_id,
                        sg.max_capacity,
                        sg.target_skill_level,
                        (u.first_name || ' ' || u.last_name)::VARCHAR(202) as coach_name,
                        COUNT(sgm.enrollment_id)::INTEGER as current_size,
                        
                        -- Simple group type detection
                        CASE 
                            WHEN COUNT(sgm.enrollment_id) = 0 THEN 'EMPTY'
                            ELSE 'OCCUPIED'
                        END as effective_group_type
                        
                    FROM scheduler_scheduledgroup sg
                    JOIN scheduler_coach c ON sg.coach_id = c.id
                    JOIN auth_user u ON c.user_id = u.id
                    LEFT JOIN scheduler_scheduledgroup_members sgm ON sg.id = sgm.scheduledgroup_id
                    WHERE sg.term_id = student_term_id
                    GROUP BY sg.id, sg.name, sg.day_of_week, sg.time_slot_id, sg.max_capacity, 
                             sg.target_skill_level, u.first_name, u.last_name
                ),
                
                -- Available groups (groups that match student availability)
                available_groups AS (
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
                        
                        -- Simple compatibility scoring
                        (
                            CASE 
                                WHEN student_skill_level = ga.target_skill_level THEN 100
                                WHEN ABS(ASCII(student_skill_level) - ASCII(ga.target_skill_level)) = 1 THEN 60
                                ELSE 20
                            END +
                            CASE 
                                WHEN ga.current_size < ga.max_capacity THEN 50
                                ELSE 0
                            END
                        )::INTEGER as compatibility_score
                        
                    FROM group_analysis ga
                    JOIN scheduler_timeslot ts ON ga.time_slot_id = ts.id
                    JOIN student_availability sa ON sa.time_slot_id = ga.time_slot_id 
                                                 AND sa.day_of_week = ga.day_of_week
                    WHERE ga.current_size < ga.max_capacity  -- Only groups with space
                )
                
                -- Final results
                SELECT 
                    ag.time_slot_id::BIGINT as slot_id,
                    ag.group_id::BIGINT,
                    ag.group_name::VARCHAR(100),
                    ag.coach_name::VARCHAR(202),
                    ag.day_name::VARCHAR(10),
                    ag.time_display::VARCHAR(50) as time_slot,
                    ag.compatibility_score,
                    'direct'::VARCHAR(20) as placement_type,
                    ag.current_size,
                    ag.max_capacity,
                    json_build_object(
                        'type', 'direct',
                        'current_members', ag.current_size,
                        'available_space', ag.max_capacity - ag.current_size
                    ) as displacement_info,
                    ('Available slot - ' || ag.current_size || '/' || ag.max_capacity || ' students')::VARCHAR(500) as explanation,
                    ag.compatibility_score as feasibility_score
                FROM available_groups ag
                ORDER BY ag.compatibility_score DESC
                LIMIT max_results;
                
            END;
            $$;
            """,
            reverse_sql="DROP FUNCTION IF EXISTS find_optimal_slots_advanced(INTEGER, INTEGER, BOOLEAN, INTEGER);"
        ),
    ]
