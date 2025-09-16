#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input
# Run database migrations to ensure schema is up to date
python manage.py migrate
