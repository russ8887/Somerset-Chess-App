from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0027_diagnostic_slot_finder'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Create a simple test function to verify RAISE NOTICE works
            CREATE OR REPLACE FUNCTION test_diagnostic_logging()
            RETURNS TEXT
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE NOTICE 'TEST: Diagnostic logging is working!';
                RAISE NOTICE 'TEST: This should appear in the logs';
                RETURN 'Test function executed successfully';
            END;
            $$;
            
            -- Test the function immediately
            SELECT test_diagnostic_logging();
            
            -- Drop the test function
            DROP FUNCTION test_diagnostic_logging();
            """,
            reverse_sql="-- No reverse needed for test function"
        ),
    ]
