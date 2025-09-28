from django.core.management.base import BaseCommand
from django.db import transaction
from scheduler.models import AttendanceRecord, Term, LessonNote

class Command(BaseCommand):
    help = 'Clear attendance records for the active term to fix roster sync issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Actually perform the deletion (required for safety)',
        )
        parser.add_argument(
            '--term-id',
            type=int,
            help='Specific term ID to clear (defaults to active term)',
        )

    def handle(self, *args, **options):
        if options['term_id']:
            try:
                term = Term.objects.get(id=options['term_id'])
                self.stdout.write(f"Using specified term: {term.name}")
            except Term.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'Term with ID {options["term_id"]} not found')
                )
                return
        else:
            term = Term.get_active_term()
            if not term:
                self.stdout.write(
                    self.style.ERROR('No active term found')
                )
                return
            self.stdout.write(f"Using active term: {term.name}")

        # Count records to be deleted
        attendance_records = AttendanceRecord.objects.filter(
            enrollment__term=term
        )
        lesson_notes = LessonNote.objects.filter(
            attendance_record__enrollment__term=term
        )
        
        attendance_count = attendance_records.count()
        notes_count = lesson_notes.count()
        
        self.stdout.write(f"Found {attendance_count} attendance records")
        self.stdout.write(f"Found {notes_count} lesson notes")
        
        if not options['confirm']:
            self.stdout.write(
                self.style.WARNING(
                    'This is a dry run. Use --confirm to actually delete the records.'
                )
            )
            self.stdout.write(
                f"Would delete {attendance_count} attendance records and {notes_count} lesson notes for term: {term.name}"
            )
            return

        # Perform the deletion in a transaction
        try:
            with transaction.atomic():
                # Delete lesson notes first (they depend on attendance records)
                deleted_notes = lesson_notes.delete()
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {deleted_notes[0]} lesson notes')
                )
                
                # Delete attendance records
                deleted_records = attendance_records.delete()
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {deleted_records[0]} attendance records')
                )
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Successfully cleared all attendance data for term: {term.name}'
                    )
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        'Rosters will now dynamically generate from current group membership!'
                    )
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error during deletion: {str(e)}')
            )
