#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input
# Run database migrations to ensure schema is up to date
python manage.py migrate

# Note: Gunicorn will be started by Render using gunicorn.conf.py configuration
# which includes the 600-second timeout for slot finder operations
