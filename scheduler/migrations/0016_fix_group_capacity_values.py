# Generated migration to fix group capacity values based on group type

from django.db import migrations

def fix_group_capacities(apps, schema_editor):
    """Update existing ScheduledGroup records to have correct capacity based on group_type"""
    ScheduledGroup = apps.get_model('scheduler', 'ScheduledGroup')
    
    # Update SOLO groups
    ScheduledGroup.objects.filter(group_type='SOLO').update(
        max_capacity=1,
        preferred_size=1
    )
    
    # Update PAIR groups
    ScheduledGroup.objects.filter(group_type='PAIR').update(
        max_capacity=2,
        preferred_size=2
    )
    
    # Update GROUP groups
    ScheduledGroup.objects.filter(group_type='GROUP').update(
        max_capacity=3,
        preferred_size=3
    )

def reverse_fix_group_capacities(apps, schema_editor):
    """Reverse migration - set all groups back to default capacity of 4"""
    ScheduledGroup = apps.get_model('scheduler', 'ScheduledGroup')
    
    ScheduledGroup.objects.all().update(
        max_capacity=4,
        preferred_size=3
    )

class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0015_remove_redundant_preferred_group_size'),
    ]

    operations = [
        migrations.RunPython(
            fix_group_capacities,
            reverse_fix_group_capacities
        ),
    ]
