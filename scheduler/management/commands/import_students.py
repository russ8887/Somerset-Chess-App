import csv
from django.core.management.base import BaseCommand, CommandError
from scheduler.models import Student, SchoolClass, Term, Enrollment

class Command(BaseCommand):
    help = 'Imports students and their enrollments from a CSV file.'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='The path to the CSV file to import.')
        parser.add_argument('term_name', type=str, help='The name of the term to enroll students in (e.g., "Term 3, 2025").')

    def handle(self, *args, **options):
        csv_file_path = options['csv_file']
        term_name = options['term_name']

        enrollment_type_map = {
            '1': 'SOLO',
            '2': 'PAIR',
            '3': 'GROUP',
        }

        try:
            term = Term.objects.get(name=term_name)
        except Term.DoesNotExist:
            raise CommandError(f'Term "{term_name}" does not exist. Please create it in the admin panel first.')

        self.stdout.write(self.style.SUCCESS(f'Found term: "{term.name}". Starting import...'))

        try:
            with open(csv_file_path, mode='r', encoding='utf-8-sig') as file: # Using utf-8-sig for better compatibility
                reader = csv.DictReader(file)
                for row in reader:
                    # Using .strip() to remove leading/trailing whitespace from CSV data
                    first_name = row['first_name'].strip()
                    last_name = row['last_name'].strip()
                    school_class_name = row['school_class'].strip()

                    if not first_name or not last_name:
                        self.stdout.write(self.style.WARNING(f"Skipping row due to missing name: {row}"))
                        continue

                    school_class, _ = SchoolClass.objects.get_or_create(
                        name=school_class_name
                    )

                    student, created = Student.objects.update_or_create(
                        first_name=first_name,
                        last_name=last_name,
                        defaults={
                            'year_level': row['year_level'],
                            'school_class': school_class,
                        }
                    )

                    enrollment_type_code = row['enrollment_type'].strip()
                    enrollment_type = enrollment_type_map.get(enrollment_type_code)

                    if not enrollment_type:
                        self.stdout.write(self.style.WARNING(f"Skipping enrollment for {student}: Invalid enrollment type code '{enrollment_type_code}'"))
                        continue

                    Enrollment.objects.get_or_create(
                        student=student,
                        term=term,
                        defaults={'enrollment_type': enrollment_type}
                    )

            self.stdout.write(self.style.SUCCESS('Successfully imported all students and enrollments.'))

        except FileNotFoundError:
            raise CommandError(f'File "{csv_file_path}" does not exist.')
        except Exception as e:
            raise CommandError(f'An error occurred: {e}')